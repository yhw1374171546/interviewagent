"""
自适应难度测试（D2）
===================
覆盖: 难度计算规则（升级/降级/钳制/第一题不动）、候选池构建、替换逻辑、
Interviewer 集成（连续答好 → 难度上升、连续答差 → 难度下降）。

全离线（确定性规则 + Mock LLM），CI 可直接运行。
"""

import asyncio

from core.mock_llm import MockLLMClient
from interview.adaptive import (
    build_candidate_pool,
    compute_target_difficulty,
    pick_replacement,
)
from interview.interviewer import Interviewer
from interview.question_bank import (
    QUESTION_BANK,
    BankQuestion,
    QuestionType,
)

# ── 难度计算规则 ────────────────────────────────────────────────

class TestComputeTargetDifficulty:

    def test_first_question_no_adjust(self):
        assert compute_target_difficulty([], 3) == 3

    def test_high_avg_upgrades(self):
        assert compute_target_difficulty([8.5, 9.0], 3) == 4

    def test_single_high_score_no_upgrade(self):
        """单题高分不升级（需要 streak 连续高分）"""
        assert compute_target_difficulty([9.0], 3) == 3

    def test_low_avg_downgrades(self):
        assert compute_target_difficulty([4.0, 4.5], 3) == 2

    def test_difficulty_clamped(self):
        assert compute_target_difficulty([9.0, 9.0], 5) == 5
        assert compute_target_difficulty([2.0, 3.0], 1) == 1

    def test_mid_avg_keeps(self):
        assert compute_target_difficulty([6.5, 7.0], 3) == 3

    def test_upgrade_needs_recent_streak(self):
        """平均高但最近一题低分 → 不升级"""
        assert compute_target_difficulty([9.0, 9.0, 4.0], 3) == 3


# ── 候选池与替换 ────────────────────────────────────────────────

class TestCandidatePool:

    def test_pool_filters_by_type_and_skill(self):
        pool = build_candidate_pool(QUESTION_BANK, QuestionType.TECHNICAL, ["python"])
        assert len(pool) > 0
        assert all(q.type == QuestionType.TECHNICAL for q in pool)
        # 技能重叠的题应排在前面（排序偏好，不作硬过滤）
        assert pool[0].tags  # 首位应是带标签的题

    def test_pool_without_skill_keeps_type(self):
        pool = build_candidate_pool(QUESTION_BANK, QuestionType.BEHAVIORAL, [])
        assert len(pool) > 0
        assert all(q.type == QuestionType.BEHAVIORAL for q in pool)

    def test_pool_never_empty_when_type_exists(self):
        """同类型题存在时候选池绝不为空（技能硬过滤已移除）"""
        for qtype in QuestionType:
            pool = build_candidate_pool(QUESTION_BANK, qtype, ["python", "redis"])
            if any(q.type == qtype for q in QUESTION_BANK):
                assert len(pool) > 0, f"{qtype} 候选池不应为空"

    def test_pick_replacement_same_difficulty_returns_none(self):
        q = BankQuestion(
            id="T1", type=QuestionType.TECHNICAL, category="x",
            question="q", expected_points=[], difficulty=3, tags=["python"],
        )
        assert pick_replacement(q, 3, [q]) is None

    def test_pick_replacement_changes_difficulty(self):
        base = BankQuestion(
            id="T1", type=QuestionType.TECHNICAL, category="x",
            question="q", expected_points=[], difficulty=3, tags=["python"],
        )
        harder = BankQuestion(
            id="T2", type=QuestionType.TECHNICAL, category="x",
            question="harder", expected_points=[], difficulty=4, tags=["python"],
        )
        result = pick_replacement(base, 4, [harder, base], exclude_ids=set())
        assert result is not None and result.id == "T2"

    def test_pick_replacement_respects_exclude(self):
        base = BankQuestion(
            id="T1", type=QuestionType.TECHNICAL, category="x",
            question="q", expected_points=[], difficulty=3, tags=["python"],
        )
        harder = BankQuestion(
            id="T2", type=QuestionType.TECHNICAL, category="x",
            question="harder", expected_points=[], difficulty=4, tags=["python"],
        )
        result = pick_replacement(base, 4, [harder], exclude_ids={"T2"})
        assert result is None or result.id != "T2"

    def test_pick_replacement_no_exact_returns_none(self):
        """找不到目标难度 → 返回 None（保持原题，不将就）"""
        base = BankQuestion(
            id="T1", type=QuestionType.TECHNICAL, category="x",
            question="q", expected_points=[], difficulty=3, tags=["python"],
        )
        # 候选里只有难度 5，目标 4 → 无精确匹配 → None
        far = BankQuestion(
            id="T3", type=QuestionType.TECHNICAL, category="x",
            question="far", expected_points=[], difficulty=5, tags=["python"],
        )
        assert pick_replacement(base, 4, [far], exclude_ids=set()) is None


# ── Interviewer 集成（Mock LLM）────────────────────────────────

JD = """
Python 后端开发工程师
要求: 精通 Python，熟悉 FastAPI，了解 Redis 和 MySQL
"""

HIGH_ANSWER = (
    "GIL 定义：全局解释器锁，同一时刻只有一个线程执行 Python 字节码。"
    "CPU 密集任务会被 GIL 卡住无法利用多核，而 IO 密集型任务因为等待时释放"
    "GIL，影响较小。绕过方案：一是 multiprocessing 多进程，每个进程独立 GIL；"
    "二是用 C 扩展释放 GIL；三是 asyncio 异步编程处理 IO 密集。"
    "我在项目里用 celery 多进程 worker 绕过 GIL，效果明显。"
)

LOW_ANSWER = "不会。"


def run(coro):
    return asyncio.run(coro)


class TestInterviewerAdaptive:

    def _inject_answers(self, iv: Interviewer, count: int, score: float) -> None:
        """直接注入 n 条指定得分的已答记录（绕过 mock 评分不确定性，专注测自适应逻辑）"""
        from interview.evaluator import EvaluationResult

        for _ in range(count):
            q = iv.state.current_question
            iv.state.answers.append({
                "question": q,
                "answer": "test",
                "evaluation": EvaluationResult(
                    correctness=score, depth=score, structure=score,
                    relevance=score, overall_comment="test",
                ),
            })

    def test_good_performance_upgrades_difficulty(self):
        """连续答好（高分已答记录）→ 触发难度升级（且留痕）"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=5, adaptive_enabled=True)
            await iv.start(JD)
            # 注入 2 条高分记录（满足 avg>=8 + streak=2 升级条件）
            self._inject_answers(iv, 2, score=9.0)
            # 取下一题 → 应触发升级
            turn = await iv.next_question()
            return iv, turn

        iv, turn = run(scenario())
        assert len(iv.state.adaptive_adjustments) > 0, "连续答好应触发难度调整"
        for adj in iv.state.adaptive_adjustments:
            assert adj["to_difficulty"] > adj["from_difficulty"], f"应升级: {adj}"

    def test_poor_performance_downgrades_difficulty(self):
        """连续答差（低分已答记录）→ 触发难度降级"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=5, adaptive_enabled=True)
            await iv.start(JD)
            self._inject_answers(iv, 2, score=4.0)
            turn = await iv.next_question()
            return iv, turn

        iv, turn = run(scenario())
        assert len(iv.state.adaptive_adjustments) > 0, "连续答差应触发难度调整"
        for adj in iv.state.adaptive_adjustments:
            assert adj["to_difficulty"] < adj["from_difficulty"], f"应降级: {adj}"

    def test_adaptive_disabled_no_changes(self):
        """默认关闭时（adaptive_enabled=False）绝不调整难度"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=4)
            await iv.start(JD)
            self._inject_answers(iv, 2, score=9.0)
            turn = await iv.next_question()
            return iv, turn

        iv, turn = run(scenario())
        assert iv.state.adaptive_adjustments == []

    def test_first_question_never_adjusted(self):
        """第一题（无已答记录）不调整"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=4, adaptive_enabled=True)
            await iv.start(JD)
            turn = await iv.next_question()
            return turn, iv

        turn, iv = run(scenario())
        assert iv.state.adaptive_adjustments == []
        assert turn.question.difficulty == iv.state.plan.questions[0].difficulty
