"""
Prompt 集中管理（D1）
====================
所有 LLM prompt 单一事实来源 — 面试域 + Agent 域，版本化 + A/B 切换。

设计:
    1. 单一事实来源 — 各模块不再内联 prompt，统一从本模块导入
    2. 版本化 — 每个 prompt 带版本号（v1 = 现行文本），历史版本保留可对比/回滚
    3. A/B — `PROMPT_REGISTRY` 记录每个 prompt 的生效版本，
       `set_prompt_version(name, version)` 运行时切换（影响后续调用）
    4. 可测试 — 注册表完整性、占位符校验、A/B 切换不破坏渲染

使用:
    from .prompts import active_prompt, render_prompt
    prompt = active_prompt("deep_eval")           # 取当前生效版本文本
    text = render_prompt("deep_eval", question=q) # 取文本 + 渲染占位符

注意: v1 与重构前各模块内联的 prompt 逐字一致，本轮不改任何 prompt 语义
（纯搬家 + 版本化），行为漂移由测试回归兜底。
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════
#  面试域
# ═══════════════════════════════════════════════════════════════

# warmup — 面试开场白（interview/interviewer.py）
WARMUP_PROMPT_V1 = """你是一位专业的面试官，现在开始一场模拟面试。请根据以下岗位信息，为面试者做一个简短的面试开场介绍。

## 岗位信息
- 岗位: {position}
- 核心要求: {skills}

## 开场白要求
1. 自我介绍（你是什么岗位的面试官）
2. 说明今天的面试流程和大致的题目数量
3. 一句话让面试者放松

请用友好、专业的语气。字数控制在 100 字以内。直接输出开场白，不要写"面试官："之类的角色标注。"""

# jd_fallback — JD 解析 LLM 兜底（interview/jd_parser.py）
JD_FALLBACK_PROMPT_V1 = """你是一位招聘 JD 分析师。以下是一份 JD 中「规则引擎未能自动识别的内容」，请从中提取以下信息，以 JSON 返回:

1. **position**: 岗位名称（如 "高级后端开发工程师"）
2. **domain_knowledge**: 业务领域知识要求（如 "电商"、"金融风控"、"SaaS"）
3. **responsibilities**: 核心工作职责（3-5 条）
4. **interview_focus**: 根据 JD 推断，面试中最应该考察的 3 个方向
5. **missing_skills**: 规则引擎可能漏掉的技术关键词（请用标准名称，如 "PostgreSQL" 而非 "postgres"）

## 未匹配的 JD 文本
{unmatched_text}

## 输出格式
```json
{{
  "position": "",
  "domain_knowledge": [],
  "responsibilities": [],
  "interview_focus": [],
  "missing_skills": []
}}
```"""

# deep_eval — LLM 深度评估（interview/evaluator.py，multi_judge 复用）
DEEP_EVAL_PROMPT_V1 = """你是一位资深面试官。请分析面试者对以下问题的回答质量。

## 题目
{question}

## 期望回答要点
{expected_points}

## 面试者回答
{answer}

## 分析要求

1. **深度分析**: 回答是停留在表面还是深入了原理？举例: 如果问 GIL，只说"全局解释器锁"是表面，讲清楚为什么设计 GIL、什么时候是瓶颈、怎么绕过，才算有深度。

2. **结构分析**: 回答是否有逻辑层次？（总分总？先结论后展开？还是想到哪说到哪？）

3. **追问决策**:
   - 回答很短/太浅 → "deepen"
   - 有明显错误或漏洞 → "challenge"，生成一个具体的挑战性问题
   - 回答很好 → "upgrade"，出一个更难的相关问题
   - 过于抽象没有例子 → "example"
   - 回答充分 → "move_on"

4. **评价**: 用一句话总结，同时指出一个亮点和一个不足。

## 输出格式
```json
{{
  "depth_level": "表面|较浅|适中|深入|非常深入",
  "structure_level": "混乱|松散|一般|清晰|优秀",
  "overall_comment": "一句话评价",
  "strengths": ["亮点"],
  "weaknesses": ["不足"],
  "follow_up_decision": "deepen|challenge|upgrade|example|move_on",
  "follow_up_question": "追问内容(move_on 时为空)",
  "follow_up_reason": "追问原因"
}}
```"""

# deep_eval v2 — 结构化思维链（CoT）版本（P0: 评估可解释性）
# 与 v1 差异: ① 先逐步分析再下结论 ② 输出增加 analysis 字段（推理过程），
# 配合 reasoning_content 落库，评分可解释、可追溯
DEEP_EVAL_PROMPT_V2 = """你是一位资深面试官。请分析面试者对以下问题的回答质量。

## 题目
{question}

## 期望回答要点
{expected_points}

## 面试者回答
{answer}

## 分析要求

1. **逐步分析（先想清楚再下结论）**: 先逐条核对回答是否覆盖期望要点、是否深入原理、有无明显错误或漏洞，再据此判断深度/结构等级。不要凭「看起来不错」直接给分。

2. **深度分析**: 回答是停留在表面还是深入了原理？举例: 如果问 GIL，只说"全局解释器锁"是表面，讲清楚为什么设计 GIL、什么时候是瓶颈、怎么绕过，才算有深度。

3. **结构分析**: 回答是否有逻辑层次？（总分总？先结论后展开？还是想到哪说到哪？）

4. **追问决策**:
   - 回答很短/太浅 → "deepen"
   - 有明显错误或漏洞 → "challenge"，生成一个具体的挑战性问题
   - 回答很好 → "upgrade"，出一个更难的相关问题
   - 过于抽象没有例子 → "example"
   - 回答充分 → "move_on"

5. **评价**: 用一句话总结，同时指出一个亮点和一个不足。

## 输出格式
请以 JSON 返回，**analysis 字段先写你的逐步分析过程**，再给出结论字段:
```json
{{
  "analysis": "逐步分析: 要点覆盖情况、深度/结构判断依据、发现的错误或漏洞、追问价值判断",
  "depth_level": "表面|较浅|适中|深入|非常深入",
  "structure_level": "混乱|松散|一般|清晰|优秀",
  "overall_comment": "一句话评价",
  "strengths": ["亮点"],
  "weaknesses": ["不足"],
  "follow_up_decision": "deepen|challenge|upgrade|example|move_on",
  "follow_up_question": "追问内容(move_on 时为空)",
  "follow_up_reason": "追问原因"
}}
```"""

# code_review — 无判题用例代码题的 AI 代码评审（interview/evaluator.py）
CODE_REVIEW_PROMPT_V1 = """你是一位资深算法面试官。这是一次**代码评审**，请评审面试者提交的代码质量。
这道题没有自动判题用例，需要你基于代码本身判断正确性。

## 题目
{question}

## 面试者提交的代码
```python
{answer}
```

## 评审要求

1. **正确性**（最重要）: 算法/逻辑是否正确地解决了题目？有没有明显 bug、边界遗漏、死循环？
2. **复杂度**: 时间/空间复杂度是否合理？
3. **代码质量**: 可读性、命名、是否硬编码、是否处理了空输入等边界情况？

## 输出格式
```json
{{
  "correctness": "数字 1-10",
  "overall_comment": "一句话评价（代码对不对、好在哪、哪里要改）",
  "strengths": ["亮点"],
  "weaknesses": ["不足"],
  "follow_up_decision": "move_on|deepen",
  "follow_up_question": "追问内容(move_on 时为空)",
  "follow_up_reason": "追问原因"
}}
```

评分参考: 10=完全正确且优雅；8-9=思路正确小瑕疵；6-7=思路基本对但有问题；4-5=方向对但实现有明显缺陷；1-3=严重错误或几乎没写。"""

# follow_up_agent — 追问自主决策 Agent（interview/follow_up_agent.py）
FOLLOW_UP_AGENT_PROMPT_V1 = """你是一位资深面试官，正在对候选人进行追问。请根据以下信息，自主判断是否继续追问，以及追问什么。

## 当前题目
{question}

## 候选人的回答
{answer}

## 评估参考
- 关键词命中率: {match_rate:.0%}
- 评语: {comment}
- 已命中要点: {matched}
- 未命中要点: {missed}

## 本轮已追问过的问题（不要重复）
{asked_follow_ups}

## 你的任务
1. **判断**：这个回答是否已经考察得足够充分？还有没有值得深挖的价值点（如回答有漏洞、未覆盖关键要点、可以挑战其方案、可以要求举实际例子）？
2. 若还有价值点 → 生成一个**具体、贴题**的追问（优先围绕「未命中要点」）
3. 若已充分 → 停止追问

## 输出格式
```json
{{
  "continue": true,
  "question": "追问内容",
  "reason": "追问理由"
}}
```"""

# final_report — 完整面试报告（interview/report.py，非流式）
FINAL_REPORT_PROMPT_V1 = """你是一位资深面试官，请根据以下面试记录，生成一份专业的面试评估报告。

## 岗位信息
- 岗位: {position}
- 核心要求: {skills}

## 面试记录
{interview_log}

## 要求
请以 JSON 格式返回面试报告，包含以下内容:

1. **overall_score**: 综合评分 (1-10)，根据所有题目的加权表现
2. **overall_level**: 等级评定（卓越/优秀/良好/一般/需提升）
3. **main_strengths**: 面试者的 3 个主要优势
4. **main_weaknesses**: 面试者最需要提升的 3 个方面
5. **improvement_advice**: 具体的改进建议（200 字左右）
6. **verdict**: 面试结论（推荐通过 / 建议待定 / 不推荐通过）
7. **verdict_reason**: 结论理由（100 字左右）
8. **reference_answers**: 每道题的参考答案（每题 1-2 句，覆盖该题关键知识点，供面试者复盘）

## 输出格式
```json
{{
  "overall_score": 7.5,
  "overall_level": "优秀",
  "main_strengths": ["优势1", "优势2", "优势3"],
  "main_weaknesses": ["不足1", "不足2", "不足3"],
  "improvement_advice": "具体的改进建议...",
  "verdict": "推荐通过",
  "verdict_reason": "结论理由...",
  "reference_answers": [
    {{"question": "第1题题目", "answer": "参考答案..."}},
    {{"question": "第2题题目", "answer": "参考答案..."}}
  ]
}}
```"""

# stream_report — 流式报告叙事（interview/report.py，SSE 路径）
STREAM_REPORT_PROMPT_V1 = """你是一位资深面试官。请根据以下面试记录，撰写一段针对性的面试复盘建议。

## 岗位信息
- 岗位: {position}
- 核心要求: {skills}

## 面试记录
{interview_log}

## 要求
请用自然流畅的中文写一段「改进建议 + 结论理由」，不要用 JSON、不要标题、不要列表符号：
1. 先给 2-3 条具体、可执行的改进建议（紧扣面试记录中暴露的短板）
2. 再用一句话给出面试结论的理由

字数控制在 200 字左右，直接输出正文。"""

# arbiter — 多评委分歧仲裁（interview/multi_judge.py）
ARBITER_PROMPT_V1 = """你是面试评分仲裁员。两位评委对同一份回答给出了不同评分，请裁决。

## 题目
{question}

## 期望回答要点
{expected_points}

## 面试者回答
{answer}

## 评委 A 的评价（严格视角）
{judge_a}

## 评委 B 的评价（宽容视角）
{judge_b}

## 裁决要求
两位评委的分歧说明该回答存在争议。请结合双方理由，给出一个公正的最终评价。
特别关注: 回答是否真正覆盖了要点、是否深入原理、是否存在明显错误。
不要因为回答"看起来不错"就给高分，也不要因为风格差异扣分。

## 输出格式
请以 JSON 返回，**analysis 字段先写你的裁决依据**（结合两位评委的理由逐条判断），再给出结论:
```json
{{
  "analysis": "裁决依据: 两位评委分歧点分析、对要点的独立核对、最终倾向的理由",
  "depth_level": "表面|较浅|适中|深入|非常深入",
  "structure_level": "混乱|松散|一般|清晰|优秀",
  "overall_comment": "一句话裁决评价",
  "strengths": ["亮点"],
  "weaknesses": ["不足"],
  "follow_up_decision": "deepen|challenge|upgrade|example|move_on",
  "follow_up_question": "追问内容(move_on 时为空)",
  "follow_up_reason": "追问原因"
}}
```"""

# question_customize — 题库题微调（interview/question_gen.py）
QUESTION_CUSTOMIZE_PROMPT_V1 = """你是一位面试官，你需要将以下面试题微调，使其更贴合这个岗位的具体 JD。

## 岗位信息
- 岗位: {position}
- 技术栈: {skills}
- 核心职责: {responsibilities}

## 当前题目
{q_list}

## 微调要求
1. 如果题目的措辞比较泛，请替换为具体的技术名称
2. 场景设计题调整为贴合实际工作场景
3. 不要改变题目的考察目标和难度

## 输出格式
```json
[
  {{"id": "T1", "question": "微调后的题目"}},
  ...
]
```"""

# question_generate — LLM 补充出题（interview/question_gen.py，题库覆盖不足时）
QUESTION_GENERATE_PROMPT_V1 = """你是一位面试官。请为以下岗位生成 {needed} 道面试题。

## 岗位信息
- 岗位: {position}
- 技术栈: {skills}
- 面试重点: {focus}

## 要求
- 优先出「面试重点」方向的题目
- 难度 2-4 之间
- 每道题包含 expected_points（3-5 个期望回答要点）

## 输出格式
```json
[
  {{
    "type": "technical|scenario|project|behavioral|coding",
    "category": "分类",
    "question": "题目",
    "expected_points": ["要点1", "要点2"],
    "difficulty": 3
  }}
]
```"""

# ═══════════════════════════════════════════════════════════════
#  Agent 域（agents/coder.py、agents/research.py 的系统提示词）
# ═══════════════════════════════════════════════════════════════

AGENT_CODER_PROMPT_V1 = """你是一位专业的编程助手。你可以编写和测试代码来解决用户的问题。

工作流程:
1. 仔细理解用户的编程需求
2. 使用 read_file 了解现有代码结构
3. 编写解决方案代码
4. 使用 run_python 测试代码正确性
5. 使用 write_file 保存最终代码
6. 给出代码说明，包括:
   - 代码功能概述
   - 使用方法
   - 复杂度分析（如适用）
   - 边界情况说明

注意事项:
- 代码要清晰可读，有必要的注释
- 优先考虑代码的健壮性和可维护性
- 测试要覆盖正常情况和边界情况
- 用中文回答
"""

AGENT_RESEARCH_PROMPT_V1 = """你是一位专业的研究助理。你的任务是帮助用户进行深入调研并提供全面、准确的分析报告。

工作流程:
1. 充分理解用户的调研问题
2. 使用 web_search 工具搜索相关信息
3. 如果搜索结果不够详细，使用 fetch_webpage 获取具体页面内容
4. 交叉验证多个来源的信息
5. 使用 write_file 工具将调研报告保存为 Markdown 文件
6. 给出结构化的最终答案，包含:
   - 核心发现
   - 不同角度的观点
   - 信息来源引用

注意事项:
- 始终标注信息来源
- 对不确定的信息要说明
- 用中文回答
"""

# ═══════════════════════════════════════════════════════════════
#  版本注册表 + A/B 切换
# ═══════════════════════════════════════════════════════════════

PROMPT_REGISTRY: dict[str, dict] = {
    "warmup":              {"versions": {"v1": WARMUP_PROMPT_V1},              "active": "v1"},
    "jd_fallback":         {"versions": {"v1": JD_FALLBACK_PROMPT_V1},         "active": "v1"},
    "deep_eval":           {"versions": {"v1": DEEP_EVAL_PROMPT_V1, "v2": DEEP_EVAL_PROMPT_V2}, "active": "v2"},
    "code_review":         {"versions": {"v1": CODE_REVIEW_PROMPT_V1},         "active": "v1"},
    "follow_up_agent":     {"versions": {"v1": FOLLOW_UP_AGENT_PROMPT_V1},     "active": "v1"},
    "final_report":        {"versions": {"v1": FINAL_REPORT_PROMPT_V1},        "active": "v1"},
    "stream_report":       {"versions": {"v1": STREAM_REPORT_PROMPT_V1},       "active": "v1"},
    "arbiter":             {"versions": {"v1": ARBITER_PROMPT_V1},             "active": "v1"},
    "question_customize":  {"versions": {"v1": QUESTION_CUSTOMIZE_PROMPT_V1},  "active": "v1"},
    "question_generate":   {"versions": {"v1": QUESTION_GENERATE_PROMPT_V1},   "active": "v1"},
    "agent_coder":         {"versions": {"v1": AGENT_CODER_PROMPT_V1},         "active": "v1"},
    "agent_research":      {"versions": {"v1": AGENT_RESEARCH_PROMPT_V1},      "active": "v1"},
}


def active_prompt(name: str) -> str:
    """当前生效的 prompt 文本（A/B 切换后实时反映）"""
    entry = PROMPT_REGISTRY[name]
    return entry["versions"][entry["active"]]


def prompt_version(name: str) -> str:
    """当前生效的版本号"""
    return PROMPT_REGISTRY[name]["active"]


def prompt_versions(name: str) -> list[str]:
    """该 prompt 的全部可用版本"""
    return list(PROMPT_REGISTRY[name]["versions"])


def set_prompt_version(name: str, version: str) -> str:
    """
    A/B 切换 prompt 生效版本（运行时生效，影响后续所有调用）。

    Args:
        name: prompt 名（见 PROMPT_REGISTRY）
        version: 目标版本号（必须是已注册的版本）

    Returns:
        切换后的版本号

    Raises:
        KeyError: prompt 名或版本不存在
    """
    entry = PROMPT_REGISTRY[name]
    if version not in entry["versions"]:
        raise KeyError(
            f"prompt '{name}' 无版本 '{version}'，可用: {prompt_versions(name)}"
        )
    entry["active"] = version
    return version


def render_prompt(name: str, **kwargs) -> str:
    """取当前生效 prompt 并渲染占位符（.format），缺占位符时抛 KeyError"""
    return active_prompt(name).format(**kwargs)


# 供 Agent 模块使用的兼容常量（取当前生效版本；agents 不感知版本机制）
CODER_SYSTEM_PROMPT = active_prompt("agent_coder")
RESEARCH_SYSTEM_PROMPT = active_prompt("agent_research")
