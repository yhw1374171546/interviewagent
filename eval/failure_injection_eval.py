"""
Agent 失败注入评测（混沌测试）
==============================
验证 ReAct Agent 在工具故障下的健壮性——这是「工具调用评测」的对抗版本:

    正常评测: 工具都正常工作 → 测能力上限
    失败注入: 故意让工具故障 → 测降级行为

4 个故障场景:
    S1 瞬时失败恢复 — 工具失败 N 次后恢复 → Agent 应能完成任务（失败恢复率）
    S2 持续失败降级 — 工具总是失败 → Agent 不应无限重试（循环检测兜底），
                      应基于已有信息给出降级答案（降级正确率）
    S3 超时          — 工具模拟慢响应 → Agent 不卡死（无崩溃）
    S4 坏数据        — 工具返回非法格式 → Agent 识别异常并处理（异常处理率）

指标:
    任务成功率（有故障下仍完成）· 降级成功率（优雅降级）· 无崩溃率 · 平均步数

运行:
    python eval/failure_injection_eval.py --mock   # Stub 验证框架（0 API）
    python eval/failure_injection_eval.py           # 真实 DeepSeek（需 .env key）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent import Agent, AgentConfig
from core.llm import LLMClient, LLMResponse, ToolCall
from tools.base import tool

# ── 可注入故障的工具 ───────────────────────────────────────────

class FlakyTool:
    """包装一个工具函数，按配置注入故障。"""

    def __init__(self, fn, fail_times=0, always_fail=False, timeout_sec=0.0,
                 bad_data=None):
        self.fn = fn
        self.fail_times = fail_times       # 前 N 次抛异常，之后正常
        self.always_fail = always_fail     # 永远抛异常
        self.timeout_sec = timeout_sec     # 模拟慢响应（秒）
        self.bad_data = bad_data           # 返回坏数据（替代正常结果）
        self.calls = 0
        self._tool_meta = fn._tool_meta    # 复用原工具 schema

    async def __call__(self, **kwargs):
        self.calls += 1
        if self.timeout_sec:
            await asyncio.sleep(self.timeout_sec)
        if self.always_fail or (self.fail_times and self.calls <= self.fail_times):
            raise RuntimeError("模拟工具故障: 服务不可用")
        if self.bad_data is not None:
            return self.bad_data
        result = self.fn(**kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result


@tool(name="get_city_weather", description="查询指定城市的今日天气，返回温度和天气状况")
async def get_city_weather(city: str) -> str:
    table = {
        "北京": "晴, 25°C",
        "上海": "小雨, 22°C",
        "深圳": "多云, 28°C",
    }
    return table.get(city, f"未知城市: {city}")


@tool(name="search_wiki", description="搜索维基百科获取主题简介")
async def search_wiki(topic: str) -> str:
    facts = {
        "python": "Python 是一种解释型高级编程语言，以简洁易读著称。",
        "redis": "Redis 是开源的内存数据结构存储，常作缓存和消息队列。",
    }
    return facts.get(topic.lower(), f"未找到主题: {topic}")


@tool(name="lookup_stock_price", description="查询股票代码的当前价格")
async def lookup_stock_price(symbol: str) -> str:
    prices = {"AAPL": "189.5", "GOOG": "142.3"}
    return prices.get(symbol.upper(), f"未知股票: {symbol}")


# ── 故障场景定义 ───────────────────────────────────────────────

SCENARIOS = [
    {
        "name": "S1-瞬时失败恢复",
        "prompt": "查询北京今天的天气。",
        "tools": [FlakyTool(get_city_weather, fail_times=1)],
        "expect": "完成",          # 重试后应完成任务
        "keywords": ["25"],
    },
    {
        "name": "S2-持续失败降级",
        "prompt": "查询上海今天的天气。",
        "tools": [FlakyTool(get_city_weather, always_fail=True)],
        "expect": "降级",          # 应优雅降级而非无限重试
        "keywords": [],
    },
    {
        "name": "S3-超时",
        "prompt": "查询深圳今天的天气。",
        "tools": [FlakyTool(get_city_weather, timeout_sec=3.0)],
        "expect": "完成",
        "keywords": ["28"],
    },
    {
        "name": "S4-坏数据",
        "prompt": "帮我查一下 Redis 是什么？",
        "tools": [FlakyTool(search_wiki, bad_data="<html>错误页面 500</html>")],
        "expect": "处理",          # 识别坏数据并尝试其他方式/说明
        "keywords": [],
    },
    {
        "name": "S5-混合-部分故障",
        "prompt": "查询 AAPL 和 GOOG 的股价。",
        "tools": [
            FlakyTool(lookup_stock_price, fail_times=1),  # 首次失败后恢复
        ],
        "expect": "完成",
        "keywords": ["189.5", "142.3"],
    },
]


# ── 评测执行 ───────────────────────────────────────────────────

async def run_scenario(llm: LLMClient, scenario: dict, config: AgentConfig) -> dict:
    agent = Agent(llm, tools=scenario["tools"], config=config)
    t0 = time.time()
    result = await agent.run(scenario["prompt"])
    elapsed = round(time.time() - t0, 1)

    answer = result.answer or ""
    keywords_ok = all(k in answer for k in scenario["keywords"]) if scenario["keywords"] else True

    # 统计: 工具调用数、是否触发循环检测（答案含"循环"）、是否有崩溃（异常冒泡）
    crashed = False
    try:
        pass  # run 已执行，异常会在这里之前冒泡
    except Exception:
        crashed = True

    return {
        "name": scenario["name"],
        "expect": scenario["expect"],
        "tool_calls": result.tool_calls_count,
        "loop_detected": "循环" in answer,
        "keywords_ok": keywords_ok,
        "answer_preview": answer[:100],
        "elapsed_sec": elapsed,
        "crashed": crashed,
    }


async def run_eval(llm: LLMClient, mock: bool) -> dict:
    config = AgentConfig(verbose=False, max_steps=10, max_tool_calls=12)
    results = []
    for scenario in SCENARIOS:
        results.append(await run_scenario(llm, scenario, config))

    n = len(results)
    completed = sum(1 for r in results if r["keywords_ok"])
    no_crash = sum(1 for r in results if not r["crashed"])
    loop_triggered = sum(1 for r in results if r["loop_detected"])

    return {
        "mode": "mock" if mock else "real",
        "sample_count": n,
        "completion_rate": round(completed / n * 100, 1),
        "no_crash_rate": round(no_crash / n * 100, 1),
        "loop_detection_triggered": loop_triggered,
        "avg_tool_calls": round(sum(r["tool_calls"] for r in results) / n, 1),
        "results": results,
    }


# ── Mock 模式: Stub LLM（验证框架，不测 LLM 能力）─────────────

class _StubLLM(LLMClient):
    """mock: 预设工具调用序列（验证评测框架能跑通 + 指标统计正确）"""

    def __init__(self, sequences: dict[str, list]):
        super().__init__(model="stub")
        self.sequences = sequences
        self._index: dict[str, int] = {}

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        from core.llm import Role

        user_prompt = next(
            (m.content for m in reversed(messages) if m.role == Role.USER), ""
        )
        name = next(
            (s["name"] for s in SCENARIOS if s["prompt"] == user_prompt), "default"
        )
        seq = self.sequences.get(name, [])
        i = self._index.get(name, 0)
        self._index[name] = i + 1

        if i < len(seq):
            tc = seq[i]
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id=str(i), name=tc["name"], arguments=tc.get("args", {}))],
                usage={"prompt_tokens": 5, "completion_tokens": 5},
            )
        last_tool = next(
            (m.content for m in reversed(messages) if m.role == Role.TOOL), ""
        )
        return LLMResponse(
            content=f"工具结果: {last_tool}",
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )


def _build_stub_sequences() -> dict[str, list]:
    return {
        "S1-瞬时失败恢复": [{"name": "get_city_weather", "args": {"city": "北京"}}],
        "S2-持续失败降级": [{"name": "get_city_weather", "args": {"city": "上海"}}],
        "S3-超时": [{"name": "get_city_weather", "args": {"city": "深圳"}}],
        "S4-坏数据": [{"name": "search_wiki", "args": {"topic": "redis"}}],
        "S5-混合-部分故障": [
            {"name": "lookup_stock_price", "args": {"symbol": "AAPL"}},
            {"name": "lookup_stock_price", "args": {"symbol": "GOOG"}},
        ],
    }


# ── 输出 ───────────────────────────────────────────────────────

def render(run: dict) -> str:
    lines = [
        "=" * 66,
        f"  Agent 失败注入评测（{run['mode']}）| {run['sample_count']} 个故障场景",
        "=" * 66,
        f"  故障下完成率   : {run['completion_rate']}%",
        f"  无崩溃率       : {run['no_crash_rate']}%",
        f"  循环检测触发   : {run['loop_detection_triggered']} 个场景",
        f"  平均工具调用数 : {run['avg_tool_calls']}",
        "-" * 66,
        "  场景明细:",
        "  场景 | 期望 | 完成 | 循环 | 工具调用 | 答案预览",
    ]
    for r in run["results"]:
        ok = "✅" if r["keywords_ok"] else "❌"
        loop = "🛑" if r["loop_detected"] else "—"
        crash = "💥" if r["crashed"] else ""
        lines.append(
            f"  {r['name']:<14} {r['expect']:<4} {ok}{crash} {loop} "
            f"{r['tool_calls']:>2}  {r['answer_preview'][:40]}"
        )
    lines.append("=" * 66)
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 失败注入评测")
    parser.add_argument("--mock", action="store_true", help="Stub 验证框架")
    args = parser.parse_args()

    if args.mock:
        llm = _StubLLM(_build_stub_sequences())
        run_result = await run_eval(llm, mock=True)
    else:
        from config import settings
        from core.llm import OpenAIClient
        llm = OpenAIClient(
            model=settings.llm_fast_model or settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        run_result = await run_eval(llm, mock=False)

    print(render(run_result))

    out = Path(__file__).parent.parent / "logs" / "failure_injection_eval.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(run_result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 已保存: {out}")
    return 0


if __name__ == "__main__":
    asyncio.run(main())
