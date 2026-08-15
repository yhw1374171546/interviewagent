"""
Agent 工具调用评测
==================
量化 core/agent.py 的 ReAct Agent 工具调用能力（此前零评测）:

    1. 工具选择正确率 — 用对工具的比例（用错工具 = 流程错误）
    2. 任务成功率 — 最终答案包含期望关键字/数值
    3. 步数效率 — 理想步数 vs 实际步数（理想 = 期望工具数 + 1 总结步）
    4. 工具调用失败率 — 工具执行抛异常的比例

任务集: 8 个典型多步任务，覆盖「单工具 / 双工具串联 / 条件分支」三类，
每个任务标注期望工具序列与答案关键字。

运行:
    python eval/tool_use_eval.py --mock     # Stub 预设响应验证框架（0 API）
    python eval/tool_use_eval.py            # 真实 DeepSeek 评测（需 .env key）
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

# ── 确定性工具集（真实可执行，返回固定结果）────────────────────

@tool(name="calculator", description="计算数学表达式，支持 + - * / 和括号")
async def calculator(expression: str) -> str:
    # 仅允许安全字符，防注入
    if not all(c in "0123456789+-*/(). " for c in expression):
        return "错误: 表达式包含非法字符"
    return str(eval(expression, {"__builtins__": {}}, {}))


@tool(name="get_city_weather", description="查询指定城市的今日天气，返回温度和天气状况")
async def get_city_weather(city: str) -> str:
    table = {
        "北京": "晴, 25°C",
        "上海": "小雨, 22°C",
        "深圳": "多云, 28°C",
        "广州": "雷阵雨, 30°C",
    }
    return table.get(city, f"未知城市: {city}")


@tool(name="lookup_stock_price", description="查询股票代码的当前价格，如 AAPL、GOOG")
async def lookup_stock_price(symbol: str) -> str:
    prices = {"AAPL": "189.5", "GOOG": "142.3", "TSLA": "248.9"}
    return prices.get(symbol.upper(), f"未知股票: {symbol}")


@tool(name="search_wiki", description="搜索维基百科获取主题的简介")
async def search_wiki(topic: str) -> str:
    facts = {
        "python": "Python 是一种解释型高级编程语言，由 Guido van Rossum 于 1991 年发布，以简洁易读著称。",
        "redis": "Redis 是开源的内存数据结构存储，支持字符串、哈希、列表等类型，常作缓存和消息队列。",
        "fastapi": "FastAPI 是 Python 的高性能 Web 框架，基于 Starlette 和 Pydantic，支持自动生成 OpenAPI 文档。",
        "docker": "Docker 是容器化平台，通过镜像和容器实现应用打包与隔离部署。",
    }
    return facts.get(topic.lower(), f"未找到主题: {topic}")


@tool(name="convert_currency", description="汇率换算，支持 USD/CNY/EUR")
async def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
    rates = {"USD": 7.2, "CNY": 1.0, "EUR": 7.8}
    f, t = rates.get(from_currency.upper()), rates.get(to_currency.upper())
    if f is None or t is None:
        return "错误: 不支持的货币"
    return str(round(amount * f / t, 2))


ALL_TOOLS = [
    calculator, get_city_weather, lookup_stock_price, search_wiki, convert_currency,
]


# ── 任务集 ─────────────────────────────────────────────────────

TASKS = [
    {
        "name": "单工具-计算",
        "prompt": "请计算 (15 + 3) * 2 的结果。",
        "expected_tools": ["calculator"],
        "expected_args": [{"expression": "(15 + 3) * 2"}],
        "expected_keywords": ["36"],
    },
    {
        "name": "单工具-查询",
        "prompt": "上海今天的天气怎么样？",
        "expected_tools": ["get_city_weather"],
        "expected_args": [{"city": "上海"}],
        "expected_keywords": ["22"],
    },
    {
        "name": "单工具-检索",
        "prompt": "帮我查一下 Redis 是什么？",
        "expected_tools": ["search_wiki"],
        "expected_args": [{"topic": "redis"}],
        "expected_keywords": ["内存", "缓存"],
    },
    {
        "name": "双工具-串联",
        "prompt": "查询 AAPL 的股价，然后告诉我它比 GOOG 贵多少。",
        "expected_tools": ["lookup_stock_price", "lookup_stock_price"],
        "expected_args": [{"symbol": "AAPL"}, {"symbol": "GOOG"}],
        "expected_keywords": ["47.2", "47"],
    },
    {
        "name": "双工具-不同",
        "prompt": "北京今天多少度？顺便查一下 Python 的简介。",
        "expected_tools": ["get_city_weather", "search_wiki"],
        "expected_args": [{"city": "北京"}, {"topic": "python"}],
        "expected_keywords": ["25", "Python"],
    },
    {
        "name": "条件分支-汇率",
        "prompt": "把 100 美元换成人民币是多少？",
        "expected_tools": ["convert_currency"],
        "expected_args": [{"amount": 100, "from_currency": "USD", "to_currency": "CNY"}],
        "expected_keywords": ["720"],
    },
    {
        "name": "多步-综合",
        "prompt": "查询深圳天气和 TSLA 股价。",
        "expected_tools": ["get_city_weather", "lookup_stock_price"],
        "expected_args": [{"city": "深圳"}, {"symbol": "TSLA"}],
        "expected_keywords": ["28", "248.9", "248"],
    },
    {
        "name": "无需工具-直接回答",
        "prompt": "请用一句话介绍你自己。",
        "expected_tools": [],
        "expected_args": [],
        "expected_keywords": [],
    },
]


# ── 评测执行 ───────────────────────────────────────────────────

def _has_keyword(answer: str, keywords: list[str]) -> bool:
    return all(k in answer for k in keywords)


def _tools_used(result) -> list[str]:
    """从 AgentStep.action 提取实际使用的工具序列"""
    tools = []
    for step in result.steps:
        try:
            actions = json.loads(step.action)
            for a in actions:
                tools.append(a["name"])
        except (ValueError, KeyError):
            continue
    return tools


def _tool_success(tools: list[str], expected: list[str]) -> bool:
    """用对工具: 实际工具序列是期望序列的子集（允许多余但必须覆盖期望）"""
    if not expected:
        return len(tools) == 0
    return all(t in tools for t in expected)


async def run_task(llm: LLMClient, task: dict, config: AgentConfig) -> dict:
    agent = Agent(llm, tools=ALL_TOOLS, config=config)
    t0 = time.time()
    result = await agent.run(task["prompt"])
    elapsed = round(time.time() - t0, 1)

    tools = _tools_used(result)
    tool_ok = _tool_success(tools, task["expected_tools"])
    ans_ok = _has_keyword(result.answer or "", task["expected_keywords"]) if task["expected_keywords"] else True
    ideal_steps = len(task["expected_tools"]) + 1  # 工具步 + 总结步
    # 工具执行失败统计: 观察里出现"错误"/"失败"视为该工具调用执行失败
    exec_failures = sum(
        1 for step in result.steps
        if "错误" in step.observation or "失败" in step.observation
    )

    return {
        "name": task["name"],
        "tools_used": tools,
        "tool_correct": tool_ok,
        "answer_correct": ans_ok,
        "steps": len(result.steps),
        "ideal_steps": ideal_steps,
        "tool_calls": result.tool_calls_count,
        "tool_exec_failures": exec_failures,
        "answer_preview": (result.answer or "")[:80],
        "elapsed_sec": elapsed,
    }


async def run_eval(llm: LLMClient, mock: bool) -> dict:
    config = AgentConfig(verbose=False, max_steps=10, max_tool_calls=12)
    results = []
    for task in TASKS:
        results.append(await run_task(llm, task, config))

    n = len(results)
    tool_ok = sum(1 for r in results if r["tool_correct"])
    ans_ok = sum(1 for r in results if r["answer_correct"])
    both = sum(1 for r in results if r["tool_correct"] and r["answer_correct"])
    steps_ratio = [r["steps"] / max(1, r["ideal_steps"]) for r in results]
    exec_fail = sum(1 for r in results if r["tool_exec_failures"] > 0)
    total_calls = sum(r["tool_calls"] for r in results)
    failed_calls = sum(r["tool_exec_failures"] for r in results)

    return {
        "mode": "mock" if mock else "real",
        "sample_count": n,
        "tool_selection_accuracy": round(tool_ok / n * 100, 1),
        "task_success_rate": round(ans_ok / n * 100, 1),
        "end_to_end_rate": round(both / n * 100, 1),
        "avg_steps_ratio": round(sum(steps_ratio) / n, 2),
        "tool_exec_failure_rate": round(failed_calls / total_calls * 100, 1) if total_calls else 0.0,
        "tasks_with_failures": exec_fail,
        "results": results,
    }


# ── Main ───────────────────────────────────────────────────────

def render(run: dict) -> str:
    lines = [
        "=" * 66,
        f"  Agent 工具调用评测（{run['mode']}）| {run['sample_count']} 个任务",
        "=" * 66,
        f"  工具选择正确率 : {run['tool_selection_accuracy']}%",
        f"  任务成功率     : {run['task_success_rate']}%",
        f"  端到端成功率   : {run['end_to_end_rate']}%",
        f"  步数效率(实际/理想): {run['avg_steps_ratio']}",
        f"  工具执行失败率 : {run['tool_exec_failure_rate']}% ({run['tasks_with_failures']} 个任务有失败)",
        "-" * 66,
        "  任务明细:",
        "  名称 | 工具正确 | 答案正确 | 步数(理想) | 用到的工具",
    ]
    for r in run["results"]:
        tc = "✅" if r["tool_correct"] else "❌"
        ac = "✅" if r["answer_correct"] else "❌"
        lines.append(
            f"  {r['name']:<16} {tc} {ac}  {r['steps']}({r['ideal_steps']})  {','.join(r['tools_used'])}"
        )
    lines.append("=" * 66)
    return "\n".join(lines)


# ── Stub LLM（mock 模式）──────────────────────────────────────

class _StubLLM(LLMClient):
    """mock 模式: 按任务预设工具调用序列（验证评测框架本身，不测 LLM 能力）"""

    def __init__(self, sequences: dict[str, list]):
        super().__init__(model="stub")
        self.sequences = sequences
        self._index: dict[str, int] = {}

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        from core.llm import Role

        user_prompt = next(
            (m.content for m in reversed(messages) if m.role == Role.USER), ""
        )
        task_name = next(
            (t["name"] for t in TASKS if t["prompt"] == user_prompt), "default"
        )
        seq = self.sequences.get(task_name, [])
        i = self._index.get(task_name, 0)
        self._index[task_name] = i + 1

        if i < len(seq):
            tc = seq[i]
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id=str(i), name=tc["name"], arguments=tc.get("args", {}))],
                usage={"prompt_tokens": 10, "completion_tokens": 10},
            )
        # 序列用完 → 用最近一次工具观察结果作为答案（mock 只验证工具链路，
        # 答案正确性由真实 LLM 评测体现）
        last_tool_msg = next(
            (m.content for m in reversed(messages) if m.role == Role.TOOL), ""
        )
        return LLMResponse(
            content=f"工具结果: {last_tool_msg}",
            usage={"prompt_tokens": 10, "completion_tokens": 10},
        )


def _build_stub_sequences() -> dict[str, list]:
    """每个任务预设"正确的工具调用序列 + 参数"（mock 验证框架用）"""
    seqs = {}
    for t in TASKS:
        seqs[t["name"]] = [
            {"name": tn, "args": args_}
            for tn, args_ in zip(t["expected_tools"], t["expected_args"])
        ]
    return seqs


async def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 工具调用评测")
    parser.add_argument("--mock", action="store_true", help="Stub 预设响应验证框架")
    args = parser.parse_args()

    if args.mock:
        llm = _StubLLM(_build_stub_sequences())
        run = await run_eval(llm, mock=True)
    else:
        from config import settings
        from core.llm import OpenAIClient
        llm = OpenAIClient(
            model=settings.llm_fast_model or settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        run = await run_eval(llm, mock=False)

    print(render(run))

    out = Path(__file__).parent.parent / "logs" / "tool_use_eval.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 已保存: {out}")
    return 0


if __name__ == "__main__":
    asyncio.run(main())
