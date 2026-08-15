"""
面试模拟 Agent — 入口
=====================

针对目标 JD 进行全真模拟面试:
- 自动解析 JD，提取核心技术栈和考察重点
- 生成 5 类面试题（技术基础/场景设计/项目深挖/行为面试/代码实操）
- 每道题根据回答质量智能追问（逐层深挖）
- 多维度实时打分（正确性/深度/结构/相关性）
- 生成完整面试评估报告

使用方式:
    python main.py                          # 交互式模拟面试
    python main.py --jd path/to/jd.txt      # 从文件读 JD
    python main.py --questions 10           # 自定义题目数量
    python main.py --test                   # 运行内置演示
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from config import settings
from core.llm import AnthropicClient, OpenAIClient
from interview import Interviewer
from utils.logger import get_logger

logger = get_logger(__name__)
console = Console()


# ── 示例 JD（用于快速体验）───────────────────────────────────

DEMO_JD = """
高级后端开发工程师

【岗位职责】
1. 负责核心业务系统的架构设计和开发，保障系统的高可用和高性能
2. 参与技术方案评审，推动技术方案落地
3. 指导和培养初中级工程师，提升团队整体技术能力
4. 持续优化系统性能，解决线上疑难问题

【任职要求】
1. 本科及以上学历，3-5 年 Python/Go 后端开发经验
2. 精通 Python，熟悉 Django/FastAPI 等主流框架
3. 熟悉 MySQL、Redis、Elasticsearch 等常用中间件
4. 有分布式系统设计和开发经验，了解微服务架构
5. 有高并发场景下的性能优化经验
6. 良好的沟通能力和团队协作精神
7. 加分项：有 Docker/K8s 运维经验，了解 CI/CD 流程
"""

DEMO_ANSWERS = [
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

    # 跳过几题，直接跳到行为题
    "我作为项目负责人，发现技术方案和产品需求有冲突时，会先理解产品为什么要这个需求，"
    "背后的业务目标是什么。然后我会提出几个技术方案，分别说明各自的利弊和开发成本，"
    "让产品和业务方一起做 trade-off 决策。",
]


def create_llm_client(provider: str, model: str) -> OpenAIClient | AnthropicClient:
    """根据配置创建 LLM 客户端"""
    if provider == "anthropic":
        return AnthropicClient(
            model=model,
            api_key=settings.anthropic_api_key,
            base_url=settings.llm_base_url,
        )
    else:
        return OpenAIClient(
            model=model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )


# ── 命令行参数 ─────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="面试模拟 Agent — 针对 JD 的全真模拟面试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--provider", default=settings.llm_provider, help="LLM Provider")
    parser.add_argument("--model", default=settings.llm_model, help="模型名称")
    parser.add_argument("--jd", type=str, help="JD 文件路径（不指定则交互输入）")
    parser.add_argument("--questions", type=int, default=8, help="面试题目数量")
    parser.add_argument("--max-follow-ups", type=int, default=3, help="每题最大追问次数")
    parser.add_argument("--test", action="store_true", help="运行内置演示")
    return parser.parse_args()


# ── 交互式面试 ─────────────────────────────────────────

async def run_interactive(args: argparse.Namespace) -> None:
    """交互式模拟面试"""
    llm = create_llm_client(args.provider, args.model)

    # ── 获取 JD ──
    if args.jd:
        jd_text = Path(args.jd).read_text(encoding="utf-8")
        console.print(f"[dim]📄 已加载 JD: {args.jd}[/dim]\n")
    else:
        console.print(Panel.fit(
            "[bold cyan]📋 请粘贴目标岗位的 JD（职位描述）[/bold cyan]\n"
            "粘贴完成后输入 [bold]END[/bold] 结束:\n\n"
            "💡 提示: 直接输入 [bold]demo[/bold] 使用内置示例 JD",
            border_style="cyan",
        ))

        lines = []
        while True:
            line = input()
            if line.strip().upper() == "END":
                break
            if line.strip().lower() == "demo":
                jd_text = DEMO_JD
                console.print("[dim]📋 已加载示例 JD[/dim]")
                break
            lines.append(line)
        else:
            jd_text = "\n".join(lines)

        if not jd_text.strip():
            console.print("[red]JD 不能为空[/red]")
            return

    # ── 初始化面试 ──
    # CLI 场景用纯内存记忆（不初始化 ChromaDB，避免 embedding 模型加载触网卡住）
    from interview.memory_context import InterviewMemory

    interviewer = Interviewer(
        llm_client=llm,
        total_questions=args.questions,
        max_follow_ups=args.max_follow_ups,
        memory=InterviewMemory(use_chroma=False),
    )

    with Progress(SpinnerColumn(), TextColumn("[cyan]正在解析 JD 并生成面试题...[/cyan]"), transient=True) as progress:
        progress.add_task("init", total=None)
        turn = await interviewer.start(jd_text)

    console.print()
    console.print(Panel(turn.message, title="🎙️ 面试官", border_style="green"))
    console.print("[dim]（输入 skip 跳过当前题，quit 结束面试）[/dim]\n")

    # ── 面试主循环 ──
    turn = await interviewer.next_question()

    while not turn.is_finished:
        # 显示题目
        console.print(Panel(turn.message, title=f"📝 {turn.progress}", border_style="yellow"))

        # 获取回答
        try:
            answer = console.input("\n[bold green]✍️  你的回答:[/bold green] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]面试终止[/yellow]")
            break

        answer = answer.strip()

        if answer.lower() in ("quit", "exit", "q"):
            console.print("[yellow]面试终止[/yellow]")
            break

        if answer.lower() == "skip":
            console.print("[dim]⏭️ 跳过[/dim]")
            turn = await interviewer.skip_question()
            continue

        if not answer:
            console.print("[dim]输入为空，请重新回答或输入 skip 跳过[/dim]")
            continue

        # 提交评估
        with Progress(SpinnerColumn(), TextColumn("[cyan]评估中...[/cyan]"), transient=True) as progress:
            progress.add_task("eval", total=None)
            turn = await interviewer.submit_answer(answer)

        console.print()

        # 显示评估反馈
        if turn.evaluation:
            _print_evaluation(turn.evaluation)

        # 如果结束了，跳出
        if turn.is_finished:
            break

        # 显示追问消息
        if turn.phase.value == "follow_up":
            console.print(Panel(turn.message, title="🔍 追问", border_style="magenta"))
        else:
            console.print(Panel(turn.message, title=f"📝 {turn.progress}", border_style="yellow"))

    # ── 最终报告 ──
    if turn.report:
        _print_final_report(turn.report)


def _print_evaluation(eval) -> None:
    """打印单题评估"""
    table = Table(title="📊 本题评估", show_header=False, border_style="blue")
    table.add_column(style="bold")
    table.add_column()

    score_color = "green" if eval.total_score >= 7 else "yellow" if eval.total_score >= 5 else "red"
    table.add_row("综合得分", f"[bold {score_color}]{eval.total_score}/10[/bold {score_color}] ({eval.level})")
    table.add_row("正确性", f"{'█' * eval.correctness}{'░' * (10 - eval.correctness)} {eval.correctness}/10")
    table.add_row("深度", f"{'█' * eval.depth}{'░' * (10 - eval.depth)} {eval.depth}/10")
    table.add_row("结构", f"{'█' * eval.structure}{'░' * (10 - eval.structure)} {eval.structure}/10")
    table.add_row("相关性", f"{'█' * eval.relevance}{'░' * (10 - eval.relevance)} {eval.relevance}/10")
    if eval.overall_comment:
        table.add_row("评价", eval.overall_comment)
    if eval.strengths:
        table.add_row("亮点", "; ".join(eval.strengths))
    if eval.weaknesses:
        table.add_row("建议", "; ".join(eval.weaknesses))

    console.print(table)


def _print_final_report(report) -> None:
    """打印最终报告"""
    console.print()
    console.rule("📋 面试评估报告")

    # 总评
    score_color = "green" if report.overall_score >= 7 else "yellow" if report.overall_score >= 5 else "red"
    console.print(f"\n[bold]综合评分: [{score_color}]{report.overall_score}/10[/{score_color}] {report.overall_level}[/bold]")
    console.print(f"[bold]面试结论: {report.verdict}[/bold] — {report.verdict_reason}")

    # 分维度
    dim_table = Table(title="分维度得分")
    dim_table.add_column("维度")
    dim_table.add_column("分数")
    dim_table.add_column("进度")

    for name, score in [
        ("正确性", report.avg_correctness),
        ("深度", report.avg_depth),
        ("结构", report.avg_structure),
        ("相关性", report.avg_relevance),
    ]:
        bar = "█" * int(score) + "░" * (10 - int(score))
        dim_table.add_row(name, f"{score}/10", bar)

    console.print(dim_table)

    # 优势 & 不足
    if report.main_strengths:
        console.print("\n[bold green]👍 主要优势[/bold green]")
        for s in report.main_strengths:
            console.print(f"  • {s}")

    if report.main_weaknesses:
        console.print("\n[bold yellow]⚠️ 待提升[/bold yellow]")
        for w in report.main_weaknesses:
            console.print(f"  • {w}")

    if report.improvement_advice:
        console.print(f"\n[bold cyan]💡 改进建议[/bold cyan]\n{report.improvement_advice}")

    # 逐题详情
    if report.details:
        console.print("\n[bold]📝 逐题详情[/bold]")
        detail_table = Table()
        detail_table.add_column("#", style="dim")
        detail_table.add_column("题目")
        detail_table.add_column("得分")
        detail_table.add_column("等级")

        for d in report.details:
            q_preview = d["question"][:50] + "..." if len(d["question"]) > 50 else d["question"]
            s = d["score"]
            color = "green" if s >= 7 else "yellow" if s >= 5 else "red"
            detail_table.add_row(str(d["index"]), q_preview, f"[{color}]{s}/10[/{color}]", d["level"])

        console.print(detail_table)

    console.rule()


# ── 演示模式 ───────────────────────────────────────────

async def run_demo(args: argparse.Namespace) -> None:
    """使用内置示例 JD 和预置回答快速运行演示"""
    llm = create_llm_client(args.provider, args.model)

    console.print(Panel.fit(
        "[bold cyan]🎬 演示模式[/bold cyan]\n"
        "使用内置示例 JD 和预置回答，展示完整的面试流程",
        border_style="cyan",
    ))

    # 演示模式用纯内存记忆（离线，不初始化 ChromaDB）
    from interview.memory_context import InterviewMemory

    interviewer = Interviewer(
        llm_client=llm,
        total_questions=args.questions,
        max_follow_ups=args.max_follow_ups,
        memory=InterviewMemory(use_chroma=False),
    )

    with Progress(SpinnerColumn(), TextColumn("[cyan]解析 JD 并生成面试题...[/cyan]"), transient=True) as progress:
        progress.add_task("init", total=None)
        report = await interviewer.run_full_interview(DEMO_JD, DEMO_ANSWERS)

    _print_final_report(report)


# ── 入口 ───────────────────────────────────────────────

def main():
    args = parse_args()

    if args.test:
        asyncio.run(run_demo(args))
    else:
        asyncio.run(run_interactive(args))


if __name__ == "__main__":
    main()
