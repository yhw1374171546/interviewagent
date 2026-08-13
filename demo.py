"""
演示脚本 — 展示工程实现（无需 LLM API Key）
===========================================

演示三项核心能力，全部是确定性代码逻辑，零 API 调用:

  1. 规则引擎 JD 解析 — 从 JD 中提取技术栈
  2. 题库检索 — 根据技能标签匹配面试题
  3. 代码判题 — 沙箱执行 + 测试用例验证

运行:
    python demo.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

console = Console()


# ═══════════════════════════════════════════════════════════════
#  Demo 1: 规则引擎 JD 解析
# ═══════════════════════════════════════════════════════════════

def demo_rule_based_jd_parsing():
    """演示: 不用 LLM，纯规则匹配从 JD 中提取技能"""
    console.print(Rule("[bold]Demo 1: 规则引擎 JD 解析 (0 API 调用)[/bold]"))

    from interview.skill_taxonomy import get_skill_coverage_report, rule_based_extract

    # 模拟一份真实的 JD
    jd_text = """
    [高级后端开发工程师] -- 北京 -- 30k-50k

    岗位职责:
    1. 负责公司核心业务系统的后端架构设计与开发
    2. 持续优化系统性能，解决高并发场景下的性能瓶颈
    3. 指导初级工程师进行代码审查和重构

    任职要求:
    1. 本科及以上学历，3-5年 Python 后端开发经验
    2. 精通 Python，熟悉 Django 或 FastAPI 框架
    3. 熟悉 MySQL、Redis、Elasticsearch 等常用中间件
    4. 了解 Docker、Kubernetes 等容器化技术
    5. 有分布式系统设计经验者优先
    6. 具备良好的沟通能力和团队协作精神

    加分项:
    - 有微服务架构落地经验
    - 熟悉 Kafka、RabbitMQ 等消息队列
    - 了解 CI/CD 流程
    """

    # 规则引擎解析
    result = rule_based_extract(jd_text)
    coverage = get_skill_coverage_report(jd_text)

    # 输出
    console.print(f"\n[bold]规则引擎覆盖率: [green]{coverage['coverage']}%[/green][/bold]")
    console.print("[dim](这部分是确定性代码逻辑，不需要 LLM)[/dim]\n")

    # 技能表格
    table = Table(title="提取的技能栈")
    table.add_column("技能", style="cyan")
    table.add_column("领域", style="dim")
    table.add_column("类别")
    table.add_column("类型", style="yellow")

    for s in result.skills:
        icon = "[*]" if s["source"] == "preferred" else "[+]"
        table.add_row(
            f"{icon} {s['name']}",
            s["domain"],
            s["category"],
            "加分项" if s["source"] == "preferred" else "必须",
        )

    console.print(table)

    # 基本信息
    console.print(f"\n[bold]学历:[/bold] {result.education or '未识别'}")
    console.print(f"[bold]经验:[/bold] {result.experience or '未识别'}")
    console.print(f"[bold]软技能:[/bold] {', '.join(result.soft_skills) if result.soft_skills else '未识别'}")

    if result.unmatched_text:
        console.print(f"\n[dim]未匹配文本 ({len(result.unmatched_text)} 字符) -- 这部分需要 LLM 兜底[/dim]")


# ═══════════════════════════════════════════════════════════════
#  Demo 2: 题库检索
# ═══════════════════════════════════════════════════════════════

def demo_question_bank_retrieval():
    """演示: 根据技能标签从题库检索面试题"""
    console.print(Rule("[bold]Demo 2: 题库检索 (0 API 调用)[/bold]"))

    from interview.question_bank import QuestionBankRetriever

    retriever = QuestionBankRetriever()

    # 模拟 JD 中提取出的技能
    skills = ["python", "mysql", "redis", "分布式", "docker"]

    console.print(f"\n[bold]检索条件:[/bold] {', '.join(skills)}\n")

    # 检索
    results = retriever.retrieve(skills, total=6)

    table = Table(title="检索到的面试题")
    table.add_column("ID", style="dim")
    table.add_column("类型")
    table.add_column("类别")
    table.add_column("题目")
    table.add_column("难度")
    table.add_column("匹配标签")

    type_icons = {
        "technical":   "[T]",
        "scenario":    "[S]",
        "project":     "[P]",
        "behavioral":  "[B]",
        "coding":      "[C]",
    }

    for q in results:
        icon = type_icons.get(q.type.value, "[?]")

        table.add_row(
            q.id,
            f"{icon} {q.type.value}",
            q.category,
            q.question[:50] + "...",
            "*" * q.difficulty,
            ", ".join(q.tags[:4]),
        )

    console.print(table)

    # 题库统计
    stats = retriever.stats()
    console.print(f"\n[dim]题库总计: {stats['total_questions']} 题 | 标签数: {stats['unique_tags']} | "
                   f"题型分布: {stats['by_type']}[/dim]")


# ═══════════════════════════════════════════════════════════════
#  Demo 3: 代码判题
# ═══════════════════════════════════════════════════════════════

async def demo_code_judge():
    """演示: 真实沙箱判题 -- 好的代码通过，烂的代码挂掉"""
    console.print(Rule("[bold]Demo 3: 沙箱代码判题 (真实执行 + 测试用例)[/bold]"))

    from interview.code_judge import (
        PRESET_CODE_QUESTIONS,
        audit_code_safety,
        run_judge,
    )

    question = PRESET_CODE_QUESTIONS[0]  # LRU Cache

    console.print(f"\n[bold]题目:[/bold] {question.title}")
    console.print(f"[dim]{question.description[:100]}...[/dim]")
    console.print(f"[dim]测试用例数: {len(question.test_cases)}[/dim]\n")

    # ── 案例 A: 正确的实现 ──
    correct_code = """
class Node:
    __slots__ = ('key', 'value', 'prev', 'next')
    def __init__(self, key=0, value=0):
        self.key = key
        self.value = value
        self.prev = None
        self.next = None

class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = {}
        self.head = Node()
        self.tail = Node()
        self.head.next = self.tail
        self.tail.prev = self.head

    def _remove(self, node):
        node.prev.next = node.next
        node.next.prev = node.prev

    def _add_to_head(self, node):
        node.next = self.head.next
        node.prev = self.head
        self.head.next.prev = node
        self.head.next = node

    def get(self, key: int) -> int:
        if key not in self.cache:
            return -1
        node = self.cache[key]
        self._remove(node)
        self._add_to_head(node)
        return node.value

    def put(self, key: int, value: int) -> None:
        if key in self.cache:
            self._remove(self.cache[key])
        node = Node(key, value)
        self._add_to_head(node)
        self.cache[key] = node
        if len(self.cache) > self.capacity:
            removed = self.tail.prev
            self._remove(removed)
            del self.cache[removed.key]
"""

    console.print("[bold green]案例 A: 正确的哈希表+双向链表实现[/bold green]")

    # AST 安全检查
    safe, msg = audit_code_safety(correct_code)
    console.print(f"  安全检查: {'[OK] 通过' if safe else '[X] ' + msg}")

    # 沙箱判题
    result = await run_judge(correct_code, question)
    if result.passed:
        console.print("  测试结果: [green]全部通过[/green]")
    else:
        console.print(f"  测试结果: [red]{result.failed_tests} 个失败[/red]")
    console.print(f"  通过: {result.passed_tests}/{result.total_tests}")

    # ── 案例 B: 有 bug 的实现 ──
    buggy_code = """
class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key: int) -> int:
        if key in self.cache:
            return self.cache[key]
        return -1

    def put(self, key: int, value: int) -> None:
        if key in self.cache:
            self.cache[key] = value
            return
        if len(self.cache) >= self.capacity:
            oldest = self.order[0] if self.order else list(self.cache.keys())[0]
            del self.cache[oldest]
            if oldest in self.order:
                self.order.remove(oldest)
        self.cache[key] = value
        self.order.append(key)
"""

    console.print("\n[bold red]案例 B: 有 Bug 的实现 (没有维护真正的 LRU 顺序)[/bold red]")

    safe, msg = audit_code_safety(buggy_code)
    console.print(f"  安全检查: {'[OK] 通过' if safe else '[X] ' + msg}")

    result2 = await run_judge(buggy_code, question)
    if result2.passed:
        console.print("  测试结果: [yellow]全部通过[/yellow]")
        console.print("  [dim]注意: 这个有 bug 的实现也全通过了 -- 说明测试用例不够全面[/dim]")
        console.print("  [dim]真实场景需要更全面的测试用例来覆盖 LRU 顺序正确性[/dim]")
    else:
        console.print(f"  测试结果: [red]{result2.passed_tests}/{result2.total_tests} 通过[/red]")
        for d in result2.details:
            if not d.get("passed"):
                console.print(f"    [X] {d['name']}: 期望 {d.get('expected', '')} 实际 {d.get('got', '')}")

    # ── 案例 C: 恶意代码被拦截 ──
    malicious_code = """
import os
os.system("rm -rf /")
"""

    console.print("\n[bold red]案例 C: 恶意代码 (尝试执行系统命令)[/bold red]")
    safe, msg = audit_code_safety(malicious_code)
    console.print(f"  安全检查: {'[OK] 通过' if safe else '[X] 拦截: ' + msg}")


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════

def main():
    console.print(Panel.fit(
        "[bold cyan]Interview Sim Agent -- 工程能力演示[/bold cyan]\n\n"
        "以下演示均 [bold]不需要 LLM API[/bold]，全部是确定性代码逻辑:\n"
        "  - 规则引擎 JD 解析  -- 200+ 关键词匹配\n"
        "  - 题库检索          -- 倒排索引 + 分层选择\n"
        "  - 代码判题          -- AST 白名单 + subprocess 沙箱 + 测试用例",
        border_style="cyan",
    ))

    # Demo 1 & 2 是同步的
    demo_rule_based_jd_parsing()
    console.print("\n")
    demo_question_bank_retrieval()
    console.print("\n")

    # Demo 3 是异步的（沙箱执行）
    asyncio.run(demo_code_judge())

    console.print("\n")
    console.print(Panel.fit(
        "[bold green][OK] 演示完成[/bold green]\n\n"
        "以上三个模块展示了本项目的核心工程实现:\n"
        "  - 规则引擎 -- 确定性的、可解释的、零成本的 JD 解析\n"
        "  - 题库检索 -- 倒排索引 + 分层选择 + 难度分层\n"
        "  - 沙箱判题 -- AST 白名单 + subprocess 隔离 + 真实测试用例\n\n"
        "LLM 在本项目中的角色是 [bold]辅助增强[/bold]，而非核心逻辑:\n"
        "  - 规则覆盖不到的 JD 文本 -> LLM 兜底\n"
        "  - 题库题目需要 JD 定制 -> LLM 做措辞微调\n"
        "  - 回答的深度/结构评估 -> LLM 做语义分析\n"
        "  - 最终报告的文字总结 -> LLM 写文案",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
