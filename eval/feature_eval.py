"""
功能增强量化评测（前后对比）
============================
对最近的工程增强做可量化的 Before/After 对比，让项目有说服力：

    1. 追问贴题率 — Before: 评估器 5 分类规则 vs After: FollowUpAgent 自主决策
    2. 参考答案质量 — Before: expected_points 关键词要点 vs After: RAG 面经
    3. 面经检索覆盖率 — 题库多少题能命中相关面经（RAG 数据源覆盖度）

Agent 开发常用指标速查（本脚本覆盖 ✓）:
    - 追问贴题率（相关性）    ✓
    - 参考答案完整性/要点覆盖率 ✓
    - 检索命中率/覆盖率       ✓
    - （另见 benchmark.py: 延迟/token/成本/调用次数；judge_eval.py: MAE/Pearson/一致性）

运行（全离线，Mock LLM）:
    python eval/feature_eval.py
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.mock_llm import MockLLMClient
from eval.dataset import EVAL_SAMPLES
from eval.judge_eval import follow_up_is_relevant
from interview.evaluator import AnswerEvaluator, FollowUpDecision
from interview.follow_up_agent import FollowUpAgent
from interview.qa_bank import QaRetriever, get_all_qa_entries
from interview.question_bank import QUESTION_BANK, InterviewQuestion, QuestionBankRetriever


def run(coro):
    return asyncio.run(coro)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9]+|[一-鿿]{2,}", text.lower()))


def _bank_to_question(bq) -> InterviewQuestion:
    return InterviewQuestion(
        id=bq.id, type=bq.type, category=bq.category, question=bq.question,
        expected_points=bq.expected_points, difficulty=bq.difficulty,
        follow_up_hints=bq.follow_up_hints, source="bank", code=bq.code,
    )


# ═════════════════ 1. 追问贴题率（Before/After） ═════════════════

async def eval_follow_up_quality(llm) -> dict:
    """评估器 5 分类规则 vs FollowUpAgent 自主决策的追问贴题率"""
    evaluator = AnswerEvaluator(llm)
    agent = FollowUpAgent(llm)
    retriever = QuestionBankRetriever(QUESTION_BANK)

    before_total = before_relevant = 0
    after_total = after_relevant = 0
    agent_continue = 0
    samples = 0

    for qid, quality, label_score, answer in EVAL_SAMPLES:
        bq = retriever.get_by_id(qid)
        if bq is None:
            continue
        q = _bank_to_question(bq)
        samples += 1

        ev = await evaluator.evaluate(q, answer)

        # Before：评估器 5 分类规则的追问
        if ev.follow_up_decision != FollowUpDecision.MOVE_ON and ev.follow_up_question:
            before_total += 1
            if follow_up_is_relevant(q.question, q.expected_points, answer, ev.follow_up_question):
                before_relevant += 1

        # After：FollowUpAgent 自主决策
        decision = await agent.decide(q, answer, ev, [])
        if decision["continue_follow_up"]:
            agent_continue += 1
            if decision["question"]:
                after_total += 1
                if follow_up_is_relevant(q.question, q.expected_points, answer, decision["question"]):
                    after_relevant += 1

    def rate(r, t):
        return round(r / t * 100, 1) if t else 0.0

    return {
        "samples": samples,
        "before": {"total": before_total, "relevant": before_relevant,
                   "rate": rate(before_relevant, before_total)},
        "after": {"total": after_total, "relevant": after_relevant,
                  "rate": rate(after_relevant, after_total)},
        "agent_continue_ratio": round(agent_continue / samples * 100, 1),
    }


# ═════════════════ 2. 参考答案质量（Before/After） ═════════════════

def _info_density(answer: str) -> int:
    """信息密度：答案中的技术关键词数量（英文字母数字词 + 中文词）"""
    return len(_tokens(answer))


def eval_reference_quality() -> dict:
    """expected_points 关键词要点 vs RAG 面经 的参考答案质量（全量数据源：内置 + Knowledge 知识库）"""
    qa = QaRetriever(get_all_qa_entries())

    before_lens, after_lens = [], []
    before_density, after_density = [], []
    rag_hits = 0
    total = 0

    for bq in QUESTION_BANK:
        before_ans = "答题要点：" + "、".join(bq.expected_points) + "。"
        # 检索 query 拼技能标签（与面经 tags 对齐）
        search = bq.question + " " + " ".join(bq.tags)
        hits = qa.retrieve(search, top_k=1)
        after_ans = hits[0]["answer"] if hits else before_ans

        before_lens.append(len(before_ans))
        after_lens.append(len(after_ans))
        before_density.append(_info_density(before_ans))
        after_density.append(_info_density(after_ans))
        total += 1
        if hits:
            rag_hits += 1

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else 0.0

    def pct(n):
        return round(n / total * 100, 1) if total else 0.0

    return {
        "total_questions": total,
        "before": {"avg_len": avg(before_lens), "avg_density": avg(before_density)},
        "after": {"avg_len": avg(after_lens), "avg_density": avg(after_density)},
        "rag_hit_rate": pct(rag_hits),
    }


# ═════════════════ 3. 面经检索覆盖率 ═════════════════

def eval_retrieval_coverage() -> dict:
    """题库题目能命中相关面经的比例（RAG 数据源覆盖度，含 Knowledge 知识库）"""
    qa = QaRetriever(get_all_qa_entries())
    hit = 0
    for bq in QUESTION_BANK:
        search = bq.question + " " + " ".join(bq.tags)
        if qa.retrieve(search, top_k=1):
            hit += 1
    return {
        "hit": hit,
        "total": len(QUESTION_BANK),
        "rate": round(hit / len(QUESTION_BANK) * 100, 1),
    }


# ═════════════════ 输出 ═════════════════

def render(report: dict) -> str:
    fu = report["follow_up"]
    ref = report["reference"]
    cov = report["retrieval"]

    lines = [
        "=" * 62,
        "  功能增强量化评测（Before / After 对比）",
        "=" * 62,
        "",
        "【1】追问贴题率（30 样本评测集）",
        f"   Before 评估器5分类规则 : {fu['before']['relevant']}/{fu['before']['total']} = {fu['before']['rate']}%",
        f"   After  FollowUpAgent    : {fu['after']['relevant']}/{fu['after']['total']} = {fu['after']['rate']}%",
        f"   Agent 自主决定继续追问的比例: {fu['agent_continue_ratio']}%",
        "   （Before 为 Mock 评估器通用话术如\"能展开说说吗\"；真实 LLM 评估路径的贴题率见 judge_eval.py=100%）",
        "",
        f"【2】参考答案质量（题库 {ref['total_questions']} 题）",
        f"   平均答案长度   : {ref['before']['avg_len']} 字 → {ref['after']['avg_len']} 字",
        f"   信息密度(关键词): {ref['before']['avg_density']} → {ref['after']['avg_density']}",
        f"   RAG 检索命中率 : {ref['rag_hit_rate']}%（面经库覆盖高频考点比例）",
        "",
        "【3】面经检索覆盖率（RAG 数据源覆盖度）",
        f"   题库命中相关面经: {cov['hit']}/{cov['total']} = {cov['rate']}%",
        "",
        "注: 全部离线可复现（Mock LLM + 规则判定），真实 LLM 评测可接 judge_eval.py --real。",
        "=" * 62,
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="功能增强前后对比评测")
    parser.add_argument("--quiet", action="store_true", help="只打印结论行")
    parser.parse_args()

    llm = MockLLMClient()

    print("⏳ 运行中（追问评测需逐样本评估，约几秒）...", file=sys.stderr)
    report = {
        "follow_up": run(eval_follow_up_quality(llm)),
        "reference": eval_reference_quality(),
        "retrieval": eval_retrieval_coverage(),
    }
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
