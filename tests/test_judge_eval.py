"""
LLM-as-judge 评测框架测试
=========================
全部离线（Mock LLM / 纯函数），CI 可直接运行。
"""

import asyncio

from core.mock_llm import MockLLMClient
from eval.judge_eval import (
    compute_metrics,
    follow_up_is_relevant,
    load_samples,
    run_eval,
)


def run(coro):
    return asyncio.run(coro)


class TestDataset:

    def test_samples_load(self):
        samples = load_samples()
        assert len(samples) == 30  # 10 题 × 3 档
        # 每题都有高/中/低三档
        qualities = {}
        for s in samples:
            qualities.setdefault(s["question_id"], set()).add(s["quality"])
        assert all(q == {"high", "mid", "low"} for q in qualities.values())

    def test_limit(self):
        samples = load_samples(limit=3)
        ids = {s["question_id"] for s in samples}
        assert len(ids) == 3


class TestFollowUpRelevance:

    def test_contextual_follow_up(self):
        """引用题目术语的追问 → 贴题"""
        assert follow_up_is_relevant(
            "Go 的 GC 是如何工作的？",
            ["三色标记", "写屏障"],
            "三色标记是核心。",
            "你提到了三色标记，那写屏障在并发场景下是怎么保证安全的？",
        )

    def test_generic_follow_up_not_relevant(self):
        """通用话术 → 不贴题（这正是此前"乱问"bug 的检测手段）"""
        assert not follow_up_is_relevant(
            "Go 的 GC 是如何工作的？",
            ["三色标记", "写屏障"],
            "三色标记是核心。",
            "能展开说说吗？具体是怎么实现的？",
        )

    def test_empty_follow_up(self):
        assert not follow_up_is_relevant("题目", ["要点"], "回答", "")


class TestRunEvalMock:

    def test_run_eval_offline(self):
        """Mock 模式全流程: 3 样本 × 2 次，无异常"""
        samples = load_samples(limit=1)
        result = run(run_eval(MockLLMClient(), samples, repeat=2))
        assert result["sample_count"] == 3
        assert result["total_calls"] == 6
        metrics = compute_metrics(result)
        assert metrics["mae"] >= 0
        # mock 确定性 → 一致性完美
        assert metrics["avg_std"] == 0.0
