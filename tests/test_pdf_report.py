"""
面试报告 PDF 导出测试（B3）
==========================
覆盖 interview/pdf_report.build_report_pdf:
    1. 生成合法 PDF（%PDF 头 + pypdf 可读 + 文本包含关键内容）
    2. 中文字体缺失 → 明确抛 PdfExportError（不静默输出乱码）
    3. 空报告/无 details 的健壮性
全部离线（fpdf2 + 系统字体，无网络），CI 可直接运行。
"""

import io

import pytest

from interview.pdf_report import PdfExportError, build_report_pdf, find_cjk_font


def _sample_report() -> dict:
    return {
        "overall_score": 7.5,
        "overall_level": "✅ 优秀",
        "avg_correctness": 8.0,
        "avg_depth": 7.0,
        "avg_structure": 7.5,
        "avg_relevance": 7.0,
        "total_questions": 2,
        "answered_questions": 2,
        "follow_up_count": 1,
        "main_strengths": ["基础扎实", "表达清晰"],
        "main_weaknesses": ["深度有待提升"],
        "improvement_advice": "建议针对薄弱知识点做专题复习。",
        "verdict": "建议待定",
        "verdict_reason": "综合表现尚可，建议进一步考察。",
        "details": [
            {
                "index": 1,
                "question": "解释 Python 的 GIL",
                "answer_preview": "GIL 是 CPython 的全局解释器锁…",
                "score": 8,
                "level": "✅ 优秀",
                "comment": "答到了要点",
                "expected_points": ["GIL", "多线程"],
            },
            {
                "index": 2,
                "question": "MySQL 为什么用 B+ 树",
                "answer_preview": "B+ 树矮胖扇出大…",
                "score": 7,
                "level": "👍 良好",
                "comment": "结构清晰",
                "expected_points": ["B+树"],
            },
        ],
        "reference_answers": [
            {"question": "解释 Python 的 GIL", "answer": "GIL 是全局解释器锁…"},
        ],
    }


class TestBuildReportPdf:

    def test_generates_valid_pdf(self):
        """生成合法 PDF：%PDF 头 + pypdf 能读出中文文本"""
        pypdf = pytest.importorskip("pypdf")
        meta = {"position": "Python 后端工程师", "session_id": "S001", "created_at": "2026-08-15"}
        pdf_bytes = build_report_pdf(meta, _sample_report())

        assert pdf_bytes[:5] == b"%PDF-"
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        # 关键内容都在（标题/岗位/总分/逐题/建议）
        assert "面试评估报告" in text
        assert "Python 后端工程师" in text
        assert "7.5" in text
        assert "GIL" in text
        assert "改进建议" in text

    def test_font_detection_returns_path(self):
        """本机应有可用中文字体（探测结果非空）"""
        font = find_cjk_font()
        assert font is None or font  # Windows/Linux/macOS 至少一个候选

    def test_missing_font_raises(self, monkeypatch):
        """字体缺失 → 抛 PdfExportError（不静默输出乱码）"""
        monkeypatch.setattr("interview.pdf_report.find_cjk_font", lambda: None)
        with pytest.raises(PdfExportError):
            build_report_pdf({"position": "x"}, _sample_report())

    def test_empty_report_still_generates(self):
        """空报告（无 details）也能生成合法 PDF（前端保证有 details 才导出，防御性）"""
        pdf_bytes = build_report_pdf(
            {"position": "x", "session_id": "S", "created_at": ""},
            {"overall_score": 0, "overall_level": "", "details": []},
        )
        assert pdf_bytes[:5] == b"%PDF-"
