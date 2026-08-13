"""
全局异常处理测试（阶段 3）
========================
覆盖降级注册表、safe_execute、超时控制、健康检查、用户友好错误。
全部离线，CI 可直接运行。
"""

import asyncio

import pytest

from core.error_handler import (
    DegradationRegistry,
    ErrorContext,
    ErrorLevel,
    HealthCheck,
    InterviewError,
    safe_execute,
    user_friendly_error,
    with_timeout,
)


def run(coro):
    return asyncio.run(coro)


# ── 异常分类与上下文 ────────────────────────────────────────────

class TestErrorContext:

    def test_interview_error_carries_context(self):
        ctx = ErrorContext(
            level=ErrorLevel.DEGRADABLE,
            module="evaluator",
            operation="评估",
            user_message="评估失败",
        )
        err = InterviewError(ctx)
        assert err.ctx.module == "evaluator"
        assert "评估失败" in str(err)

    def test_error_level_enum(self):
        assert ErrorLevel.TRANSIENT.value == "transient"
        assert ErrorLevel.FATAL.value == "fatal"


# ── 降级注册表 ──────────────────────────────────────────────────

class TestDegradationRegistry:

    def test_register_and_get(self):
        reg = DegradationRegistry()
        reg.register("evaluator", "llm_unavailable", lambda **kw: "rule-based")
        assert reg.get("evaluator", "llm_unavailable") is not None
        assert reg.get("evaluator", "unknown") is None

    def test_degrade_executes_strategy(self):
        reg = DegradationRegistry()
        reg.register("evaluator", "llm_unavailable", lambda answer="": f"降级:{answer}")

        async def scenario():
            return await reg.degrade("evaluator", "llm_unavailable", answer="hi")

        assert run(scenario()) == "降级:hi"

    def test_degrade_missing_strategy_raises(self):
        reg = DegradationRegistry()
        with pytest.raises(InterviewError):
            run(reg.degrade("evaluator", "no_such_mode"))


# ── safe_execute 装饰器 ─────────────────────────────────────────

class TestSafeExecute:

    def test_async_success(self):
        @safe_execute("m", "op", fallback_value="fb")
        async def fn():
            return "ok"

        assert run(fn()) == "ok"

    def test_async_fallback_on_error(self):
        @safe_execute("m", "op", fallback_value="fb")
        async def fn():
            raise RuntimeError("boom")

        assert run(fn()) == "fb"

    def test_sync_fallback_on_error(self):
        @safe_execute("m", "op", fallback_value="fb")
        def fn():
            raise RuntimeError("boom")

        assert fn() == "fb"

    def test_reraise_wraps_in_interview_error(self):
        @safe_execute("m", "op", fallback_value="fb", reraise=True)
        async def fn():
            raise RuntimeError("boom")

        with pytest.raises(InterviewError):
            run(fn())


# ── 超时控制 ────────────────────────────────────────────────────

class TestWithTimeout:

    def test_completes_within_timeout(self):
        async def fast():
            return "ok"

        assert run(with_timeout(fast(), timeout_sec=5)) == "ok"

    def test_timeout_raises_interview_error(self):
        async def slow():
            await asyncio.sleep(5)
            return "never"

        with pytest.raises(InterviewError) as exc:
            run(with_timeout(slow(), timeout_sec=0.05))
        assert exc.value.ctx.can_retry is True


# ── 健康检查 ────────────────────────────────────────────────────

class TestHealthCheck:

    def test_run_without_llm(self):
        health = HealthCheck()
        status = run(health.run())
        assert isinstance(status.all_ok, bool)
        assert "磁盘空间" in status.checks
        assert "配置" in status.checks

    def test_check_llm_available(self):
        class _OkLLM:
            async def chat(self, messages, **kwargs):
                from core.llm import LLMResponse
                return LLMResponse(content="pong")

        health = HealthCheck(llm_client=_OkLLM())
        status = run(health.run())
        assert status.checks["LLM API"] is True

    def test_check_llm_unavailable(self):
        class _FailLLM:
            async def chat(self, messages, **kwargs):
                raise RuntimeError("down")

        health = HealthCheck(llm_client=_FailLLM())
        status = run(health.run())
        assert status.checks["LLM API"] is False

    def test_report_format(self):
        status = HealthCheck.Status()
        status.checks = {"a": True, "b": False}
        status.messages = {"a": "正常", "b": "异常"}
        report = status.report()
        assert "✅ a" in report
        assert "❌ b" in report


# ── 用户友好错误 ────────────────────────────────────────────────

class TestUserFriendlyError:

    def test_known_error_mapping(self):
        # user_friendly_error 按「异常类名」映射（对齐 openai SDK 的异常类名）
        class RateLimitError(Exception):
            pass

        class APITimeoutError(Exception):
            pass

        class AuthenticationError(Exception):
            pass

        assert "频繁" in user_friendly_error(RateLimitError())
        assert "超时" in user_friendly_error(APITimeoutError())
        assert "API Key" in user_friendly_error(AuthenticationError())

    def test_default_message(self):
        msg = user_friendly_error(RuntimeError("some unknown error"))
        assert "暂时" in msg
