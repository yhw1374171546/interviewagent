"""
评估器健壮性测试
================
覆盖「异常得分测试用例矩阵」的全部边界场景（A/B/C/D 四类）。

测试策略:
    - 确定性层测试用 AnswerEvaluator(llm=None) — 0 API 调用
    - LLM 异常场景用 FakeLLM 注入（返回非法枚举/抛异常）验证降级
    - 全链路用 MockLLMClient

运行:
    pytest tests/test_evaluator_robustness.py -v
"""

import asyncio

from core.llm import LLMClient, LLMResponse
from core.mock_llm import MockLLMClient
from interview.evaluator import (
    AnswerEvaluator,
    FollowUpDecision,
    _default_follow_up,
    _safe_decision,
)
from interview.question_bank import InterviewQuestion, QuestionType

# ── 测试题目 ────────────────────────────────────────────────────

GO_GC_QUESTION = InterviewQuestion(
    id="GO003",
    type=QuestionType.TECHNICAL,
    category="Go语言",
    question="Go 的 GC 是如何工作的？它经历了哪些演进（从 STW 到并发三色标记）？什么情况下 GC 会成为瓶颈？",
    expected_points=["三色标记", "写屏障", "混合写屏障", "GC触发条件", "GC调优"],
    difficulty=5,
)

NO_POINTS_QUESTION = InterviewQuestion(
    id="LLM001",
    type=QuestionType.TECHNICAL,
    category="综合",
    question="请谈谈你对高并发系统的理解。",
    expected_points=[],  # LLM 补充生成的题可能缺这个字段
    difficulty=3,
)


def run(coro):
    """pytest 无 pytest-asyncio 环境下的 asyncio 运行器"""
    return asyncio.run(coro)


# ═══════════════ A 类: 输入侧异常 ═══════════════

class TestInputEdgeCases:
    """A1-A6: answer 本身的异常"""

    def test_a1_empty_answer(self):
        ev = run(AnswerEvaluator(None).evaluate(GO_GC_QUESTION, "   "))
        assert ev.total_score == 1.0
        assert "未回答" in ev.overall_comment

    def test_a2_short_answer(self):
        ev = run(AnswerEvaluator(None).evaluate(GO_GC_QUESTION, "三色标记"))
        assert ev.total_score <= 3.0
        assert ev.follow_up_decision == FollowUpDecision.DEEPEN

    def test_a3_repeated_char_spam(self):
        ev = run(AnswerEvaluator(None).evaluate(GO_GC_QUESTION, "GOGOGOGOGOGOGOGOGOGOGO"))
        assert ev.correctness == 1 and ev.depth == 1
        assert "无效" in ev.overall_comment

    def test_a4_question_restate(self):
        """复读题目 — 相关性不能被题面词虚高"""
        ev = run(AnswerEvaluator(None).evaluate(
            GO_GC_QUESTION,
            "Go 的 GC 是如何工作的？它经历了哪些演进，从 STW 到并发三色标记，"
            "什么情况下 GC 会成为瓶颈呢？",
        ))
        assert ev.correctness <= 3, f"复读题目正确性应封顶: {ev.correctness}"
        assert ev.relevance <= 4, f"复读题目相关性应封顶: {ev.relevance}"
        assert "复读" in ev.overall_comment

    def test_a5_keyword_stuffing(self):
        """只罗列关键词不加解释 — 正确性不能拿满分"""
        ev = run(AnswerEvaluator(None).evaluate(
            GO_GC_QUESTION,
            "三色标记 写屏障 混合写屏障 GC触发条件 GC调优",  # 49 字全命中
        ))
        assert ev.correctness <= 6, f"关键词堆砌正确性应封顶: {ev.correctness}"
        assert any("关键词" in w or "展开" in w for w in ev.weaknesses)

    def test_a6_padded_repetition(self):
        """同一句话重复 N 遍凑字数 — 结构分应封顶"""
        sentence = "Go 的 GC 使用并发三色标记算法进行垃圾回收。"
        ev = run(AnswerEvaluator(None).evaluate(GO_GC_QUESTION, sentence * 5))
        assert ev.structure <= 4, f"灌水回答结构分应封顶: {ev.structure}"
        assert any("凑字数" in w for w in ev.weaknesses)

    def test_a7_long_answer_not_crashing(self):
        """超长回答不崩溃（LLM 内部截断）"""
        long_answer = "三色标记和写屏障是 Go GC 的核心机制。" * 200  # 4000 字
        ev = run(AnswerEvaluator(MockLLMClient()).evaluate(GO_GC_QUESTION, long_answer))
        assert 1 <= ev.total_score <= 10


# ═══════════════ B 类: 题目数据异常 ═══════════════

class TestQuestionEdgeCases:
    """B1: expected_points 缺失"""

    def test_b1_empty_expected_points_neutral_score(self):
        """无期望要点 → 正确性取中性值 5，不能恒 9 分"""
        ev = run(AnswerEvaluator(None).evaluate(
            NO_POINTS_QUESTION,
            "高并发系统需要做好限流、降级、熔断，利用缓存减少数据库压力。",
        ))
        assert ev.correctness == 5, f"无要点时正确性应为中性 5: {ev.correctness}"


# ═══════════════ C 类: LLM 输出异常 ═══════════════

class _FakeBadDecisionLLM(LLMClient):
    """返回非法 follow_up_decision 的 LLM（模拟幻觉）"""

    def __init__(self):
        super().__init__(model="fake-bad-decision")

    async def chat(self, messages, **kwargs):
        return LLMResponse(content='{"depth_level": "深入", "structure_level": "清晰", '
                                    '"overall_comment": "ok", "follow_up_decision": "skip_bad_value", '
                                    '"follow_up_question": "", "strengths": [], "weaknesses": []}')


class _FakeCrashLLM(LLMClient):
    """调用即抛异常的 LLM（模拟非瞬态故障，不可重试 → 直接走降级）"""

    def __init__(self):
        super().__init__(model="fake-crash")

    async def chat(self, messages, **kwargs):
        raise RuntimeError("simulated model failure")


class _FlakyLLM(LLMClient):
    """
    前 N 次调用抛限流异常、之后恢复正常的 LLM — 验证重试链路真正生效。
    """

    def __init__(self, fail_times: int = 1):
        super().__init__(model="fake-flaky")
        self.fail_times = fail_times
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("RateLimitError: simulated 429 timeout")
        return LLMResponse(
            content='{"depth_level": "深入", "structure_level": "清晰", '
                    '"overall_comment": "ok", "follow_up_decision": "move_on", '
                    '"follow_up_question": "", "strengths": [], "weaknesses": []}',
        )


class TestLLMOutputEdgeCases:

    def test_c1_invalid_follow_up_decision_no_crash(self):
        """非法枚举值 → 安全回退 move_on，不抛 ValueError"""
        ev = run(AnswerEvaluator(_FakeBadDecisionLLM()).evaluate(
            GO_GC_QUESTION, "三色标记 写屏障 混合写屏障 触发条件 调优手段" * 3,
        ))
        assert ev.follow_up_decision == FollowUpDecision.MOVE_ON

    def test_c1b_safe_decision_helper(self):
        assert _safe_decision("deepen") == FollowUpDecision.DEEPEN
        assert _safe_decision("unknown_garbage") == FollowUpDecision.MOVE_ON
        assert _safe_decision(None) == FollowUpDecision.MOVE_ON

    def test_c2_unknown_levels_default(self):
        """非法 depth/structure level → 默认值不崩溃"""
        ev = run(AnswerEvaluator(_FakeBadDecisionLLM()).evaluate(
            GO_GC_QUESTION, "三色标记与写屏障。" * 4,
        ))
        assert 1 <= ev.depth <= 10

    def test_c4_llm_crash_fallback_comment(self):
        """LLM 异常 → 降级评分 + 明确评语（不是空气泡）"""
        ev = run(AnswerEvaluator(_FakeCrashLLM()).evaluate(
            GO_GC_QUESTION, "Go 的 GC 使用三色标记，通过写屏障保证并发安全。" * 2,
        ))
        assert ev.overall_comment, "LLM 失败时评语不能为空"
        assert "语义评估" in ev.overall_comment or "规则" in ev.overall_comment

    def test_c5_retry_recovers_from_rate_limit(self):
        """重试链路生效: 限流异常 → 自动重试 → 恢复成功（不是直接失败）"""
        flaky = _FlakyLLM(fail_times=1)
        ev = run(AnswerEvaluator(flaky).evaluate(
            GO_GC_QUESTION, "三色标记 写屏障 混合写屏障 触发条件 调优手段" * 3,
        ))
        assert flaky.calls == 2, f"应该重试 1 次（共 2 次调用），实际 {flaky.calls}"
        assert ev.follow_up_decision == FollowUpDecision.MOVE_ON
        assert "语义评估" not in ev.overall_comment  # 重试成功后走正常路径


# ═══════════════ D 类: 追问异常 ═══════════════

class _FakeEmptyFollowUpLLM(LLMClient):
    """decision=deepen 但追问文本为空的 LLM"""

    def __init__(self):
        super().__init__(model="fake-empty-followup")

    async def chat(self, messages, **kwargs):
        return LLMResponse(content='{"depth_level": "较浅", "structure_level": "一般", '
                                    '"overall_comment": "回答不完整", "follow_up_decision": "deepen", '
                                    '"follow_up_question": "", "strengths": [], "weaknesses": []}')


class TestFollowUpEdgeCases:

    def test_d1_empty_follow_up_text_gets_contextual_default(self):
        """追问文本为空 → 优先用未命中要点生成上下文追问（贴合题目而非通用话术）"""
        ev = run(AnswerEvaluator(_FakeEmptyFollowUpLLM()).evaluate(
            GO_GC_QUESTION, "Go 的 GC 主要使用了三色标记算法。",
        ))
        assert ev.follow_up_decision == FollowUpDecision.DEEPEN
        assert ev.follow_up_question, "追问文本不能为空"
        # 回答只命中了「三色标记」，未命中要点应出现在追问中（上下文追问）
        assert "写屏障" in ev.follow_up_question or "混合写屏障" in ev.follow_up_question

    def test_d2_move_on_has_no_follow_up(self):
        assert _default_follow_up(FollowUpDecision.MOVE_ON) == ""


# ═══════════════ 全链路（Mock 模式） ═══════════════

class TestFullPipeline:
    """此前修过的场景回归测试 — 防止回退"""

    def test_spam_vs_irrelevant_scores_differ(self):
        """两种不同质量的回答必须得到不同且合理的分数"""
        async def scenario():
            evaluator = AnswerEvaluator(MockLLMClient())
            spam = await evaluator.evaluate(GO_GC_QUESTION, "GOGOGOGOGOGOGOGOGOGOGO")
            irrelevant = await evaluator.evaluate(
                GO_GC_QUESTION,
                "RAG 就是检索增强生成，先把文档切分成小段落，用嵌入模型转成向量存进"
                "向量数据库，查询时检索最相似的段落拼给大模型生成答案。" * 2,
            )
            return spam, irrelevant

        spam, irrelevant = run(scenario())
        assert spam.depth <= 3
        assert irrelevant.depth < 8, "无关长文不能拿高深度分"
        assert spam.depth != irrelevant.depth
        assert spam.total_score != irrelevant.total_score


# ═══════════════ 编程题沙箱判题（阶段：code_judge 接入） ═══════════════

CODING_QUESTION = InterviewQuestion(
    id="COD001",
    type=QuestionType.CODING,
    category="数据结构",
    question="实现 LRU 缓存",
    expected_points=["哈希表+双向链表"],
    difficulty=3,
    code={
        "language": "python",
        "function_signature": "class LRUCache:\n    def __init__(self, capacity: int): ...",
        "test_cases": [
            {"name": "基本", "input_code": "cache = LRUCache(1)\ncache.put(1, 1)\nprint(cache.get(1))", "expected": "1"},
            {"name": "淘汰", "input_code": "cache = LRUCache(1)\ncache.put(1, 1)\ncache.put(2, 2)\nprint(cache.get(1))\nprint(cache.get(2))", "expected": "-1\n2"},
        ],
    },
)

CORRECT_CODE = "from collections import OrderedDict\nclass LRUCache:\n    def __init__(self, capacity):\n        self.c = capacity\n        self.d = OrderedDict()\n    def get(self, key):\n        if key not in self.d: return -1\n        self.d.move_to_end(key)\n        return self.d[key]\n    def put(self, key, value):\n        if key in self.d: self.d.move_to_end(key)\n        self.d[key] = value\n        if len(self.d) > self.c: self.d.popitem(last=False)\n"


class TestCodeJudgeInEvaluator:
    """coding 题走沙箱判题，pass/fail 覆盖正确性，结果透出到 EvaluationResult"""

    def test_correct_code_gets_full_correctness(self):
        ev = run(AnswerEvaluator(MockLLMClient()).evaluate(CODING_QUESTION, CORRECT_CODE))
        assert ev.code_judge is not None
        assert ev.code_judge["passed"] is True
        assert ev.correctness == 10

    def test_wrong_code_gets_low_correctness(self):
        bad = "class LRUCache:\n    def __init__(self, c): self.d = {}\n    def get(self, k): return -1\n    def put(self, k, v): self.d[k] = v\n"
        ev = run(AnswerEvaluator(MockLLMClient()).evaluate(CODING_QUESTION, bad))
        assert ev.code_judge is not None
        assert ev.code_judge["passed"] is False
        assert ev.correctness < 10
        assert ev.follow_up_decision == FollowUpDecision.DEEPEN

    def test_code_judge_not_triggered_for_non_coding(self):
        ev = run(AnswerEvaluator(MockLLMClient()).evaluate(
            GO_GC_QUESTION, "三色标记 写屏障 混合写屏障 GC触发条件 GC调优" * 2,
        ))
        assert ev.code_judge is None
