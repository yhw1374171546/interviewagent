"""
重试与熔断测试（阶段 3）
======================
覆盖退避计算、熔断三态、with_retry（重试/降级/协程判断）、LLMRetryHandler。
全部离线，CI 可直接运行。
"""

import asyncio
import time

import pytest

from core.retry import (
    CircuitBreaker,
    FallbackStrategy,
    LLMRetryHandler,
    RetryConfig,
    _calc_delay,
    is_retryable_error,
    with_retry,
)


def run(coro):
    return asyncio.run(coro)


# ── 退避计算 ────────────────────────────────────────────────────

class TestCalcDelay:

    def test_exponential_backoff_no_jitter(self):
        config = RetryConfig(base_delay=1.0, exponential_base=2, max_delay=30.0, jitter=False)
        assert _calc_delay(0, config) == 1.0
        assert _calc_delay(2, config) == 4.0

    def test_max_delay_cap(self):
        config = RetryConfig(base_delay=1.0, exponential_base=2, max_delay=8.0, jitter=False)
        assert _calc_delay(10, config) == 8.0

    def test_jitter_within_range(self):
        config = RetryConfig(base_delay=2.0, exponential_base=2, max_delay=30.0, jitter=True)
        for _ in range(20):
            delay = _calc_delay(0, config)
            assert 1.0 <= delay <= 3.0  # 2.0 * (0.5~1.5)


# ── 熔断器三态 ──────────────────────────────────────────────────

class TestCircuitBreaker:

    def test_initial_closed(self):
        cb = CircuitBreaker(threshold=3)
        assert cb.state == CircuitBreaker.State.CLOSED
        assert cb.is_open is False

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(threshold=3, recovery_sec=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False  # 2 < 3
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.OPEN
        assert cb.is_open is True

    def test_recovery_to_half_open(self):
        cb = CircuitBreaker(threshold=2, recovery_sec=0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.OPEN
        assert cb.is_open is False  # recovery=0 → 立即半开，允许探测
        assert cb.state == CircuitBreaker.State.HALF_OPEN

    def test_half_open_closes_after_successes(self):
        cb = CircuitBreaker(threshold=2, recovery_sec=0)
        cb.record_failure()
        cb.record_failure()
        cb.is_open  # 触发 HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitBreaker.State.HALF_OPEN
        cb.record_success()  # 2 次成功 → 恢复关闭
        assert cb.state == CircuitBreaker.State.CLOSED


# ── is_retryable_error ──────────────────────────────────────────

class TestIsRetryable:

    def test_retryable_keywords(self):
        config = RetryConfig()
        assert is_retryable_error(RuntimeError("RateLimitError: 429"), config) is True
        assert is_retryable_error(RuntimeError("connection timeout"), config) is True
        assert is_retryable_error(RuntimeError("internal 500 error"), config) is True

    def test_non_retryable(self):
        assert is_retryable_error(ValueError("bad argument"), RetryConfig()) is False


# ── with_retry ──────────────────────────────────────────────────

class TestWithRetry:

    def test_success_passthrough(self):
        async def fn():
            return "ok"
        assert run(with_retry(fn, RetryConfig(max_retries=2))) == "ok"

    def test_retries_then_succeeds(self):
        async def scenario():
            state = {"calls": 0}

            async def fn():
                state["calls"] += 1
                if state["calls"] == 1:
                    raise RuntimeError("RateLimitError: 429")
                return "ok"

            result = await with_retry(fn, RetryConfig(max_retries=2, base_delay=0.01))
            return result, state["calls"]

        result, calls = run(scenario())
        assert result == "ok"
        assert calls == 2

    def test_non_retryable_raises_immediately(self):
        async def scenario():
            calls = {"n": 0}

            async def fn():
                calls["n"] += 1
                raise ValueError("bad argument")

            with pytest.raises(ValueError):
                await with_retry(fn, RetryConfig(max_retries=3))
            return calls["n"]

        assert run(scenario()) == 1  # 不重试

    def test_fallback_executes(self):
        async def scenario():
            async def fn():
                raise RuntimeError("RateLimitError")

            async def fb():
                return "fallback"

            return await with_retry(fn, RetryConfig(max_retries=0), fallback_fn=fb)

        assert run(scenario()) == "fallback"

    def test_awaits_coroutine_returned_by_lambda(self):
        """回归: lambda 包裹协程必须被 await（iscoroutinefunction 判断的坑）"""
        async def scenario():
            async def work():
                return "done"
            return await with_retry(lambda: work(), RetryConfig())

        assert run(scenario()) == "done"


# ── LLMRetryHandler ─────────────────────────────────────────────

class _OkLLM:
    async def chat(self, *args, **kwargs):
        return "ok"


class _FailLLM:
    async def chat(self, *args, **kwargs):
        raise RuntimeError("RateLimitError")


class _FallbackLLM:
    async def chat(self, *args, **kwargs):
        return "fallback"


class TestLLMRetryHandler:

    def test_success(self):
        handler = LLMRetryHandler(main_llm=_OkLLM())
        result = run(handler.call(lambda: handler.main_llm.chat()))
        assert result == "ok"
        assert handler.stats["success"] == 1

    def test_fallback_on_failure(self):
        handler = LLMRetryHandler(
            main_llm=_FailLLM(),
            fallback_llm=_FallbackLLM(),
            retry_config=RetryConfig(max_retries=0),
        )
        result = run(handler.call(lambda: handler.main_llm.chat()))
        assert result == "fallback"

    def test_breaker_open_short_circuits_to_fallback(self):
        handler = LLMRetryHandler(
            main_llm=_FailLLM(),
            fallback_llm=_FallbackLLM(),
            retry_config=RetryConfig(max_retries=0),
        )
        handler.circuit_breaker.state = CircuitBreaker.State.OPEN
        handler.circuit_breaker._last_failure_time = time.time()  # 未到恢复期
        result = run(handler.call(lambda: handler.main_llm.chat()))
        assert result == "fallback"

    def test_fallback_strategy_enum(self):
        assert FallbackStrategy.SWITCH_MODEL.value == "switch_model"
        assert FallbackStrategy.RULE_BASED.value == "rule_based"
