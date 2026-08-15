"""
面试模拟 Agent — Web 服务
=========================
FastAPI 后端，对接 interview 核心模块。

架构:
    Browser (原生 JS)
        │  REST API
    FastAPI (web/server.py)
        │  调用
    Interviewer + SessionManager (interview/)
        │  调用
    LLMClient (core/) — OpenAI / Anthropic / Mock

核心设计:
    1. 无状态 API + 会话注册表: 活跃的 Interviewer 实例在内存中
       (INTERVIEWERS dict)，每次 turn 后序列化状态到磁盘 — 服务
       重启后可从 SessionManager 恢复未完成的面试。
    2. Mock 降级: 未配置 API Key 时自动使用 MockLLMClient，
       Web Demo 无需任何配置即可完整体验面试流程。
    3. 简历解析: 上传 PDF 用 pypdf 提取文本；岗位名从「求职意向」
       等关键词正则提取，回退到 JD 解析器的猜测逻辑。

启动:
    uvicorn web.server:app --reload
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent.parent))  # 保证项目根目录可导入

from config import settings
from core.llm import LLMClient
from core.mock_llm import MockLLMClient
from interview.interviewer import Interviewer
from interview.memory_context import InterviewMemory
from interview.profile import ProfileBuilder
from interview.session_manager import SessionManager
from utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(title="面试模拟 Agent")

# ── 静态文件 ───────────────────────────────────────────────────
WEB_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.get("/")
async def index():
    """首页（落地页 + 聊天页共用一个 HTML，JS 控制视图切换）"""
    return FileResponse(WEB_DIR / "static" / "index.html")


# ── LLM 客户端（按模型缓存，支持模型路由）─────────────────────

_llm_cache: dict[str, LLMClient] = {}


def get_llm(model: str | None = None) -> LLMClient:
    """
    获取 LLM 客户端（按模型名缓存）。

    模型路由: 高频调用（评估/JD解析/暖场/出题）用快速模型
    （LLM_FAST_MODEL，实测 flash 比 pro 快 2.3 倍），最终报告用主模型。
    优先级: 配置的 provider → 无 Key 时降级到 Mock。
    """
    model = model or settings.llm_model
    if model in _llm_cache:
        return _llm_cache[model]

    provider = settings.llm_provider
    has_key = bool(settings.llm_api_key or settings.anthropic_api_key)

    if provider == "mock" or not has_key:
        logger.info("未配置 API Key，Web 使用 Mock LLM（演示模式）")
        client = MockLLMClient()
    elif provider == "anthropic" and settings.anthropic_api_key:
        from core.llm import AnthropicClient

        client = AnthropicClient(model=model, api_key=settings.anthropic_api_key)
    else:
        from core.llm import OpenAIClient

        client = OpenAIClient(
            model=model, api_key=settings.llm_api_key, base_url=settings.llm_base_url,
        )
    _llm_cache[model] = client
    return client


def get_fast_llm() -> LLMClient:
    """高频调用用的快速模型（未配置 LLM_FAST_MODEL 时回退主模型）"""
    return get_llm(settings.llm_fast_model or settings.llm_model)


def is_mock_mode() -> bool:
    return isinstance(get_llm(), MockLLMClient)


# ── 会话存储 ───────────────────────────────────────────────────

session_mgr = SessionManager(
    storage_dir=str(settings.project_root / "data" / "sessions")
)
# 活跃会话注册表: session_id → Interviewer（内存态）
INTERVIEWERS: dict[str, Interviewer] = {}

# 跨会话记忆（ChromaDB 不可用时自动降级进程内存储）
# 注意: 懒初始化——装 chromadb 后顶层直接 InterviewMemory() 会加载 embedding
# 模型并触网（HEAD huggingface.co），服务启动即卡死。改为首次用时初始化。
_shared_memory: InterviewMemory | None = None


def get_shared_memory():
    """懒获取跨会话记忆（首次调用时初始化，失败降级进程内）"""
    global _shared_memory
    if _shared_memory is None:
        _shared_memory = InterviewMemory(
            persist_dir=str(settings.project_root / "data" / "memory")
        )
    return _shared_memory


# JD 语义缓存（懒初始化——embedding 模型加载较重，首次用时才建）
_jd_cache = None  # JDSemanticCache | None（字符串注解避免顶层导入）


def get_jd_cache():
    """懒获取 JD 语义缓存（模型加载失败自动降级为无缓存）"""
    global _jd_cache
    if _jd_cache is None:
        from interview.semantic_cache import JDSemanticCache
        try:
            _jd_cache = JDSemanticCache()
        except Exception:
            _jd_cache = None  # 模型不可用 → 无缓存（正常解析）
    return _jd_cache


# 多评委仲裁（懒初始化——双评委 + 分歧仲裁，解决单评委评分偏差）
_multi_judge = None  # MultiJudge | None


def get_multi_judge():
    """懒获取多评委仲裁器（模型不可用/初始化失败自动降级为单评委）"""
    global _multi_judge
    if _multi_judge is None:
        from interview.multi_judge import MultiJudge
        try:
            _multi_judge = MultiJudge(get_fast_llm())
        except Exception:
            _multi_judge = None  # 初始化失败 → 单评委（不中断）
    return _multi_judge

# 能力画像聚合器（跨会话统计强弱项/进步趋势，零 LLM 依赖）
profile_builder = ProfileBuilder(session_mgr)


# ── 工具函数 ───────────────────────────────────────────────────

def extract_pdf_text(file: UploadFile) -> str:
    """用 pypdf 提取 PDF 简历文本"""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise HTTPException(500, "未安装 pypdf，请执行: pip install pypdf")

    content = file.file.read()
    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages).strip()
    if not text:
        raise HTTPException(400, "PDF 无法提取文本（可能是扫描件图片型 PDF）")
    return text


def question_to_dict(q) -> dict:
    """InterviewQuestion → API 返回结构"""
    if q is None:
        return None
    return {
        "id": q.id,
        "type": q.type.value,
        "category": q.category,
        "question": q.question,
        "difficulty": q.difficulty,
        "code": q.code,  # 编程题判题元数据（语言/签名/测试用例），非编程题为 None
    }


def evaluation_to_dict(ev) -> dict | None:
    """EvaluationResult → API 返回结构"""
    if ev is None:
        return None
    return {
        "total_score": ev.total_score,
        "level": ev.level,
        "correctness": ev.correctness,
        "depth": ev.depth,
        "structure": ev.structure,
        "relevance": ev.relevance,
        "overall_comment": ev.overall_comment,
        "strengths": ev.strengths,
        "weaknesses": ev.weaknesses,
        "keyword_match_rate": ev.keyword_match_rate,
        "matched_points": ev.matched_points,
        "missed_points": ev.missed_points,
        "code_judge": ev.code_judge,  # 编程题判题结果（非编程题为 None）
    }


def report_to_dict(report) -> dict | None:
    """InterviewReport → API 返回结构"""
    if report is None:
        return None
    return {
        "overall_score": report.overall_score,
        "overall_level": report.overall_level,
        "avg_correctness": report.avg_correctness,
        "avg_depth": report.avg_depth,
        "avg_structure": report.avg_structure,
        "avg_relevance": report.avg_relevance,
        "total_questions": report.total_questions,
        "answered_questions": report.answered_questions,
        "follow_up_count": report.follow_up_count,
        "main_strengths": report.main_strengths,
        "main_weaknesses": report.main_weaknesses,
        "improvement_advice": report.improvement_advice,
        "verdict": report.verdict,
        "verdict_reason": report.verdict_reason,
        "details": report.details,
        "reference_answers": report.reference_answers,
    }


def meta_to_dict(meta) -> dict:
    """SessionMeta → 侧边栏列表项结构"""
    return {
        "session_id": meta.session_id,
        "position": meta.position,
        "display_name": meta.display_name,
        "custom_name": meta.custom_name,
        "created_at": meta.created_at,
        "status": meta.status,
        "overall_score": meta.overall_score,
        "pinned": meta.pinned,
        "message_count": 0,  # 列表不加载完整记录，占位
    }


def get_interviewer(session_id: str) -> Interviewer:
    """
    获取 Interviewer：优先内存注册表，其次从磁盘恢复。

    断点恢复: 服务重启后 INTERVIEWERS 为空，从 SessionManager
    加载 interviewer_state 快照重建（Interviewer.from_dict）。
    """
    if session_id in INTERVIEWERS:
        return INTERVIEWERS[session_id]

    record = session_mgr.load(session_id)
    if not record:
        raise HTTPException(404, "会话不存在")
    if record.meta.status != "in_progress":
        raise HTTPException(400, "该面试已结束")
    if not record.interviewer_state:
        raise HTTPException(500, "会话状态快照缺失，无法恢复")

    interviewer = Interviewer.from_dict(
        record.interviewer_state,
        get_fast_llm(),
        memory=get_shared_memory(),
        llm_strong=get_llm(settings.llm_model),
    )
    interviewer.session_id = session_id
    INTERVIEWERS[session_id] = interviewer
    # 内存聊天记录以磁盘为准（服务重启后 RECORD_MESSAGES 为空）
    RECORD_MESSAGES.setdefault(session_id, list(record.messages))
    logger.info(f"从磁盘恢复面试会话 {session_id}")
    return interviewer


def append_metrics_message(session_id: str, interviewer: Interviewer) -> None:
    """面试结束时追加本场统计消息（延迟/token/成本 — 可观测性）"""
    cost = interviewer.session_cost_estimate()
    append_message(session_id, role="assistant", kind="metrics", metrics={
        "timings": interviewer.state.timings,
        "prompt_tokens": cost["prompt_tokens"],
        "completion_tokens": cost["completion_tokens"],
        "cost_yuan": cost["cost_yuan"],
        "evaluate_count": interviewer.state.evaluate_count,
    })


def persist(session_id: str, interviewer: Interviewer) -> None:
    """保存会话: 聊天记录 + 状态快照 + 元数据"""
    record = session_mgr.load(session_id)
    if not record:
        return

    record.interviewer_state = interviewer.to_dict()
    record.messages = RECORD_MESSAGES[session_id]
    record.meta.answered_count = len(interviewer.state.answers)
    record.meta.question_count = len(interviewer.state.plan.questions)
    if interviewer.state.is_finished:
        record.meta.status = "completed"
    session_mgr.save(record)


# 聊天记录注册表（内存，保存时写入 SessionRecord.messages）
RECORD_MESSAGES: dict[str, list[dict]] = {}


def append_message(session_id: str, **msg) -> None:
    RECORD_MESSAGES.setdefault(session_id, []).append(msg)


def _sse(event: str, data: dict) -> str:
    """构造一条 SSE 消息（event + JSON data）"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── API: 面试生命周期 ─────────────────────────────────────────

@app.post("/api/interviews")
async def create_interview(
    text: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    """
    创建面试会话。

    输入: 简历 PDF 文件 或 文本（简历/岗位 JD）。
    流程: 提取文本 → Interviewer.start（解析+出题+暖场）→ 第一题。
    """
    if file and file.filename:
        content = extract_pdf_text(file)
    elif text and text.strip():
        content = text.strip()
    else:
        raise HTTPException(400, "请上传 PDF 简历或粘贴文本")

    if len(content) < 10:
        raise HTTPException(400, "内容太短，请提供完整的简历或 JD")

    # 创建 Interviewer 并开始
    interviewer = Interviewer(
        get_fast_llm(),                      # 高频调用: 评估/JD/暖场/出题
        memory=get_shared_memory(),
        llm_strong=get_llm(settings.llm_model),  # 最终报告: 强模型
        defer_report=True,                   # 报告由 SSE 流式生成
        jd_cache=get_jd_cache(),             # JD 语义缓存（相似 JD 复用解析结果）
        multi_judge=get_multi_judge(),       # 多评委仲裁（双评委+分歧裁决，压评分偏差）
    )
    try:
        turn_start = await interviewer.start(content)
        warmup_text = turn_start.message
        turn = await interviewer.next_question()
    except Exception as e:
        raise HTTPException(500, f"面试初始化失败: {e}")

    # 弱项注入：把历史能力画像的弱项并入 memory_hints，
    # 使评估/追问对候选人的历史短板重点验证（"翻旧账"升级为"画像驱动"）
    try:
        weakest = profile_builder.build().weakest
        if weakest:
            interviewer.state.memory_hints.append(
                f"历史弱项（能力画像）：{'、'.join(weakest)}"
            )
    except Exception:
        pass  # 画像失败不影响面试启动

    # 岗位名: LLM 解析 > 求职意向正则 > 岗位关键词猜测 > 候选人
    position = interviewer.state.jd_analysis.position or "候选人"

    # 建立会话
    record = session_mgr.create_session(
        position=position,
        jd_text=content,
        tags=interviewer.state.jd_analysis.all_skills[:8],
    )
    session_id = record.meta.session_id
    interviewer.session_id = session_id  # 回填会话 ID（记忆元数据标记用）
    session_mgr.save(record)  # 初始化磁盘文件（persist 依赖 load）
    INTERVIEWERS[session_id] = interviewer
    RECORD_MESSAGES[session_id] = []

    # 记录暖场 + 第一题
    append_message(session_id, role="assistant", kind="warmup", content=warmup_text)
    append_message(session_id, role="assistant", kind="question",
                   content=turn.question.question, question=question_to_dict(turn.question),
                   progress=turn.progress)
    persist(session_id, interviewer)

    return {
        "session_id": session_id,
        "position": position,
        "mock": is_mock_mode(),
        "messages": RECORD_MESSAGES[session_id],
    }


@app.post("/api/interviews/{session_id}/answer")
async def submit_answer(session_id: str, payload: dict):
    """提交回答 → 返回评估 + 追问/下一题/报告"""
    interviewer = get_interviewer(session_id)
    answer = (payload.get("answer") or "").strip()
    if not answer:
        raise HTTPException(400, "回答不能为空")

    append_message(session_id, role="user", kind="answer", content=answer)

    try:
        turn = await interviewer.submit_answer(answer)
    except Exception as e:
        raise HTTPException(500, f"评估失败: {e}")

    evaluation = evaluation_to_dict(turn.evaluation)
    report = report_to_dict(turn.report)
    # 延迟报告: 面试结束但报告未内联生成 → 前端转 SSE 流式拉取
    stream_report = turn.is_finished and report is None

    if evaluation:
        append_message(session_id, role="assistant", kind="evaluation", content="", evaluation=evaluation)

    if report:
        append_message(session_id, role="assistant", kind="report", content="", report=report)
        append_metrics_message(session_id, interviewer)
    elif turn.phase.value == "follow_up":
        append_message(session_id, role="assistant", kind="follow_up",
                       content=turn.message, progress=turn.progress)
    elif turn.question is not None:
        append_message(session_id, role="assistant", kind="question",
                       content=turn.question.question, question=question_to_dict(turn.question),
                       progress=turn.progress)

    persist(session_id, interviewer)

    return {
        "evaluation": evaluation,
        "report": report,
        "question": question_to_dict(turn.question),
        "phase": turn.phase.value,
        "message": turn.message if turn.phase.value == "follow_up" else "",
        "progress": turn.progress,
        "is_finished": turn.is_finished,
        "stream_report": stream_report,
    }


@app.post("/api/interviews/{session_id}/skip")
async def skip_question(session_id: str):
    """跳过当前题目"""
    interviewer = get_interviewer(session_id)
    append_message(session_id, role="user", kind="answer", content="（跳过此题）")
    turn = await interviewer.skip_question()

    report = report_to_dict(turn.report)
    stream_report = turn.is_finished and report is None
    if report:
        append_message(session_id, role="assistant", kind="report", content="", report=report)
        append_metrics_message(session_id, interviewer)
    elif turn.question is not None:
        append_message(session_id, role="assistant", kind="question",
                       content=turn.question.question, question=question_to_dict(turn.question),
                       progress=turn.progress)

    persist(session_id, interviewer)
    return {
        "report": report,
        "question": question_to_dict(turn.question),
        "progress": turn.progress,
        "is_finished": turn.is_finished,
        "stream_report": stream_report,
    }


@app.post("/api/code/run")
async def run_code(payload: dict):
    """
    运行代码（自测，LeetCode 式「运行」按钮）。

    只跑测试用例返回 pass/fail，不推进面试、不评分、不落库。
    前端把题目的判题元数据（language/mode/test_cases）+ 用户代码传来。
    """
    from interview.code_judge import CodeQuestion, TestCase, run_judge

    code = (payload.get("code") or "").strip()
    language = payload.get("language") or "python"
    mode = payload.get("mode") or "core"
    test_cases = payload.get("test_cases") or []

    if not code:
        raise HTTPException(400, "代码不能为空")

    if not test_cases:
        raise HTTPException(
            400,
            "本题暂无自动判题用例（如类设计/SQL/Shell 题），无法自测——"
            "请在面试中提交代码，由 AI 代码评审评估质量",
        )

    question = CodeQuestion(
        id="run", title="", description="", function_signature="",
        example_input="", example_output="",
        test_cases=[TestCase(**tc) for tc in test_cases],
    )
    judge = await run_judge(code, question, language=language, mode=mode)

    return {
        "passed": judge.passed,
        "total_tests": judge.total_tests,
        "passed_tests": judge.passed_tests,
        "failed_tests": judge.failed_tests,
        "errors": judge.errors,
        "details": judge.details,
        "stderr": judge.stderr,
    }


@app.get("/api/interviews/{session_id}/report/stream")
async def stream_report(session_id: str):
    """
    流式生成最终报告（SSE）。

    面试结束后（/answer 返回 stream_report=true）前端打开此连接，
    报告文字经 LLM 流式逐字返回（评估 JSON 保持整块，不流式）。

    事件:
        stats  — 确定性统计部分（分数/等级/逐题），先到，可立即渲染
        delta  — LLM 叙事逐字增量（改进建议+结论理由）
        done   — 完整报告 + 已持久化
    """
    interviewer = get_interviewer(session_id)

    async def event_stream():
        async for event in interviewer.stream_report():
            etype = event["type"]
            if etype == "stats":
                yield _sse("stats", {"report": report_to_dict(event["report"])})
            elif etype == "delta":
                yield _sse("delta", {"text": event["text"]})
            elif etype == "done":
                report = event["report"]
                append_message(
                    session_id, role="assistant", kind="report",
                    content="", report=report_to_dict(report),
                )
                append_metrics_message(session_id, interviewer)
                persist(session_id, interviewer)
                yield _sse("done", {"report": report_to_dict(report)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── API: 历史会话管理 ─────────────────────────────────────────

@app.get("/api/interviews")
async def list_interviews():
    """侧边栏历史列表: 置顶优先，其余按时间倒序"""
    metas = session_mgr.list_sessions(limit=100)
    return {"sessions": [meta_to_dict(m) for m in metas]}


@app.get("/api/profile")
async def ability_profile():
    """
    能力画像：跨会话聚合每个技能分类的强弱项与进步趋势。
    数据来自所有历史会话的答题记录，零 LLM 依赖，随时可查。
    """
    profile = profile_builder.build()
    return profile.to_dict()


@app.get("/api/qa/search")
async def qa_search(q: str = "", top_k: int = 3):
    """面经检索（RAG 检索环节演示）：按关键词/语义相似度返回相关面经"""
    from interview.qa_bank import QaRetriever

    query = q.strip()
    if not query:
        raise HTTPException(400, "缺少查询词 q，例如 /api/qa/search?q=GIL")
    results = QaRetriever().retrieve(query, top_k=top_k)
    return {"query": query, "count": len(results), "results": results}


def _aggregate_stats(sessions: list) -> dict:
    """
    聚合全局用量统计（可测试纯函数）。

    输入 session_mgr.list_sessions() 的 SessionMeta 列表，
    输出聚合指标 + 每会话明细。
    """
    total_prompt = 0
    total_completion = 0
    total_cost = 0.0
    total_latency = 0.0
    completed = 0
    per_session: list[dict] = []

    for meta in sessions:
        record = session_mgr.load(meta.session_id)
        if not record or meta.status != "completed":
            continue
        metrics = record.interviewer_state.get("state", {}).get("metrics", {})
        if not metrics:
            continue
        completed += 1
        s_prompt = s_completion = 0
        s_latency = 0.0
        s_cost = 0.0
        for m in metrics.values():
            s_prompt += m.get("prompt_tokens", 0)
            s_completion += m.get("completion_tokens", 0)
            s_latency += m.get("latency", 0)

        # 成本估算: 按阶段模型分别计价
        for m in metrics.values():
            price = settings.llm_pricing.get(m.get("model", ""), [0, 0])
            s_cost += (
                m.get("prompt_tokens", 0) / 1_000_000 * price[0]
                + m.get("completion_tokens", 0) / 1_000_000 * price[1]
            )

        total_prompt += s_prompt
        total_completion += s_completion
        total_cost += s_cost
        total_latency += s_latency

        per_session.append({
            "session_id": meta.session_id,
            "position": meta.position or "未命名面试",
            "created_at": meta.created_at,
            "overall_score": meta.overall_score,
            "question_count": meta.question_count,
            "prompt_tokens": s_prompt,
            "completion_tokens": s_completion,
            "total_tokens": s_prompt + s_completion,
            "latency_sec": round(s_latency, 1),
            "cost_yuan": round(s_cost, 4),
        })

    return {
        "completed_sessions": completed,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "total_latency_sec": round(total_latency, 1),
        "estimated_cost_yuan": round(total_cost, 4),
        "per_session": per_session,
    }


@app.get("/api/stats")
async def global_stats():
    """
    全局用量统计（可观测性）— 聚合所有已完成面试的
    token 消耗、成本估算、调用耗时。数据来自会话快照，随时可查。
    """
    sessions = session_mgr.list_sessions(limit=100)
    return _aggregate_stats(sessions)


@app.get("/api/interviews/{session_id}")
async def get_interview(session_id: str):
    """加载会话详情（历史查看 / 断点续聊）"""
    record = session_mgr.load(session_id)
    if not record:
        raise HTTPException(404, "会话不存在")

    can_resume = record.meta.status == "in_progress"
    # 在内存 → 优先内存消息（可能比磁盘新）；否则用磁盘记录
    if session_id in INTERVIEWERS:
        interviewer = INTERVIEWERS[session_id]
        messages = RECORD_MESSAGES.get(session_id) or record.messages
        can_resume = not interviewer.state.is_finished
    else:
        messages = record.messages
        # 未完成且有状态快照 → 可续聊（发消息时触发 get_interviewer 延迟重建）

    return {
        "meta": meta_to_dict(record.meta),
        "messages": messages,
        "can_resume": can_resume,
        "mock": is_mock_mode(),
        # 性能指标: 各阶段耗时（内存态优先，磁盘快照兜底）
        "timings": (
            INTERVIEWERS[session_id].state.timings
            if session_id in INTERVIEWERS
            else record.interviewer_state.get("state", {}).get("timings", {})
        ),
    }


@app.patch("/api/interviews/{session_id}")
async def update_interview(session_id: str, payload: dict):
    """重命名 / 置顶"""
    if "custom_name" in payload:
        name = (payload.get("custom_name") or "").strip()
        if not name:
            raise HTTPException(400, "名称不能为空")
        if not session_mgr.rename_session(session_id, name):
            raise HTTPException(404, "会话不存在")
        return {"ok": True}

    if "pinned" in payload:
        if not session_mgr.set_pinned(session_id, bool(payload["pinned"])):
            raise HTTPException(404, "会话不存在")
        return {"ok": True}

    raise HTTPException(400, "不支持的操作")


@app.delete("/api/interviews/{session_id}")
async def delete_interview(session_id: str):
    """删除会话"""
    INTERVIEWERS.pop(session_id, None)
    RECORD_MESSAGES.pop(session_id, None)
    if not session_mgr.delete_session(session_id):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {"ok": True, "provider": settings.llm_provider, "mock": is_mock_mode()}
