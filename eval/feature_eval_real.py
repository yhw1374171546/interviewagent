"""
B 组: 真实 LLM 证据链评测
========================
用真实 DeepSeek 跑 feature_eval 的「追问贴题率」部分，产出真实数字。

背景: 简历/报告里「追问贴题率 100%」是 Mock 模式测的——真实 DeepSeek 下
FollowUpAgent 的追问是否仍贴题，此前没有数据。本脚本补上真实证据。

用法（需 .env 真实 key）:
    python eval/feature_eval_real.py            # 真实 30 样本追问评测
    python eval/feature_eval_real.py --mock     # Mock 对照（验证框架）

输出: 控制台 + logs/feature_eval_real.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.mock_llm import MockLLMClient
from eval.dataset import EVAL_SAMPLES
from eval.feature_eval import _bank_to_question, eval_reference_quality, eval_retrieval_coverage
from eval.judge_eval import follow_up_is_relevant
from interview.evaluator import AnswerEvaluator
from interview.follow_up_agent import FollowUpAgent
from interview.question_bank import QUESTION_BANK, QuestionBankRetriever


async def eval_follow_up_real(llm) -> dict:
    """真实 LLM 下 FollowUpAgent 的追问贴题率（30 样本）"""
    evaluator = AnswerEvaluator(llm)
    agent = FollowUpAgent(llm)
    retriever = QuestionBankRetriever(QUESTION_BANK)

    total = relevant = continue_count = 0
    samples_detail = []

    for qid, quality, label_score, answer in EVAL_SAMPLES:
        bq = retriever.get_by_id(qid)
        if bq is None:
            continue
        q = _bank_to_question(bq)
        ev = await evaluator.evaluate(q, answer)
        decision = await agent.decide(q, answer, ev, [])
        fu = decision["question"] if decision["continue_follow_up"] else ""

        is_rel = (
            follow_up_is_relevant(q.question, q.expected_points, answer, fu)
            if fu else False
        )
        if fu:
            total += 1
            if is_rel:
                relevant += 1
        if decision["continue_follow_up"]:
            continue_count += 1

        samples_detail.append({
            "qid": qid, "quality": quality,
            "follow_up": fu[:60], "relevant": is_rel,
        })

    rate = round(relevant / total * 100, 1) if total else 0.0
    return {
        "samples": len(EVAL_SAMPLES),
        "non_empty_follow_up": total,
        "relevant": relevant,
        "rate": rate,
        "agent_continue_ratio": round(continue_count / len(EVAL_SAMPLES) * 100, 1),
        "detail": samples_detail,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="B 组真实证据链评测")
    parser.add_argument("--mock", action="store_true", help="Mock 对照")
    args = parser.parse_args()

    if args.mock:
        llm = MockLLMClient()
        mode = "mock"
    else:
        from config import settings
        from core.llm import OpenAIClient
        llm = OpenAIClient(
            model=settings.llm_fast_model or settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        mode = "real"

    print(f"⏳ 真实追问评测运行中（{mode}，30 样本）...", file=sys.stderr)
    t0 = time.time()
    follow_up = await eval_follow_up_real(llm)
    elapsed = round(time.time() - t0, 1)

    reference = eval_reference_quality()   # 确定性（RAG 数据源），不依赖 LLM
    retrieval = eval_retrieval_coverage()  # 确定性

    print("=" * 62)
    print(f"  B 组真实证据链评测（{mode}）| 耗时 {elapsed}s")
    print("=" * 62)
    print("【1】FollowUpAgent 追问贴题率（30 样本）")
    print(f"    非空追问: {follow_up['non_empty_follow_up']} 条, "
          f"贴题 {follow_up['relevant']} 条 = {follow_up['rate']}%")
    print(f"    Agent 决定继续追问比例: {follow_up['agent_continue_ratio']}%")
    print(f"【2】参考答案质量（确定性，题库 {reference['total_questions']} 题）")
    print(f"    长度 {reference['before']['avg_len']}→{reference['after']['avg_len']} 字 | "
          f"密度 {reference['before']['avg_density']}→{reference['after']['avg_density']} | "
          f"RAG 命中 {reference['rag_hit_rate']}%")
    print(f"【3】面经检索覆盖率: {retrieval['rate']}% ({retrieval['hit']}/{retrieval['total']})")
    print("=" * 62)

    out = Path(__file__).parent.parent / "logs" / "feature_eval_real.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "mode": mode, "elapsed_sec": elapsed,
        "follow_up": follow_up, "reference": reference, "retrieval": retrieval,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
