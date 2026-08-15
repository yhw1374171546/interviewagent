"""
面试报告 PDF 导出（B3）
======================
用 fpdf2 把 InterviewReport 渲染成 A4 PDF — 离线可测、零网络依赖。

- 中文字体自动探测（simhei/msyh/simsun/文泉驿/Noto，跨平台）
- `build_report_pdf(meta, report) -> bytes` 纯函数，无副作用，可单测
- 字体缺失 → 抛 PdfExportError（HTTP 层转 5xx），绝不静默输出乱码

使用:
    from interview.pdf_report import build_report_pdf
    pdf_bytes = build_report_pdf(meta, report_dict)
"""

from __future__ import annotations

import re
from pathlib import Path

# 中文字体候选（按优先级）: 路径。ttf 优先（fpdf2 对 ttc 集合兼容性依赖版本）
_CJK_FONT_CANDIDATES = [
    "C:/Windows/Fonts/simhei.ttf",                 # Windows 黑体
    "C:/Windows/Fonts/msyh.ttc",                   # Windows 微软雅黑
    "C:/Windows/Fonts/simsun.ttc",                 # Windows 宋体
    "C:/Windows/Fonts/Deng.ttf",                   # Windows 等线
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # Linux 文泉驿正黑
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux Noto
    "/System/Library/Fonts/PingFang.ttc",          # macOS 苹方
]

# Emoji 范围（CJK 字体普遍缺字形，PDF 里会渲染成豆腐块 → 生成前剥离）
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # 杂项符号和象形文字（👍💡📖 等）
    "\U00002600-\U000027BF"  # 杂项符号（⚠❌✅ 等）
    "\u2B50\u2728\u2764\u2714\u2716\u2705\u274C\u2753\u2757\u270D\u274E"
    "]"
)


def _strip_emoji(text: str) -> str:
    """剥离 emoji（PDF 正式文档用纯文本，避免字体缺字形警告/豆腐块）"""
    return _EMOJI_RE.sub("", text)

_font_cache: str | None | bool = False  # False=未探测, None=探测过但无


class PdfExportError(RuntimeError):
    """PDF 导出失败（中文字体缺失等）"""


def find_cjk_font() -> str | None:
    """探测 fpdf2 可加载的中文字体文件路径（结果缓存，进程内只探测一次）"""
    global _font_cache
    if _font_cache is not False:
        return _font_cache

    from fpdf import FPDF  # 懒导入（fpdf2 缺失时不影响其他功能）

    for path in _CJK_FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            probe = FPDF()
            probe.add_font("cjk", "", path)
            _font_cache = path
            return path
        except Exception:
            continue  # 该字体不可加载（如 ttc 兼容问题）→ 试下一个
    _font_cache = None
    return None


class _ReportPDF:
    """A4 报告排版（fpdf2 封装，保持 build_report_pdf 纯函数签名简洁）"""

    def __init__(self, font_path: str):
        from fpdf import FPDF

        class _Pdf(FPDF):
            def footer(self):
                self.set_y(-15)
                self.set_font("cjk", size=8)
                self.set_text_color(150, 150, 150)
                self.set_x(12)
                self.cell(0, 10, f"第 {self.page_no()} 页", align="C")

        self.pdf = _Pdf(format="A4")
        self.pdf.set_auto_page_break(auto=True, margin=18)
        self.pdf.add_font("cjk", "", font_path)
        try:
            # style="B" 需要独立注册的粗体变体；无粗体字体时用同一文件（黑体自带粗感）
            self.pdf.add_font("cjk", "B", font_path)
        except Exception:
            pass
        self.pdf.set_font("cjk", size=11)

    # ── 排版原语 ──

    def heading(self, text: str, size: int = 13):
        self.pdf.set_font("cjk", size=size)
        self.pdf.set_text_color(24, 60, 110)
        self.pdf.cell(0, 8, _strip_emoji(text), new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.ln(1)

    def para(self, text: str, size: int = 11, color: tuple[int, int, int] | None = None):
        text = _strip_emoji(text)
        self.pdf.set_font("cjk", size=size)
        if color:
            self.pdf.set_text_color(*color)
        # 注意: multi_cell 默认 new_x="RIGHT" — 调用后光标停在行尾，
        # 下次 multi_cell(0,...) 可用宽度≈0 会抛"Not enough horizontal space"
        self.pdf.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_text_color(0, 0, 0)

    def kv(self, label: str, value: str):
        label, value = _strip_emoji(label), _strip_emoji(value)
        self.pdf.set_font("cjk", size=11)
        self.pdf.set_text_color(90, 90, 90)
        self.pdf.cell(24, 6, label)
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")

    def line(self):
        self.pdf.set_draw_color(200, 210, 225)
        self.pdf.line(12, self.pdf.get_y(), 198, self.pdf.get_y())
        self.pdf.ln(2)

    def block(self, title: str, body: str):
        title, body = _strip_emoji(title), _strip_emoji(body)
        self.pdf.set_font("cjk", size=11, style="B")
        self.pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_font("cjk", size=10.5)
        self.pdf.multi_cell(0, 5.5, body, new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(1.5)

    def bullet_list(self, title: str, items: list[str]):
        if not items:
            return
        title = _strip_emoji(title)
        self.pdf.set_font("cjk", size=11, style="B")
        self.pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_font("cjk", size=10.5)
        for item in items:
            self.pdf.multi_cell(0, 5.5, f"- {_strip_emoji(item)}", new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(1.5)


def build_report_pdf(meta: dict, report: dict, font_path: str | None = None) -> bytes:
    """
    生成 PDF 报告字节流。

    Args:
        meta: {"position", "session_id", "created_at"}
        report: report_to_dict(InterviewReport) 输出结构
            （overall_score/overall_level/维度分/details/优劣势/改进建议/结论…）
        font_path: 中文字体路径；None 时自动探测

    Returns:
        PDF 文件字节流（可直接作为下载内容返回）

    Raises:
        PdfExportError: 无可用中文字体
    """
    font = font_path or find_cjk_font()
    if font is None:
        raise PdfExportError(
            "未找到可用的中文字体（simhei/msyh/simsun），无法生成 PDF 报告"
        )

    p = _ReportPDF(font)
    pdf = p.pdf

    # ── 第一页: 标题 + 总评 ──
    pdf.add_page()
    pdf.set_font("cjk", size=20)
    pdf.cell(0, 12, "面试评估报告", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(3)

    p.para(f"岗位: {meta.get('position') or '未命名面试'}", size=10, color=(100, 100, 100))
    p.para(f"面试编号: {meta.get('session_id', '')}  |  时间: {meta.get('created_at', '')}",
           size=10, color=(100, 100, 100))
    pdf.ln(2)
    p.line()

    # 总评
    score = report.get("overall_score", 0)
    p.heading(f"总评 {score}/10  {report.get('overall_level', '')}")
    verdict = report.get("verdict", "")
    if verdict:
        p.para(f"结论: {verdict}")
    if report.get("verdict_reason"):
        p.para(report["verdict_reason"], size=10.5, color=(80, 80, 80))
    pdf.ln(1)

    # 维度分
    p.heading("分维度得分")
    dims = [
        ("正确性", report.get("avg_correctness")),
        ("深度", report.get("avg_depth")),
        ("结构", report.get("avg_structure")),
        ("相关性", report.get("avg_relevance")),
    ]
    pdf.set_font("cjk", size=11)
    dim_text = "    ".join(f"{name} {v}" for name, v in dims)
    p.para(dim_text)
    p.para(
        f"题目数: {report.get('total_questions', 0)}  |  已答: {report.get('answered_questions', 0)}"
        f"  |  追问: {report.get('follow_up_count', 0)}",
        size=10, color=(100, 100, 100),
    )
    pdf.ln(1)
    p.line()

    # 优劣势 + 改进建议
    p.bullet_list("主要优势", report.get("main_strengths", []))
    p.bullet_list("待提升", report.get("main_weaknesses", []))
    if report.get("improvement_advice"):
        p.block("改进建议", report["improvement_advice"])

    # ── 逐题详情 ──
    p.heading("逐题详情")
    details = report.get("details") or []
    for i, d in enumerate(details, 1):
        title = f"第 {i} 题  [{d.get('score', 0)}/10  {d.get('level', '')}]"
        if i > 1 and pdf.get_y() > 235:
            pdf.add_page()
        p.block(title, f"题目: {d.get('question', '')}")
        if d.get("answer_preview"):
            p.para(f"回答: {d['answer_preview']}", size=10, color=(70, 70, 70))
        if d.get("comment"):
            p.para(f"评语: {d['comment']}", size=10)
        points = d.get("expected_points") or []
        if points:
            p.para(f"要点: {'、'.join(str(x) for x in points)}", size=10, color=(100, 100, 100))
        pdf.ln(2)

    # ── 参考答案 ──
    refs = report.get("reference_answers") or []
    if refs:
        if pdf.get_y() > 220:
            pdf.add_page()
        p.heading("参考答案（复盘）")
        for r in refs:
            p.block(f"Q: {r.get('question', '')}", f"A: {r.get('answer', '')}")

    return pdf.output()
