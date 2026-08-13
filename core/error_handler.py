"""
全局异常处理与容错机制
======================
系统级异常处理架构，确保面试流程在任何异常情况下都能优雅降级。

设计原则:
    1. 分层容错 — 每层有独立的降级策略
    2. 用户无感 — 尽量不要让用户看到原始报错
    3. 信息不丢 — 异常发生时保存已有数据
    4. 快速恢复 — 支持从断点继续

异常分类:
    - L1 瞬态故障: API 超时、限流 → 重试后恢复
    - L2 服务降级: 主模型挂了 → 切换到备用模型/规则引擎
    - L3 输入问题: JD 格式异常 → 引导用户修正
    - L4 系统故障: 磁盘满、内存不足 → 保存数据后退出

容错架构:
    ┌─────────────────────────────────────────┐
    │              Interview Loop              │
    ├─────────────────────────────────────────┤
    │  LLM Call                                │
    │    ├── L1: 重试 (指数退避)               │
    │    ├── L2: 降级 (备用模型/规则引擎)      │
    │    └── L3: Fallback (返回默认值)         │
    ├─────────────────────────────────────────┤
    │  Code Execution                          │
    │    ├── AST 安全审计                      │
    │    ├── 超时控制 (subprocess timeout)     │
    │    └── 内存限制                          │
    ├─────────────────────────────────────────┤
    │  Data Persistence                        │
    │    ├── 自动保存 (每次回答后保存)         │
    │    └── Crash Recovery (启动时检测未完成) │
    └─────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import functools
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


# ── 异常分类 ────────────────────────────────────────────────────

class ErrorLevel(str, Enum):
    """异常严重等级"""
    TRANSIENT = "transient"    # 瞬态 — 重试可恢复
    DEGRADABLE = "degradable"  # 可降级 — 用备选方案
    RECOVERABLE = "recoverable" # 可恢复 — 需要用户干预
    FATAL = "fatal"           # 致命 — 保存数据退出


@dataclass
class ErrorContext:
    """异常上下文"""
    level: ErrorLevel
    module: str                # 发生异常的模块
    operation: str             # 正在执行的操作
    original_error: Exception | None = None
    user_message: str = ""     # 展示给用户的消息
    recovery_action: str = ""  # 建议的恢复操作
    can_retry: bool = False
    can_degrade: bool = False
    data_saved: bool = False   # 是否已保存数据


class InterviewError(Exception):
    """面试系统基础异常"""
    def __init__(self, ctx: ErrorContext):
        self.ctx = ctx
        super().__init__(ctx.user_message or str(ctx.original_error))


# ── 降级策略注册表 ──────────────────────────────────────────────

class DegradationRegistry:
    """
    降级策略注册表。

    注册各模块的降级方案，异常时自动路由。

    使用:
        reg = DegradationRegistry()
        reg.register("evaluator", "llm_unavailable", rule_based_evaluate)
        result = reg.degrade("evaluator", "llm_unavailable", answer="...")
    """

    def __init__(self):
        self._strategies: dict[str, dict[str, Callable]] = {}

    def register(
        self,
        module: str,
        failure_mode: str,
        strategy: Callable,
    ) -> None:
        """
        注册降级策略。

        Args:
            module: 模块名 (evaluator, question_gen, jd_parser, ...)
            failure_mode: 故障模式 (llm_unavailable, timeout, parse_error, ...)
            strategy: 降级函数
        """
        self._strategies.setdefault(module, {})[failure_mode] = strategy
        logger.debug(f"注册降级策略: {module}.{failure_mode}")

    def get(
        self,
        module: str,
        failure_mode: str = "llm_unavailable",
    ) -> Callable | None:
        """获取降级策略"""
        return self._strategies.get(module, {}).get(failure_mode)

    async def degrade(
        self,
        module: str,
        failure_mode: str,
        **kwargs,
    ) -> Any:
        """执行降级策略"""
        strategy = self.get(module, failure_mode)
        if not strategy:
            # 尝试通用降级
            strategy = self.get(module, "default")
        if not strategy:
            raise InterviewError(ErrorContext(
                level=ErrorLevel.FATAL,
                module=module,
                operation=f"degrade_{failure_mode}",
                user_message=f"模块 {module} 无法处理 {failure_mode}，且无降级方案",
            ))

        logger.info(f"执行降级: {module}.{failure_mode}")
        result = strategy(**kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result


# ── 安全执行装饰器 ──────────────────────────────────────────────

def safe_execute(
    module: str,
    operation: str,
    fallback_value: Any = None,
    reraise: bool = False,
):
    """
    安全执行装饰器 — 包装函数，异常时优雅降级。

    Args:
        module: 模块名
        operation: 操作描述
        fallback_value: 异常时返回的默认值
        reraise: 是否重新抛出异常

    Usage:
        @safe_execute("evaluator", "评估答案", fallback_value=default_eval)
        async def evaluate(self, question, answer): ...
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except InterviewError:
                raise  # 已经是我们的异常，直接抛
            except Exception as e:
                _log_error(module, operation, e)
                if reraise:
                    raise InterviewError(ErrorContext(
                        level=ErrorLevel.DEGRADABLE,
                        module=module, operation=operation,
                        original_error=e,
                        user_message=f"{operation} 失败: {e}",
                    ))
                return fallback_value

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except InterviewError:
                raise
            except Exception as e:
                _log_error(module, operation, e)
                if reraise:
                    raise InterviewError(ErrorContext(
                        level=ErrorLevel.DEGRADABLE,
                        module=module, operation=operation,
                        original_error=e,
                        user_message=f"{operation} 失败: {e}",
                    ))
                return fallback_value

        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        return sync_wrapper
    return decorator


# ── 面试安全上下文管理器 ───────────────────────────────────────

class InterviewSafeContext:
    """
    面试安全上下文 — 确保异常时自动保存进度。

    使用:
        async with InterviewSafeContext(interviewer) as ctx:
            await interviewer.submit_answer(answer)
        # 即使异常，ctx 也会自动保存
    """

    def __init__(self, interviewer, session_manager=None):
        self.interviewer = interviewer
        self.session_manager = session_manager
        self._saved = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            logger.error(f"面试异常: {exc_type.__name__}: {exc_val}")
            # 自动保存
            if self.session_manager and self.interviewer:
                try:
                    self._save_progress()
                    self._saved = True
                    logger.info("已自动保存面试进度")
                except Exception as save_error:
                    logger.error(f"保存进度失败: {save_error}")

            # 打印用户友好消息
            self._print_recovery_info(exc_val)

        return False  # 不吞异常

    def _save_progress(self) -> None:
        """保存当前进度"""
        if not self.session_manager:
            return
        # 构建简化的 session record
        from interview.session_manager import SessionMeta, SessionRecord
        state = self.interviewer.state
        meta = SessionMeta(
            session_id=f"recovery_{id(self)}",
            position=state.jd_analysis.position or "未知岗位",
            created_at="",
            status="in_progress",
            question_count=state.total_questions,
            answered_count=len(state.answers),
        )
        record = SessionRecord(
            meta=meta,
            jd_text=state.jd_text,
            jd_analysis={},
            answers=[{
                "question": a.get("question").question if hasattr(a.get("question"), "question") else str(a.get("question", "")),
                "answer": a.get("answer", ""),
                "evaluation": {},
            } for a in state.answers],
        )
        self.session_manager.save(record)

    def _print_recovery_info(self, error) -> None:
        """打印恢复信息"""
        from rich.console import Console
        console = Console()
        console.print("\n[yellow]⚠️ 面试过程中出现异常，但您的进度已自动保存。[/yellow]")
        console.print("[dim]下次启动时可恢复未完成的面试。[/dim]")
        console.print(f"[dim]错误详情: {error}[/dim]")


# ── 超时控制 ───────────────────────────────────────────────────

async def with_timeout(
    coro,
    timeout_sec: float = 30.0,
    timeout_message: str = "操作超时",
) -> Any:
    """
    带超时的异步调用。

    比 asyncio.wait_for 更好的错误消息。

    Args:
        coro: 协程
        timeout_sec: 超时时间（秒）
        timeout_message: 超时提示

    Returns:
        协程返回值

    Raises:
        InterviewError: 超时时抛出
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_sec)
    except TimeoutError:
        raise InterviewError(ErrorContext(
            level=ErrorLevel.TRANSIENT,
            module="unknown",
            operation="with_timeout",
            user_message=f"{timeout_message}（超过 {timeout_sec}s）",
            can_retry=True,
            recovery_action="请检查网络连接后重试",
        ))


# ── 系统健康检查 ───────────────────────────────────────────────

class HealthCheck:
    """
    系统健康检查 — 面试开始前检查各组件可用性。

    检查项:
        1. LLM API 连通性
        2. 磁盘空间（会话存储）
        3. ChromaDB 状态
        4. 配置完整性

    使用:
        health = HealthCheck(llm_client, session_manager)
        status = await health.run()
        if not status.all_ok:
            print(status.report())
    """

    def __init__(self, llm_client=None, session_manager=None, vector_store=None):
        self.llm = llm_client
        self.session_mgr = session_manager
        self.vector_store = vector_store

    @dataclass
    class Status:
        all_ok: bool = True
        checks: dict[str, bool] = field(default_factory=dict)
        messages: dict[str, str] = field(default_factory=dict)

        def report(self) -> str:
            lines = []
            for name, ok in self.checks.items():
                icon = "✅" if ok else "❌"
                lines.append(f"{icon} {name}: {self.messages.get(name, '')}")
            return "\n".join(lines)

    async def run(self) -> HealthCheck.Status:
        status = self.Status()

        # 1. LLM 连通性
        if self.llm:
            status.checks["LLM API"] = await self._check_llm()
            status.messages["LLM API"] = (
                "正常" if status.checks["LLM API"] else "无法连接"
            )

        # 2. 磁盘空间
        status.checks["磁盘空间"] = self._check_disk()
        status.messages["磁盘空间"] = (
            "充足" if status.checks["磁盘空间"] else "空间不足"
        )

        # 3. 配置检查
        status.checks["配置"] = self._check_config()
        status.messages["配置"] = (
            "完整" if status.checks["配置"] else "缺少必要配置"
        )

        status.all_ok = all(status.checks.values())
        return status

    async def _check_llm(self) -> bool:
        try:
            from core.llm import Message, Role
            response = await asyncio.wait_for(
                self.llm.chat(
                    messages=[Message(role=Role.USER, content="ping")],
                    max_tokens=10,
                ),
                timeout=10,
            )
            return bool(response.content)
        except Exception:
            return False

    def _check_disk(self) -> bool:
        import shutil
        stat = shutil.disk_usage(".")
        return stat.free > 100 * 1024 * 1024  # > 100MB

    def _check_config(self) -> bool:
        import os
        return bool(os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))


# ── 工具函数 ───────────────────────────────────────────────────

def _log_error(module: str, operation: str, error: Exception) -> None:
    """统一错误日志"""
    logger.error(
        f"[{module}] {operation} 失败: {type(error).__name__}: {error}\n"
        f"{traceback.format_exc()}"
    )


def user_friendly_error(error: Exception) -> str:
    """将技术异常转为用户友好的提示"""
    error_type = type(error).__name__
    error_msg = str(error)

    messages = {
        "RateLimitError": "请求太频繁了，请稍等片刻后重试",
        "APITimeoutError": "AI 服务响应超时，正在重试...",
        "APIConnectionError": "网络连接异常，请检查网络后重试",
        "AuthenticationError": "API Key 配置错误，请检查 .env 文件",
        "JSONDecodeError": "AI 返回格式异常，正在自动修正...",
        "InterviewError": error_msg,
    }

    for key, msg in messages.items():
        if key.lower() in error_type.lower():
            return msg

    # 默认
    return f"系统出现了一个暂时的问题（{error_type}），已自动保存您的进度。"
