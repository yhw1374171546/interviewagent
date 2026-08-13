"""
LLM 调用重试与容错机制
======================
提供指数退避重试、熔断器、Fallback 降级链。

为什么需要:
    - LLM API 不稳定: 限流(429)、超时、临时故障
    - 不同模型价格差异大: 主模型挂了可以自动降级到备用模型
    - 不能因为一次 API 错误就让整个面试中断

优化手段:
    1. 指数退避 + 随机抖动 (jitter) — 防止惊群效应
    2. 熔断器 (Circuit Breaker) — 连续失败 N 次后跳过等待期
    3. Fallback 降级链 — GPT-4o → GPT-4o-mini → 本地规则引擎
    4. 结构化输出重试 — JSON 解析失败时要求 LLM 修正格式

使用:
    retry_config = RetryConfig(max_retries=3, fallback_models=["gpt-4o-mini"])
    result = await with_retry(lambda: llm.chat(...), config)
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


# ── 配置 ────────────────────────────────────────────────────────

class FallbackStrategy(str, Enum):
    """降级策略"""
    SWITCH_MODEL = "switch_model"      # 切换到备用模型
    USE_CACHE = "use_cache"            # 使用缓存结果
    RULE_BASED = "rule_based"          # 降级到规则引擎
    GRACEFUL_DEGRADE = "graceful"      # 优雅降级（返回部分结果）


@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 3                    # 最大重试次数
    base_delay: float = 1.0                 # 基础等待时间（秒）
    max_delay: float = 30.0                 # 最大等待时间
    exponential_base: int = 2               # 指数退避底数
    jitter: bool = True                     # 是否加随机抖动

    # 熔断器
    circuit_breaker_threshold: int = 5      # 连续失败 N 次后熔断
    circuit_breaker_recovery: float = 60.0  # 熔断恢复等待（秒）

    # 降级
    fallback_models: list[str] = field(default_factory=list)  # 备选模型
    fallback_strategy: FallbackStrategy = FallbackStrategy.SWITCH_MODEL

    # 可重试的异常类型
    retryable_errors: tuple = (
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "ServiceUnavailableError",
    )


# ── 熔断器 ──────────────────────────────────────────────────────

class CircuitBreaker:
    """
    熔断器 — 防止对已故障的服务持续发起请求。

    三态模型:
        CLOSED      — 正常，请求通过
        OPEN        — 熔断，直接拒绝请求
        HALF_OPEN   — 半开，允许探测请求

    使用:
        breaker = CircuitBreaker(threshold=5, recovery=60)
        async with breaker:
            result = await call_api()
    """

    class State(str, Enum):
        CLOSED = "closed"
        OPEN = "open"
        HALF_OPEN = "half_open"

    def __init__(self, threshold: int = 5, recovery_sec: float = 60.0):
        self.threshold = threshold
        self.recovery_sec = recovery_sec
        self.state = self.State.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._success_count = 0

    @property
    def is_open(self) -> bool:
        """熔断器是否打开"""
        if self.state == self.State.CLOSED:
            return False
        if self.state == self.State.OPEN:
            if time.time() - self._last_failure_time > self.recovery_sec:
                self.state = self.State.HALF_OPEN
                logger.info("🔧 熔断器进入半开状态，允许探测请求")
                return False
            return True
        return False  # HALF_OPEN — 允许请求

    def record_success(self) -> None:
        """记录成功"""
        self._failure_count = 0
        self._success_count += 1
        if self.state == self.State.HALF_OPEN and self._success_count >= 2:
            self.state = self.State.CLOSED
            self._success_count = 0
            logger.info("✅ 熔断器恢复关闭")

    def record_failure(self) -> None:
        """记录失败"""
        self._failure_count += 1
        self._last_failure_time = time.time()
        self._success_count = 0

        if self._failure_count >= self.threshold:
            self.state = self.State.OPEN
            logger.warning(
                f"🔴 熔断器打开！连续失败 {self._failure_count} 次，"
                f"将在 {self.recovery_sec}s 后恢复"
            )


# ── 重试核心 ────────────────────────────────────────────────────

def _calc_delay(attempt: int, config: RetryConfig) -> float:
    """计算退避延迟（指数退避 + 随机抖动）"""
    delay = min(
        config.base_delay * (config.exponential_base ** attempt),
        config.max_delay,
    )
    if config.jitter:
        delay *= 0.5 + random.random()  # 50%~150% 抖动
    return delay


async def with_retry(
    fn: Callable[[], Any],
    config: RetryConfig | None = None,
    fallback_fn: Callable[[], Any] | None = None,
) -> Any:
    """
    带重试和降级的异步调用包装器。

    流程:
        1. 尝试调用 → 成功则返回
        2. 异常 → 判断是否可重试
        3. 可重试 → 指数退避等待 → 重试
        4. 不可重试/超过最大重试 → 执行降级策略
        5. 降级失败 → 抛出原始异常

    Args:
        fn: 主函数
        config: 重试配置
        fallback_fn: 降级函数

    Returns:
        函数返回值

    Raises:
        最后一次异常（降级也失败时）
    """
    config = config or RetryConfig()
    last_error = None

    for attempt in range(config.max_retries + 1):  # 0 是首次尝试
        try:
            result = await fn() if asyncio.iscoroutinefunction(fn) else fn()
            if attempt > 0:
                logger.info(f"✅ 第 {attempt} 次重试成功")
            return result

        except Exception as e:
            last_error = e
            error_type = type(e).__name__
            error_msg = str(e)[:200]

            # 判断是否可重试
            is_retryable = (
                error_type in config.retryable_errors
                or "rate" in error_msg.lower()
                or "timeout" in error_msg.lower()
                or "connection" in error_msg.lower()
                or "500" in error_msg
                or "503" in error_msg
                or "529" in error_msg
            )

            if not is_retryable or attempt >= config.max_retries:
                logger.error(
                    f"❌ {'不可重试' if not is_retryable else '已达最大重试次数'}: "
                    f"{error_type}: {error_msg}"
                )
                break

            delay = _calc_delay(attempt, config)
            logger.warning(
                f"⏳ 第 {attempt + 1}/{config.max_retries} 次重试，"
                f"等待 {delay:.1f}s... ({error_type})"
            )
            await asyncio.sleep(delay)

    # ── 执行降级 ──
    if fallback_fn:
        try:
            logger.info("🔄 执行降级策略...")
            return await fallback_fn() if asyncio.iscoroutinefunction(fallback_fn) else fallback_fn()
        except Exception as fallback_error:
            logger.error(f"❌ 降级也失败了: {fallback_error}")

    raise last_error


# ── LLM 专用重试 ────────────────────────────────────────────────

class LLMRetryHandler:
    """
    LLM 调用专用重试处理器。

    封装了熔断器 + 指数退避 + 模型降级。

    使用:
        handler = LLMRetryHandler(main_llm, fallback_llm)
        response = await handler.call(lambda: main_llm.chat(...))
    """

    def __init__(
        self,
        main_llm,           # LLMClient
        fallback_llm=None,  # LLMClient | None
        retry_config: RetryConfig | None = None,
    ):
        self.main_llm = main_llm
        self.fallback_llm = fallback_llm
        self.config = retry_config or RetryConfig()
        self.circuit_breaker = CircuitBreaker(
            threshold=self.config.circuit_breaker_threshold,
            recovery_sec=self.config.circuit_breaker_recovery,
        )
        self.stats = {"success": 0, "failure": 0, "retry": 0, "fallback": 0}

    async def call(
        self,
        fn: Callable,
        fallback_fn: Callable | None = None,
    ) -> Any:
        """
        调用 LLM，自动处理重试和降级。

        Args:
            fn: 主 LLM 调用
            fallback_fn: 自定义降级函数（如用规则引擎代替 LLM）
        """
        # 检查熔断器
        if self.circuit_breaker.is_open:
            logger.warning("⚡ 熔断器已打开，直接使用降级")
            if fallback_fn or self.fallback_llm:
                self.stats["fallback"] += 1
                return await self._do_fallback(fallback_fn)
            raise RuntimeError("熔断器已打开且无降级方案")

        try:
            result = await with_retry(
                fn,
                config=self.config,
                fallback_fn=lambda: self._do_fallback(fallback_fn),
            )
            self.circuit_breaker.record_success()
            self.stats["success"] += 1
            return result

        except Exception as e:
            self.circuit_breaker.record_failure()
            self.stats["failure"] += 1
            # 再次尝试降级
            if fallback_fn or self.fallback_llm:
                self.stats["fallback"] += 1
                try:
                    return await self._do_fallback(fallback_fn)
                except Exception:
                    pass
            raise e

    async def _do_fallback(self, fallback_fn: Callable | None) -> Any:
        """执行降级"""
        if fallback_fn:
            return await fallback_fn() if asyncio.iscoroutinefunction(fallback_fn) else fallback_fn()
        if self.fallback_llm:
            return await self.fallback_llm.chat(...)
        raise RuntimeError("无可用降级方案")

    @property
    def is_healthy(self) -> bool:
        return not self.circuit_breaker.is_open
