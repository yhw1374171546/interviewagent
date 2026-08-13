"""
面试模拟模块
============

核心组件:
- JDParser:         JD 解析 (规则匹配 + LLM 兜底)
- QuestionGenerator: 题目生成 (题库检索 + LLM 适配)
- AnswerEvaluator:  答案评估 (关键词匹配 + LLM 深度分析)
- CodeJudge:        代码判题 (AST 白名单 + 沙箱执行 + 测试用例)
- Interviewer:      面试主控 (状态机)
- ReportGenerator:  报告生成

注意: 使用懒加载避免不依赖 openai 的模块（skill_taxonomy, question_bank,
code_judge）被导入时触发连锁导入。
"""

# 无依赖模块 — 直接导出
from .code_judge import (
    PRESET_CODE_QUESTIONS,
    CodeQuestion,
    JudgeResult,
    audit_code_safety,
    format_judge_report,
    run_judge,
)
from .question_bank import QUESTION_BANK, BankQuestion, QuestionBankRetriever, QuestionType
from .skill_taxonomy import SKILL_TAXONOMY, get_skill_coverage_report, rule_based_extract

# 有 LLM 依赖的模块 — 懒加载
__lazy_imports__ = {
    "JDParser": ".jd_parser",
    "JDAnalysis": ".jd_parser",
    "QuestionGenerator": ".question_gen",
    "InterviewQuestion": ".question_gen",
    "InterviewPlan": ".question_gen",
    "AnswerEvaluator": ".evaluator",
    "EvaluationResult": ".evaluator",
    "FollowUpDecision": ".evaluator",
    "Interviewer": ".interviewer",
    "ReportGenerator": ".report",
    "InterviewReport": ".report",
    "OutputValidator": ".output_validator",
    "SessionManager": ".session_manager",
    "SessionRecord": ".session_manager",
    "SessionMeta": ".session_manager",
}


def __getattr__(name: str):
    if name in __lazy_imports__:
        import importlib
        mod = importlib.import_module(__lazy_imports__[name], __package__)
        attr = getattr(mod, name)
        # 缓存到模块字典，下次直接命中
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # 无依赖
    "rule_based_extract",
    "SKILL_TAXONOMY",
    "get_skill_coverage_report",
    "QuestionBankRetriever",
    "BankQuestion",
    "QUESTION_BANK",
    "CodeQuestion",
    "JudgeResult",
    "run_judge",
    "format_judge_report",
    "PRESET_CODE_QUESTIONS",
    "audit_code_safety",
    "QuestionType",
    # 懒加载
    "JDParser",
    "JDAnalysis",
    "QuestionGenerator",
    "InterviewQuestion",
    "InterviewPlan",
    "AnswerEvaluator",
    "EvaluationResult",
    "FollowUpDecision",
    "Interviewer",
    "ReportGenerator",
    "InterviewReport",
    "OutputValidator",
    "SessionManager",
    "SessionRecord",
    "SessionMeta",
]
