"""
真实 DeepSeek 联调成本探针
==========================
用真实 API 跑一场完整 8 题面试，验证「单场成本 ¥0.049」口径在当前价格/模型
路由下是否站得住，输出各阶段 token 明细 + 成本估算。

用法（需要 .env 里的真实 key）:
    python tools/realtime_cost_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from core.llm import OpenAIClient  # noqa: E402
from interview import Interviewer  # noqa: E402
from main import DEMO_JD  # noqa: E402

# 8 题预置回答（覆盖技术/场景/项目/行为/代码各类，贴近真实面试回答长度）
ANSWERS = [
    # 技术基础 — GIL
    "Python的GIL是全局解释器锁，它确保同一时刻只有一个线程执行Python字节码。"
    "在多线程场景下，CPU密集型任务会因为GIL而无法利用多核优势。"
    "解决方案包括：使用多进程(multiprocessing)绕过GIL，用C扩展释放GIL，"
    "或者使用异步编程(asyncio)处理IO密集型任务。我们项目中大量使用celery做异步任务，"
    "就是通过多进程worker来绕过GIL限制的。",
    # 场景设计 — 排行榜
    "对于实时排行榜，我会使用Redis的有序集合(sorted set)来实现。"
    "核心思路是用ZADD更新分数，ZREVRANGE获取前N名。"
    "如果日活100w，需要考虑：1) 数据分片，按时间段拆分key；"
    "2) 使用Redis Cluster做水平扩展；3) 榜单数据可以定时刷入MySQL做持久化；"
    "4) 热点数据加本地缓存。不过说实话我对排行榜的实时一致性保证还不太有经验。",
    # 项目深挖 — 技术方案与产品冲突
    "我作为项目负责人，发现技术方案和产品需求有冲突时，会先理解产品为什么要这个需求，"
    "背后的业务目标是什么。然后我会提出几个技术方案，分别说明各自的利弊和开发成本，"
    "让产品和业务方一起做 trade-off 决策。",
    # 技术基础 — FastAPI 异步
    "FastAPI 基于 Starlette 和 Pydantic，天然支持 async/await。"
    "异步接口适合 IO 密集型场景，比如数据库查询、外部 API 调用，"
    "可以用 asyncio.gather 并发执行多个 IO 任务。但 CPU 密集型任务在事件循环里"
    "会阻塞其他请求，需要丢给线程池或进程池执行。",
    # 场景设计 — 缓存一致性
    "缓存一致性我一般采用 Cache Aside 模式：读时先查缓存，未命中查库并回填；"
    "写时先更新数据库再删缓存，配合短暂的过期时间兜底。"
    "极端一致场景会用 binlog 订阅 + 消息队列异步删除缓存，或者引入版本号。",
    # 项目深挖 — 性能优化
    "我主导过一次接口性能优化：先用链路追踪定位到慢 SQL 和 N+1 查询，"
    "通过加索引、批量查询、结果缓存，把 P99 延迟从 800ms 降到 120ms。"
    "过程中用压测脚本量化收益，每次改动都有 Before/After 数据对比。",
    # 行为面试 — 冲突处理
    "之前和同事在技术选型上有分歧，我组织了一次小型技术评审，"
    "把两种方案的优缺点、维护成本、团队熟悉度都列出来对比，"
    "最后用数据说话选型，大家达成了共识。",
    # 行为面试 — 学习能力
    "我习惯用项目驱动学习：比如为了搞懂消息队列，我在项目中手写了一个精简版"
    "生产者消费者模型，再对比 RabbitMQ 的实现理解其设计取舍。",
]


async def main() -> int:
    print("=" * 60)
    print("真实 DeepSeek 8 题面试成本探针")
    print(f"主模型: {settings.llm_model}  快模型: {settings.llm_fast_model or '(未配置)'}")
    print(f"Base URL: {settings.llm_base_url}")
    print("=" * 60)

    # 模型路由: 高频调用用快模型，报告用强模型
    flash = OpenAIClient(
        model=settings.llm_fast_model or settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
    pro = OpenAIClient(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    interviewer = Interviewer(
        llm_client=flash,
        llm_strong=pro,
        total_questions=8,
        max_follow_ups=3,
    )

    print("\n⏳ 正在运行完整 8 题面试（真实 API，可能耗时几分钟）...\n")
    report = await interviewer.run_full_interview(DEMO_JD, ANSWERS)

    # ── 成本统计 ──
    cost = interviewer.session_cost_estimate()
    print("=" * 60)
    print("📊 本场面试实测（真实 DeepSeek）")
    print(f"  输入 tokens : {cost['prompt_tokens']:,}")
    print(f"  输出 tokens : {cost['completion_tokens']:,}")
    print(f"  成本估算   : ¥{cost['cost_yuan']}")

    print("\n📈 分阶段明细:")
    for stage, m in interviewer.state.metrics.items():
        print(f"  {stage:<22} 延迟 {m.get('latency', 0):>7.1f}s  "
              f"in {m.get('prompt_tokens', 0):>6}  out {m.get('completion_tokens', 0):>6}  "
              f"model {m.get('model', '')}")

    print(f"\n🎯 面试结论: {report.overall_score}/10 {report.verdict}")
    print("=" * 60)

    # 输出 JSON 便于归档
    summary = {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "fast_model": settings.llm_fast_model,
        "base_url": settings.llm_base_url,
        "pricing": settings.llm_pricing,
        "prompt_tokens": cost["prompt_tokens"],
        "completion_tokens": cost["completion_tokens"],
        "cost_yuan": cost["cost_yuan"],
        "metrics": interviewer.state.metrics,
        "overall_score": report.overall_score,
        "verdict": report.verdict,
    }
    out = Path(__file__).resolve().parent.parent / "logs" / "realtime_cost_probe.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 明细已保存: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
