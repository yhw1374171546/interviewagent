"""
多题评估并行化测试（C1）
========================
覆盖 evaluate_many 批量接口：
    1. 并行结果与逐题串行完全一致（并发不改语义，gather 保序）
    2. 并发上限生效（慢速 LLM 模拟 IO 等待，峰值并发 ≤ 上限）
    3. 空输入/单输入边界
全部离线（Mock LLM），CI 可直接运行。
"""

import asyncio

from core.mock_llm import MockLLMClient
from interview.evaluator import AnswerEvaluator
from interview.question_bank import InterviewQuestion, QuestionType


def run(coro):
    return asyncio.run(coro)


def _q(question: str, points: list[str]) -> InterviewQuestion:
    return InterviewQuestion(
        id=question[:4], type=QuestionType.TECHNICAL, category="Python基础",
        question=question, expected_points=points, difficulty=4,
    )


ITEMS = [
    (_q("解释 Python 的 GIL 全局解释器锁", ["GIL", "多线程"]),
     "GIL 是 CPython 的全局解释器锁，多线程下 CPU 密集型任务会被串行化。"),
    (_q("为什么 MySQL 用 B+ 树做索引", ["B+树", "范围查询"]),
     "B+ 树矮胖扇出大，叶子节点链表支持范围查询。"),
    (_q("Redis 为什么这么快", ["内存", "io多路复用"]),
     "纯内存操作 + 单线程 + epoll 多路复用。"),
    (_q("TCP 三次握手", ["SYN", "ACK"]),
     "SYN → SYN+ACK → ACK，确认收发能力并同步初始序号。"),
    (_q("docker 镜像和容器的区别", ["镜像", "容器"]),
     "镜像是只读模板，容器是镜像的运行实例。"),
    (_q("HTTP 和 HTTPS 的区别", ["TLS", "加密"]),
     "HTTPS 在 HTTP 上加了 TLS 加密层，防窃听防篡改。"),
    (_q("git rebase 和 merge 的区别", ["rebase", "merge"]),
     "rebase 变基重放提交，merge 保留分叉历史。"),
    (_q("什么是幂等性", ["重复请求", "结果一致"]),
     "同一个请求执行多次结果一致，比如支付接口的幂等设计。"),
]


class TestEvaluateMany:

    def test_parallel_matches_serial(self):
        """并行评估结果与逐题串行完全一致（并发不改语义）"""
        evaluator = AnswerEvaluator(MockLLMClient())

        async def scenario():
            serial = []
            for q, a in ITEMS:
                serial.append(await evaluator.evaluate(q, a))
            parallel = await evaluator.evaluate_many(ITEMS)
            return serial, parallel

        serial, parallel = run(scenario())
        assert len(parallel) == len(serial)
        for s, p in zip(serial, parallel):
            assert p.total_score == s.total_score
            assert p.follow_up_decision == s.follow_up_decision
            assert p.overall_comment == s.overall_comment
            assert p.matched_points == s.matched_points

    def test_empty_items(self):
        """空输入 → 空结果"""
        evaluator = AnswerEvaluator(MockLLMClient())
        assert run(evaluator.evaluate_many([])) == []

    def test_single_item(self):
        """单输入行为与 evaluate 一致"""
        evaluator = AnswerEvaluator(MockLLMClient())
        q, a = ITEMS[0]

        async def scenario():
            one = await evaluator.evaluate(q, a)
            many = await evaluator.evaluate_many([(q, a)])
            return one, many[0]

        one, many = run(scenario())
        assert many.total_score == one.total_score


class SlowMockLLM(MockLLMClient):
    """带 IO 等待的慢速 Mock — 模拟真实 API 延迟，用于验证并发上限"""

    def __init__(self, delay: float = 0.02):
        super().__init__()
        self.delay = delay
        self._active = 0
        self._peak = 0
        self._lock = asyncio.Lock()

    @property
    def peak_concurrency(self) -> int:
        return self._peak

    async def chat(self, messages, tools=None, temperature=0.7,
                   max_tokens=4096, stream=False):
        async with self._lock:
            self._active += 1
            self._peak = max(self._peak, self._active)
        try:
            await asyncio.sleep(self.delay)  # 模拟网络 IO 等待
            return await super().chat(messages, tools, temperature, max_tokens, stream)
        finally:
            async with self._lock:
                self._active -= 1


class TestConcurrencyCap:

    def test_peak_concurrency_bounded(self):
        """并发上限生效: 8 题 × max_concurrency=3 → 峰值并发 ≤ 3 且全部完成"""
        evaluator = AnswerEvaluator(SlowMockLLM())

        async def scenario():
            results = await evaluator.evaluate_many(ITEMS, max_concurrency=3)
            return results

        results = run(scenario())
        assert len(results) == len(ITEMS)
        assert evaluator.llm.peak_concurrency <= 3

    def test_concurrency_one_is_serial(self):
        """max_concurrency=1 退化为串行（峰值并发 == 1）"""
        evaluator = AnswerEvaluator(SlowMockLLM())

        async def scenario():
            return await evaluator.evaluate_many(ITEMS, max_concurrency=1)

        run(scenario())
        assert evaluator.llm.peak_concurrency == 1
