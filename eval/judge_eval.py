"""
LLM-as-judge 评测框架
=====================
用量化指标回答「你的评估器到底好不好」:

    1. 评分一致性 — 同一回答评 N 次，分数标准差越小越稳定
       （LLM 评委的经典问题: 同题同答每次得分不同）
    2. 评分准确性 — LLM 评分 vs 人工标注分数的 MAE / Pearson 相关
    3. 追问质量 — 追问是否贴合题目（与题目/要点/回答的关键词呼应率）

数据集: eval/dataset.py — 10 题 × 高/中/低 三档回答，人工标注分数

运行:
    python eval/judge_eval.py --mock            # 离线跑通框架（0 API 调用）
    python eval/judge_eval.py --limit 3         # 真实 API pilot: 前 3 题
    python eval/judge_eval.py --repeat 3        # 一致性: 每样本评 3 次
    python eval/judge_eval.py                   # 完整: 10 题 × 3 档 × 3 次

输出: docs/eval_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.mock_llm import MockLLMClient
from eval.dataset import EVAL_SAMPLES
from interview.evaluator import AnswerEvaluator
from interview.follow_up_agent import FollowUpAgent
from interview.question_bank import QUESTION_BANK, QuestionBankRetriever
from utils.logger import get_logger

logger = get_logger(__name__)


# ── 数据准备 ───────────────────────────────────────────────────

def load_samples(limit: int | None = None) -> list[dict]:
    """dataset → 结构化样本（含 InterviewQuestion 对象）"""
    retriever = QuestionBankRetriever(QUESTION_BANK)
    samples = []
    for qid, quality, label_score, answer in EVAL_SAMPLES:
        bank_q = retriever.get_by_id(qid)
        if bank_q is None:
            logger.warning(f"题库中找不到 {qid}，跳过")
            continue
        samples.append({
            "question_id": qid,
            "question": bank_q,  # BankQuestion 自带 expected_points
            "quality": quality,
            "label_score": label_score,
            "answer": answer,
        })
    if limit:
        # 按题分组取前 limit 题
        seen, out = set(), []
        for s in samples:
            if s["question_id"] not in seen:
                seen.add(s["question_id"])
            if len(seen) > limit:
                break
            out.append(s)
        return out
    return samples


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9]+|[一-鿿]{2,}", text.lower()))


def follow_up_is_relevant(question_text: str, expected_points: list[str],
                          answer: str, follow_up: str) -> bool:
    """
    追问贴题判定（规则近似）:
    追问文本与 (题目词 / 期望要点词 / 回答词) 任一有重合 → 贴题。
    "能展开说说吗？" 这类通用话术三边都不重合 → 判为不贴题。

    匹配用子串而非 token 相等 — 中文长 token（如"你提到了三色标记"）
    拆不出独立要点词，必须用包含关系判断。
    """
    if not follow_up:
        return False
    fu = follow_up.lower()
    targets = _tokens(question_text) | _tokens(answer)
    for p in expected_points or []:
        targets |= _tokens(p)
    return any(
        t in fu or fu in t
        for t in targets if len(t) >= 2
    )


# ── 评测执行 ───────────────────────────────────────────────────

async def run_eval(llm, samples: list[dict], repeat: int, multi_judge: bool = False,
                   calibrate: bool = False) -> dict:
    from interview.multi_judge import MultiJudge

    evaluator = AnswerEvaluator(
        llm,
        multi_judge=MultiJudge(llm) if multi_judge else None,
        calibrate=calibrate,
    )
    # 项目已 Agent 化：追问由 FollowUpAgent 自主决策（评分仍用评估器）
    follow_up_agent = FollowUpAgent(llm)
    results = []

    t0 = time.time()
    total_calls = 0

    for sample in samples:
        scores = []
        details = []
        for _ in range(repeat):
            ev = await evaluator.evaluate(sample["question"], sample["answer"])
            scores.append(ev.total_score)
            details.append(ev)
            total_calls += 1

        ev = details[-1]  # 取最后一次的评估做质量分析
        avg_score = statistics.mean(scores)
        std = statistics.pstdev(scores) if len(scores) > 1 else 0.0

        # 追问贴题率：FollowUpAgent 自主决策的追问（Agent 化后的真实追问来源）。
        # Agent 决定不追问（回答已充分）→ 追问文本置空，不计入贴题率统计。
        decision = await follow_up_agent.decide(
            sample["question"], sample["answer"], ev, [],
        )
        agent_follow_up = (
            decision["question"] if decision["continue_follow_up"] and decision["question"]
            else ""
        )
        relevant = (
            follow_up_is_relevant(
                sample["question"].question,
                sample["question"].expected_points,
                sample["answer"],
                agent_follow_up,
            )
            if agent_follow_up
            else False
        )

        results.append({
            **sample,
            "avg_score": round(avg_score, 1),
            "std": round(std, 2),
            "scores": [round(s, 1) for s in scores],
            "follow_up": agent_follow_up[:60],
            "follow_up_relevant": relevant,
            "decision": ev.follow_up_decision.value,
        })

    return {
        "results": results,
        "elapsed_sec": round(time.time() - t0, 1),
        "total_calls": total_calls,
        "sample_count": len(results),
    }


# ── 指标计算 ───────────────────────────────────────────────────

def compute_metrics(run: dict) -> dict:
    results = run["results"]

    # 1. 评分一致性: 平均 std 与超过 0.5 的样本占比
    stds = [r["std"] for r in results if r["std"] > 0]
    avg_std = statistics.mean(stds) if stds else 0.0
    unstable = sum(1 for s in stds if s > 0.5)

    # 2. 评分准确性: MAE + Pearson 相关
    errors = [abs(r["avg_score"] - r["label_score"]) for r in results]
    mae = statistics.mean(errors) if errors else 0.0

    xs = [r["label_score"] for r in results]
    ys = [r["avg_score"] for r in results]
    r = _pearson(xs, ys)

    # 按质量档位分组误差
    by_quality = {}
    for q in ("high", "mid", "low"):
        group = [r_ for r_ in results if r_["quality"] == q]
        if group:
            by_quality[q] = {
                "count": len(group),
                "mae": round(statistics.mean(
                    [abs(r_["avg_score"] - r_["label_score"]) for r_ in group]
                ), 2),
                "avg_label": round(statistics.mean([r_["label_score"] for r_ in group]), 1),
                "avg_score": round(statistics.mean([r_["avg_score"] for r_ in group]), 1),
            }

    # 3. 追问贴题率（仅统计非空追问）
    fu_results = [r_ for r_ in results if r_["follow_up"]]
    relevance_rate = (
        sum(1 for r_ in fu_results if r_["follow_up_relevant"]) / len(fu_results)
        if fu_results else 0.0
    )

    return {
        "avg_std": round(avg_std, 2),
        "unstable_ratio": round(unstable / len(stds), 2) if stds else 0,
        "mae": round(mae, 2),
        "pearson_r": round(r, 3) if r is not None else None,
        "by_quality": by_quality,
        "follow_up_relevance_rate": round(relevance_rate, 3),
        "follow_up_count": len(fu_results),
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


# ── 报告输出 ───────────────────────────────────────────────────

def render_report(run: dict, metrics: dict, repeat: int, mock: bool,
                  multi_judge: bool = False, calibrate: bool = False) -> str:
    from datetime import date

    features = []
    if multi_judge:
        features.append("多评委仲裁")
    if calibrate:
        features.append("评分校准")
    judge_label = "+".join(features) if features else "单评委"
    lines = [
        "# 评估器评测报告 (LLM-as-judge)",
        "",
        f"> 生成时间: {date.today().isoformat()} | 模式: {'Mock（框架验证）' if mock else '真实 API'} | "
        f"评委: {judge_label} | 重复次数: {repeat} | 样本数: {run['sample_count']} | "
        f"LLM 调用: {run['total_calls']} 次 | 耗时: {run['elapsed_sec']}s",
        "",
        "## 1. 评分一致性（同一回答评 N 次的标准差）",
        "",
        f"- 平均标准差: **{metrics['avg_std']}**（满分 10 分制）",
        f"- 不稳定样本占比 (std > 0.5): **{metrics['unstable_ratio']:.0%}**",
        "",
        "## 2. 评分准确性（LLM vs 人工标注）",
        "",
        f"- MAE (平均绝对误差): **{metrics['mae']}**",
        f"- Pearson 相关: **{metrics['pearson_r']}**（越接近 1 越能区分好坏回答）",
        "",
        "| 质量档 | 样本数 | 人工均分 | LLM 均分 | MAE |",
        "|--------|:---:|:---:|:---:|:---:|",
    ]
    for q, m in metrics["by_quality"].items():
        lines.append(
            f"| {q} | {m['count']} | {m['avg_label']} | {m['avg_score']} | {m['mae']} |"
        )

    lines += [
        "",
        "## 3. 追问质量（贴题率 — 追问与题目/要点/回答的关键词呼应）",
        "",
        f"- 非空追问: {metrics['follow_up_count']} 条，贴题率: **{metrics['follow_up_relevance_rate']:.0%}**",
        "",
        "## 4. 逐样本明细",
        "",
        "| 题 | 档 | 人工分 | LLM 均分 | std | 决策 | 追问贴题 |",
        "|----|----|:---:|:---:|:---:|:---:|:---:|",
    ]
    for r in run["results"]:
        icon = "✅" if r["follow_up_relevant"] else "❌"
        lines.append(
            f"| {r['question_id']} | {r['quality']} | {r['label_score']} | "
            f"{r['avg_score']} | {r['std']} | {r['decision']} | {icon} |"
        )
    lines += ["", "---", "", "*报告由 eval/judge_eval.py 生成，可随时复跑。*", ""]
    return "\n".join(lines)


def print_summary(metrics: dict) -> None:
    print()
    print("=" * 56)
    print("  评测指标汇总")
    print("=" * 56)
    print(f"  一致性: 平均 std {metrics['avg_std']} | 不稳定占比 {metrics['unstable_ratio']:.0%}")
    print(f"  准确性: MAE {metrics['mae']} | Pearson r {metrics['pearson_r']}")
    print(f"  追问贴题率: {metrics['follow_up_relevance_rate']:.0%}")
    print("=" * 56)


# ── Main ───────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="评估器 LLM-as-judge 评测")
    parser.add_argument("--mock", action="store_true", help="Mock LLM 离线跑通框架")
    parser.add_argument("--limit", type=int, default=None, help="只测前 N 道题")
    parser.add_argument("--repeat", type=int, default=3, help="每样本重复评估次数（一致性）")
    parser.add_argument("--multi-judge", action="store_true", help="多评委仲裁评估（Before/After 对比用）")
    parser.add_argument("--calibrate", action="store_true", help="评分校准（按命中率纠正高低估）")
    parser.add_argument("--no-report", action="store_true", help="不写 docs/eval_report.md")
    args = parser.parse_args()

    samples = load_samples(args.limit)
    print(f"数据集: {len(samples)} 个样本，重复 {args.repeat} 次")

    if args.mock:
        llm = MockLLMClient()
    else:
        from config import settings
        from core.llm import OpenAIClient
        llm = OpenAIClient(
            model=settings.llm_fast_model or settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )

    run = await run_eval(llm, samples, args.repeat, multi_judge=args.multi_judge,
                         calibrate=args.calibrate)
    metrics = compute_metrics(run)
    print_summary(metrics)

    report = render_report(run, metrics, args.repeat, args.mock,
                           multi_judge=args.multi_judge, calibrate=args.calibrate)
    if not args.no_report:
        out = Path(__file__).parent.parent / "docs" / "eval_report.md"
        out.write_text(report, encoding="utf-8")
        print(f"\n报告已写入: {out}")


if __name__ == "__main__":
    asyncio.run(main())
