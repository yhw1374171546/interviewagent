# 开发日志 (Changelog)

> 按时间正序记录项目每个模块的设计与迭代，保留技术决策上下文，便于面试复习。

---

## 阶段一：基础设施搭建

### 2026-08-12 18:35 | `config/settings.py` — 全局配置中心
**做了什么**: 用 `dataclass` 集中管理所有配置项，通过 `python-dotenv` 加载 `.env` 环境变量。  
**为什么这么做**: 把所有 API Key、模型名、超时参数收敛到一个地方，避免散落在各模块的 `os.getenv()` 调用。后续换模型或调参数只需改一处。  
**关键配置项**: `llm_provider`、`llm_model`、`llm_api_key`、`agent_max_steps`、`memory_max_tokens`

### 2026-08-12 18:35 | `utils/logger.py` — 彩色日志模块
**做了什么**: 终端彩色日志 + 文件持久化，`get_logger(name)` 获取预配置的 logger 实例。  
**为什么自己封装**: Python 标准 logging 配置繁琐，每个模块都要重复写 handler/formatter。一次封装，全项目统一风格（INFO 绿色、WARNING 黄色、ERROR 红色）。

### 2026-08-12 18:35 | `utils/token_counter.py` — Token 用量与成本追踪
**做了什么**: 基于 `tiktoken` 精确统计输入/输出 Token 数，支持按模型单价估算费用。  
**为什么需要**: 面试场景是多轮对话，Token 消耗量大。实时追踪用量有助于后续做成本优化（比如发现某个 prompt 模板特别费 Token）。

### 2026-08-12 18:36 | `memory/context.py` — 滑动窗口 Token 管理
**做了什么**: 基于 `tiktoken` 精确计数的对话上下文管理器，超出 `max_tokens` 时自动淘汰最早的消息（FIFO）。  
**为什么是滑动窗口**: 最简单、最通用的上下文管理策略。单次对话场景（如一次工具调用）足够用。但后续发现在面试多轮追问场景中有缺陷（会误淘汰 system prompt），于是 v2 用 ContextOptimizer 替代。  
**核心方法**: `add(message)` → 自动触发 `_truncate()`，`token_count` 属性返回实时统计。

### 2026-08-12 18:36 | `memory/vector_store.py` — ChromaDB 向量记忆
**做了什么**: 封装 ChromaDB，用 `Sentence-Transformers (all-MiniLM-L6-v2)` 做本地 Embedding，提供 `remember()` 和 `recall()` 接口。  
**为什么用 ChromaDB 而不是 Pinecone/Weaviate**: ChromaDB 是嵌入式数据库，零部署依赖，本地运行，适合个人项目。384 维向量 + HNSW 索引，检索速度足够。  
**为什么用 all-MiniLM-L6-v2**: 轻量(~80MB)、本地运行(无需 API Key)、中英文混合场景表现好、隐私友好。

### 2026-08-12 18:36 | `memory/summarizer.py` — 对话摘要压缩
**做了什么**: 当对话历史超过阈值时，将早期消息发送给 LLM 压缩为一段摘要，作为 system prompt 的一部分注入。  
**为什么需要**: 单纯丢弃旧消息会丢失上下文信息（例如面试前半段的回答可能在后半段被面试官回溯）。摘要能保留关键信息同时大幅降低 Token 占用。  
**压缩策略**: 保留最近 `keep_recent` 条消息不变，更早的消息压缩为 3-5 句摘要。

---

## 阶段二：工具系统与 Agent 框架

### 2026-08-12 18:38 | `tools/base.py` — 工具装饰器与注册中心
**做了什么**: 实现 `@tool` 装饰器 + `ToolRegistry` 注册中心 + 函数签名自动推断 JSON Schema。  
**为什么自己实现而不是用 LangChain 的 `@tool`**: 内核相同（装饰器 → 元数据 → Function Calling Schema），但自己实现更轻量，且面试时能讲清楚"我写了一个装饰器，从函数签名推断 JSON Schema"。  
**Schema 推断逻辑**: `inspect.signature(fn)` → Python type hints (`str`→`"string"`, `int`→`"integer"`) → JSON Schema properties + required 列表。

### 2026-08-12 18:38 | `tools/code_exec.py` — Python 沙箱执行
**做了什么**: 在受限的 `__builtins__` 白名单环境中执行 Python 代码，带超时控制。  
**安全措施**: 只暴露安全内置函数（`print`/`len`/`range`/`list`/`dict`/`json`/`math`等），禁止 `eval`/`exec`/`open`/`__import__`。

### 2026-08-12 18:38 | `tools/search.py` — 网页搜索工具
**做了什么**: DuckDuckGo Instant Answer API 搜索 + HTTP 网页抓取。  
**为什么用 DuckDuckGo 而不是 Google/Bing**: 免费、无需 API Key、零配置。缺点是结果不如 Google 丰富，但对技术调研场景（查文档、查概念）足够。

### 2026-08-12 18:38 | `tools/file_ops.py` — 安全文件操作
**做了什么**: 限制工作目录的 `read_file` 和 `write_file`，防止 Agent 越权访问系统文件。  
**安全策略**: 所有路径 `resolve()` 后检查是否以项目根目录为前缀，否则拒绝。

### 2026-08-12 18:55 | `core/llm.py` v1 — LLM 抽象层
**做了什么**: 实现 `LLMClient` 抽象基类 + `OpenAIClient` + `AnthropicClient`，统一消息格式和 Tool Definition 格式。  
**设计模式**: 适配器模式 (Adapter Pattern) — 对外暴露统一的 `chat(messages, tools)` 接口，内部各自做格式转换。  
**关键差异处理**: Anthropic 的 system 消息需要从 messages 列表中分离出来作为单独参数传递；Anthropic 的工具定义用 `input_schema` 而非 OpenAI 的 `parameters`。

### 2026-08-12 19:00 | `core/agent.py` — ReAct Agent 主循环
**做了什么**: 实现 ReAct (Reasoning + Acting) 模式的 Agent 主循环。  
**为什么是 ReAct**: 面试场景需要 Agent 根据回答动态调用评估/追问/代码执行等工具。ReAct 的 Think → Act → Observe 循环天然适合：LLM 先判断"这个回答需要追问还是跳过"(Think)，然后调用对应的评估工具(Act)，拿到评分后决定下一步(Observe)。  
**核心循环**:
```
while step < max_steps:
    response = await llm.chat(messages, tools=tool_defs)
    if not response.tool_calls:
        return response.content  # 对话结束，给出最终答案
    for tc in response.tool_calls:
        result = await execute_tool(tc)
        messages.append(tool_result)
```
**终止条件**: LLM 不再返回 Tool Call（说明它认为可以给出最终答案了），或达到 `max_steps`（强制 LLM 总结）。

### 2026-08-12 19:05 | `core/orchestrator.py` — 多 Agent 编排器
**做了什么**: 实现三种多 Agent 协作模式：Sequential（串行管道）、Parallel（并行汇总）、Debate（多方辩论 + 裁判裁决）。  
**为什么需要多 Agent**: 单一 Agent 适合简单任务，但面试中报告生成环节可以"多角度评估"（技术视角 + 沟通视角 + 成长潜力视角），并行分析后汇总更全面。  
**三种模式的适用场景**:
- **Sequential**: 代码编写 → 代码审查 → 文档生成（流水线）
- **Parallel**: 多角度分析同一问题（扇出-汇总）
- **Debate**: 方案选型、关键决策（对抗性思考）

### 2026-08-12 19:10 | `main.py` — CLI 入口
**做了什么**: 基于 `Rich` 库的终端交互界面，支持交互式面试(`--jd`)和演示模式(`--test`)。  
**为什么用 Rich**: Rich 提供 `Panel`/`Table`/`Progress`/`Rule` 等终端 UI 组件。四维度打分表用 Rich Table 展示比纯 print 清晰得多，进度条让用户感知"面试正在进行中"。  
**架构分层**: `main.py` 只负责展示和交互，`interviewer.py` 负责业务逻辑。换 Web UI 时 `main.py` 直接替换，业务逻辑不动。

---

## 阶段三：面试核心业务

### 2026-08-12 18:40 | `interview/report.py` — 报告生成
**做了什么**: 分两步生成面试报告 — ① 统计计算（分维度均分、总分、等级，纯代码，确定性强）② LLM 综合分析（优势/不足/建议，需要语言能力）。  
**为什么分开**: 均分和等级用代码算更准确（LLM 可能算错），且零 API 成本；优势/不足/建议需要自然语言生成和逻辑归纳，这是 LLM 的强项。各司其职。

### 2026-08-12 18:42 | `interview/code_judge.py` — 沙箱代码判题
**做了什么**: AST 白名单安全审计 → subprocess 隔离执行 → 测试用例驱动验证（预置 3 道编程题 + 各 3-4 个测试用例）。  
**为什么不能用 LLM 评价代码**: LLM "看起来写得不错"不准确 — 代码正确性是客观的：要么通过测试，要么不通过。真实执行才有说服力。  
**安全四层**: ① AST 节点白名单（~40 种允许节点）② import 模块白名单 ③ 禁止函数列表（`eval`/`exec`/`open`/`os.system`）④ subprocess 超时 + 独立进程隔离。

### 2026-08-12 18:44 | `interview/evaluator.py` — 双引擎答案评估
**做了什么**: 阶段 1 — 关键词匹配引擎（确定性，0 API 调用），阶段 2 — LLM 深度语义分析（仅分析关键词搞不定的：深度、结构、追问决策）。  
**为什么不让 LLM 直接打分**: LLM 有"看起来不错就给高分"的偏差（hallucination-like bias），而且同一个回答每次打分可能不同。把客观部分（关键词命中率 → 正确性）和主观部分（语义 → 深度/结构）分开，前者用代码保证公平和一致性。  
**四维度权重**: correctness(35%) + depth(25%) + structure(20%) + relevance(20%)  
**追问决策树** (五分类):
```
回答过短(≤20字) → deepen: "能展开说说吗？具体是怎么实现的？"
有明显错误/漏洞 → challenge: "如果 QPS 突然涨 10 倍，你的方案还 work 吗？"
回答很好 → upgrade: 出一道更难的相关题
过于抽象 → example: "能举个你实际项目中的例子吗？"
回答充分 → move_on: 进入下一题
```

### 2026-08-12 18:46 | `interview/question_gen.py` + `question_bank.py` — 题目生成
**做了什么**: 80+ 题题库（按技能标签索引）+ 倒排索引检索器 + 分层选择算法 + LLM 微调适配。  
**为什么不是 LLM 凭空出题**: 题库题目是人工设计的，每道题自带 `expected_points`(期望回答要点) 和 `follow_up_hints`(追问提示)，质量可控且一致。LLM 只做"把题干的通用措辞替换为 JD 中的具体技术"这个轻量任务。  
**检索引擎算法**:
1. 精确标签匹配（JD 技能 ∩ 题目标签）→ +3 分
2. 模糊标签匹配（子串包含）→ +1 分
3. 按分数排序 → 分层选择（保证五类题型配额 + 难度分层 2-3 简单 + 4-5 中等 + 1-2 困难）
4. 不够则 LLM 补充
**题库规模**: 38 道技术题 + 场景题 + 项目题 + 行为题 + 代码题，90 个索引标签。

### 2026-08-12 18:48 | `interview/skill_taxonomy.py` — 技能分类知识库
**做了什么**: 200+ 技术关键词 → (标准名称, 领域, 类别) 映射表 + 正则匹配引擎 + 学历/经验/软技能提取器。  
**覆盖领域**: 编程语言(20+)、后端框架(15+)、数据库(17+)、消息队列/中间件(8+)、云原生 DevOps(12+)、前端(15+)、大数据(10+)、AI/ML(10+)、协议/概念(15+)  
**为什么不用 LLM 全部解析**: 规则匹配是确定性的（同样的 JD 永远返回同样的技能列表），速度 <10ms，零成本。LLM 只处理规则覆盖不到的模糊文本（如岗位职责描述、面试重点推断）。

### 2026-08-12 18:48 | `interview/jd_parser.py` — JD 解析器（混合模式 v2）
**做了什么**: 规则引擎优先（覆盖 70-90%）→ LLM 兜底（处理剩余模糊文本）。  
**为什么是混合模式**: 纯规则覆盖率高但无法理解"负责核心业务系统架构设计"这样的职责描述；纯 LLM 成本高且每次结果有波动。混合模式取两者之长：规则做确定性提取，LLM 做语义理解。  
**容错设计**: LLM 调用失败时自动降级 — 岗位名从 JD 中正则匹配，考察重点使用默认推断规则（"核心技术 + 系统设计 + 项目经验"），确保解析不中断。

### 2026-08-12 18:50 | `interview/interviewer.py` — 面试主控（状态机）
**做了什么**: 7 状态状态机管理面试全生命周期。  
**为什么用状态机**: 面试流程有明确的阶段和跳转规则（出题 → 等回答 → 评估 → 追问 or 下一题 → 结束）。状态机能保证任意状态下只执行合法的转换（比如不能在 ANSWER 状态直接跳到 CONCLUSION），避免逻辑 bug。  
**状态机流转**:
```
INIT → WARMUP → QUESTION → WAIT_ANSWER → EVALUATE
                    ↑                          ↓
                    └──── FOLLOW_UP ←──────────┘
                    │                          ↓ (move_on / max_followups)
                    └────────── NEXT_QUESTION ─┘
                                               ↓
                                          CONCLUSION
```
**对 UI 暴露的 API**: `start(jd_text)` → `next_question()` → `submit_answer(answer)` → `skip_question()`。业务逻辑与 UI 层完全解耦。

---

## 阶段四：v3 升级 — 生产级可靠性

### 2026-08-12 19:20 | `interview/output_validator.py` — LLM 输出校验器
**做了什么**: 为 LLM 输出增加四层校验 — JSON 提取 → Schema 校验 → 自动修正 → 重试决策。  
**为什么需要**: LLM 即使被要求"输出 JSON"，也可能返回 markdown 包裹的 JSON、中文引号混入、缺少必填字段、枚举值超出范围。面试评分场景下，一个格式错误可能导致整题评分数据丢失。  
**为 4 种场景定义了独立 Schema**: JD 分析结果、答案评估结果、面试报告、题目列表补充。  
**修正策略**: 缺少必填字段 → FATAL → 让 LLM 重新生成；枚举值超出范围 → WARNING → 自动修正为第一个合法值；数值超出 min/max → 裁剪到边界。

### 2026-08-12 19:30 | `interview/session_manager.py` — 多会话持久化
**做了什么**: 面试会话的完整生命周期管理 — create/save/load/delete/resume/compare。  
**为什么需要**: 之前的项目只能"一次性面试"，数据全在内存。加入会话管理后实现三个新能力：① 面试中断后恢复继续（crash recovery）；② 跨场次进度追踪（"面了几次？平均分多少？"）；③ 多场面试横向对比（"面了 5 次后端岗，薄弱点在哪？"）。  
**双层存储**:
- 磁盘 JSON 文件 (`sessions/{session_id}.json`) — 完整会话数据
- 索引文件 (`sessions/index.json`) — 所有会话的元数据，支持快速列表查询和过滤

### 2026-08-12 19:35 | `core/retry.py` — 重试、熔断与降级
**做了什么**: 指数退避重试 + 熔断器(Circuit Breaker) + 降级链(Fallback Chain) + LLM 专用重试处理器。  
**为什么需要**: LLM API 不是 100% 可靠的 — 限流(429)、超时、500 错误都是常见场景。如果一次 API 故障就让面试中断，用户体验极差。  
**三个组件的协作关系**:
```
请求 → [熔断器检查] → [执行] → 成功? → 返回
                       ↓ 失败
                  [指数退避重试] → 3次都失败? → [降级链]
                                                   ↓
                                    GPT-4o → GPT-4o-mini → 规则引擎
```
**熔断器三态**: CLOSED(正常通过) → OPEN(连续 5 次失败，60s 内直接拒绝) → HALF_OPEN(允许探测，2 次成功后恢复)

### 2026-08-12 19:45 | `core/error_handler.py` — 全局异常处理框架
**做了什么**: 异常四级分级 + 安全执行装饰器(`@safe_execute`) + 面试安全上下文管理器(`InterviewSafeContext`) + 降级策略注册表 + 系统健康检查。  
**异常分级**:
| 级别 | 类型 | 处理方式 | 示例 |
|------|------|---------|------|
| L1 | 瞬态 | 自动重试 | API 限流、超时 |
| L2 | 可降级 | 切换到备选方案 | 主模型不可用 |
| L3 | 可恢复 | 引导用户修正 | JD 格式异常 |
| L4 | 致命 | 保存数据后退出 | 磁盘满、OOM |

**面试安全上下文**: `async with InterviewSafeContext(interviewer)` — 即使在 `submit_answer` 中抛出异常，上下文管理器也会自动保存已完成的答题记录，防止数据全部丢失。

### 2026-08-12 19:40 | `core/llm.py` v3 — LLM 层生产级升级
**做了什么**: 在 v1 适配器基础上新增三个能力：
- **`structured_chat()`**: 自动注入 JSON Schema 到 prompt → 调用 LLM → 用 `output_validator` 校验输出 → 格式错误则自动告诉 LLM 修正（最多 2 次）
- **`stream_chat()`**: 异步生成器，逐 token 返回（OpenAI 用 `stream=True`，Anthropic 用 `messages.stream()`），面试官追问/反馈可以逐字显示，体验更自然
- **`chat_with_retry()`**: 封装 `with_retry` 的统一入口，自动处理重试
- **Prompt Caching**: Anthropic 原生 `cache_control: {"type": "ephemeral"}`，system prompt + 最后 2 条消息标记可缓存。system prompt 在整场面试中不变，缓存命中后输入成本降低 90%

### 2026-08-12 19:55 | `memory/context_optimizer.py` — 上下文优化器 v2
**做了什么**: 从简单 FIFO 滑动窗口升级为"优先级混合保留"策略。  
**为什么 v1 的 FIFO 不够**: 面试场景中，system prompt（定义面试官行为）和 JD 分析（决定出题方向）绝对不能丢。FIFO 按时间淘汰，可能把 system prompt 淘汰掉而保留暖场的废话。  
**v2 的四项策略**:
1. **优先级评分**: System(CRITICAL/100) → JD分析(VERY_HIGH/80) → 当前题(HIGH/60) → 前一道题(MEDIUM/40) → 暖场(LOW/20)
2. **语义分块**: 按 Q&A 对（一道题 + 追问 + 回答 = 一个段）分组，裁剪时不会切断一道题的追问链
3. **混合保留**: 最近 N 个完整段(Recency) + 从老段中挑分数最高的 K 个(Importance) + System Prompt(Always Keep)
4. **自适应预算**: 根据题目难度动态调整 `budget_reserve` — 简单题留 10% 给输出，复杂题留 25%

---

## 阶段五：文档体系

### 2026-08-12 20:15 | `README.md` — 项目主页重写
**做了什么**: 全面更新 README，反映 v3 所有新增模块、架构图和设计亮点列表。  
**为什么重要**: README 是面试官和 HR 看项目的第一眼。清晰的树形结构目录 + 架构图 + 设计亮点速览表，让阅者在 30 秒内理解项目价值。

### 2026-08-12 20:25 | `docs/interview_qa.md` — 13 道面试高频题详解
**做了什么**: 针对秋招面试中项目相关的高频问题，逐题给出完整技术回答（含代码片段和架构图）。  
**为什么需要**: 项目代码写好了不代表面试能讲清楚。这份文档帮你从"我做了什么"升级到"我为什么这么做 + 还能怎么做"。  
**覆盖的 13 题**:
1. Agent 完整流程（ReAct 循环 pseudocode + 面试状态机图）
2. 项目做了哪些优化（成本/性能/质量/可靠性 + 量化数据）
3. Agent 优化常见手段（5 层框架：Prompt → 推理 → 输出 → 基础设施 → 成本）
4. 场景题 — 面试官不追问了怎么办（根因分析 pipeline + 三层 fix）
5. 语义检索实现（Embedding → HNSW → 相似度排序完整链路）
6. 向量数据库三个角色（长期记忆/跨会话积累/题目去重）
7. 为什么不用 LangChain（自我实现 vs 框架 + LangGraph 适用场景）
8. 模型输出四层控制（Prompt → Schema → Validate → Fallback）
9. 三层记忆架构（Working → Short-term → Long-term）
10. 多轮多会话 memory 处理（单场追问/跨场次对比/中断恢复）
11. 分层容错架构 + 异常场景处置矩阵
12. 上下文管理场景题 — 优先级混合保留策略 + 效果对比
13. 关键设计决策 Trade-off 速查表

### 2026-08-12 20:30 | `docs/optimization.md` — 优化手段全记录
**做了什么**: 新建优化指南，按 Prompt / 推理 / 输出控制 / 基础设施 / 成本控制五个维度归档 14 项优化措施，附代码示例和量化效果。  
**核心数据**: 单次面试 API 调用从 14 次降到 12.5 次(-11%)，Token 消耗降低 18%+。  
**后续方向**: 模型路由、语义缓存、RAG 增强、自适应难度。

---

## 阶段六：题库与知识库一致性补齐

### 2026-08-13 16:15 | `interview/question_bank.py` — 题库扩充 38 → 91 题
**问题发现**: 对照 `skill_taxonomy`（8 大领域 200+ 关键词）和 `question_bank`（38 题），发现四个领域"规则引擎能识别、题库却出不了题"：前端、大数据、消息队列、AI/LLM。这类 JD 贴进来后检索全部落空，只能靠 LLM 即兴出题，违背了"题库保证质量"的设计初衷。  
**做了什么**: 按现有 `BankQuestion` 格式（含 `expected_points` 期望回答要点 + `follow_up_hints` 追问提示）补齐 53 道题：

| 领域 | 新增 | 典型题目 |
|------|:---:|---------|
| 前端 | 11 | React diff/虚拟DOM、Vue 响应式、事件循环、webpack vs Vite、首屏优化 |
| 大数据 | 12 | RDD 宽窄依赖、Flink Checkpoint/Watermark、数据倾斜、数仓分层、实时大屏设计 |
| 消息队列 | 8 | Kafka ISR/acks、消息不丢不重、顺序性、积压治理、基于 MQ 的最终一致性 |
| AI/LLM/Agent | 16 | RAG 链路、HNSW 索引、ReAct vs Plan-Execute、Function Calling、上下文工程、多 Agent 协作、LoRA 微调、BPE 分词器（代码题） |
| Java 加深 | 4 | synchronized 锁升级、volatile 内存屏障、AQS、CMS/G1/ZGC 演进 |
| K8s 加深 | 2 | Service 网络与 kube-proxy、StatefulSet 与 PV/PVC |

**验证结果**: 分别用前端/大数据/AI Agent 三类 JD 跑检索，均命中对应领域的专属题（Agent JD 8 题中 6 题为 AI/LLM 专项题）。

### 2026-08-13 16:15 | `interview/skill_taxonomy.py` — 知识库扩充 + 词边界修复
**做了什么**:
1. **新增 24 个关键词**（164 个总计），补齐 AI/LLM 方向（rag、embedding、向量数据库、chromadb、faiss、milvus、llm、大模型、prompt、智能体、多智能体、nlp、微调、知识图谱）和大数据方向（数仓、数据湖、实时计算、离线计算、etl），使题库标签与知识库一一对应。
2. **修复短关键词子串误匹配 bug**: `java` 会命中 `javascript`、`go` 会命中 `django/mongodb`、`rag` 会命中 `storage`。增加词边界检查 `_is_word_match()` —— 前后字符是 ASCII 字母/数字则拒绝匹配。**关键细节**: 只把 ASCII 字母/数字视为词内字符，中文不算，否则 `java后端开发` 中的 `java` 会被 `后`.isalnum() 误杀（Python 中中文字符 isalnum() 为 True）。

**为什么这个 bug 重要**: 前端 JD 会被误提取出 Java 技能 → 题库检索出一堆 Java 题 → 整个面试方向跑偏。这是规则引擎的经典坑（子串匹配 vs 词边界匹配），面试中可讲。

### 2026-08-13 16:15 | `README.md` — 题库规模与覆盖方向更新
**做了什么**: 题库规模 80+ → 90+，设计亮点表补充覆盖方向说明（12 个方向）。

---

## 阶段七：Web Demo（DeepSeek 风格聊天界面）

### 2026-08-13 16:30 | `core/mock_llm.py` — Mock LLM 客户端
**做了什么**: 实现 `LLMClient` 抽象基类的确定性 Mock 实现。  
**为什么需要**: ① Web Demo 在无 API Key 环境也能完整演示面试全流程；② 单元测试不依赖外部 API（确定性 → 可断言）。  
**技术方案**: 按 prompt 内容路由 — 检测到"开场白"返回暖场话术、"follow_up_decision"返回评估 JSON（按回答长度做五分类追问决策）、"overall_score"返回报告 JSON（从面试记录正则提取每题评分算均分）。  
**面试可讲点**: LLMClient 是抽象基类，Mock 是它的一个实现 — 适配器模式 + 依赖注入的体现，换真实模型只需改配置不动业务代码。

### 2026-08-13 16:35 | `interview/session_manager.py` — 置顶/重命名
**做了什么**: SessionMeta 增加 `pinned`（置顶）和 `custom_name`（自定义名）字段，新增 `rename_session()` / `set_pinned()` 方法，`list_sessions()` 排序改为置顶优先 + 时间倒序。  
**为什么这么做**: Web 侧边栏需要 DeepSeek 式的历史管理（置顶/重命名/删除）。排序用两步稳定排序：先按时间倒序，再按 pinned 分组（组内保持时间序）。  
**细节**: 重命名为空串时自动清除自定义名，回退到岗位名展示（`display_name` property 统一处理）。

### 2026-08-13 16:40 | `interview/interviewer.py` — 状态序列化（断点恢复）
**做了什么**: `to_dict()` / `from_dict()` 支持完整面试状态快照的序列化与重建。  
**为什么需要**: Web 场景服务重启后，内存中的 Interviewer 丢失。快照保存到磁盘后，重启时可以从 SessionManager 加载并重建状态机（当前题、已回答记录、评估结果、追问计数全部恢复）。  
**技术方案**: `_jsonable()` 递归把 dataclass/Enum 转为 JSON 安全类型；`from_dict` 反向重建 InterviewQuestion / EvaluationResult / InterviewPhase 等枚举和数据结构。  
**验证**: 用 Mock LLM 跑通「开始 → 答题 → 序列化 → 重建 → 继续答题」全流程 round-trip 测试。

### 2026-08-13 16:45 | `interview/jd_parser.py` — 求职意向岗位提取
**做了什么**: `_guess_position` 优先匹配简历中「求职意向/意向岗位/期望职位」等显式声明，回退到岗位关键词上下文截取。  
**为什么需要**: 简历输入场景（非 JD）下，岗位名通常写在求职意向里，原来的关键词猜测会把"求职意向："前缀也截进去。

### 2026-08-13 16:50 | `web/server.py` — FastAPI 后端
**做了什么**: REST API 全套实现 — 创建面试（PDF 上传/文本）、提交回答、跳过、历史列表、会话详情、重命名/置顶（PATCH）、删除（DELETE）、健康检查。  
**核心设计**:
- **会话注册表 + 持久化双写**: 活跃 Interviewer 在内存（INTERVIEWERS dict），每次 turn 后序列化到磁盘 — 服务重启自动从磁盘恢复未完成面试
- **Mock 降级**: 未配置 API Key 自动使用 MockLLMClient，`/api/health` 暴露 mock 标志给前端显示"演示模式"横幅
- **PDF 解析**: pypdf 提取文本，扫描件（无文本层）给出明确报错
- **聊天记录**: 每次交互追加结构化消息（warmup/question/evaluation/follow_up/report 五种 kind），持久化到 SessionRecord.messages

### 2026-08-13 17:00 | `web/static/` — DeepSeek 风格前端
**做了什么**: 原生 HTML/CSS/JS 三件套（零构建步骤、零框架依赖）。
- **落地页**: 文本粘贴 + PDF 拖拽上传 + 「开始对话」按钮 → 跳转聊天界面
- **聊天页**: 深色侧边栏（#15191E）+ 浅色聊天区 + 蓝色强调（#4D6BFE，DeepSeek 品牌色）
- **侧边栏**: 「开始新面试」按钮 + 历史列表（置顶优先、时间倒序）→ hover 条目右侧弹三点菜单 → 置顶/重命名/删除；重命名为行内输入框（Enter 确认 / Esc 取消），删除有确认弹窗
- **消息渲染**: 五种消息类型卡片 — 题目卡（题型/难度星）、评估卡（四维度进度条 + 亮点/建议标签）、报告卡（总分 + 结论 + 优势/不足）、追问气泡、打字中动画
- **输入区**: Enter 发送 / Shift+Enter 换行 / 跳过按钮 / 面试结束后自动禁用

**为什么不用 React/Vue**: 这个界面只有两个视图 + 一个列表，原生 JS 足够且零构建步骤；用框架反而引入 node 工具链。面试可讲"根据复杂度选技术栈"的权衡。

### 2026-08-13 17:00 | 全链路验证
**做了什么**: TestClient 端到端测试 — 创建面试 → 答题（返回评估卡）→ 历史列表 → 重命名/置顶 → 会话详情 → 跳过 → 删除；以及模拟服务重启（清空内存注册表）→ 从磁盘恢复 → 继续答题。全部通过。

---

## 阶段八：Bug 修复

### 2026-08-13 17:35 | `web/static/style.css` — 弹窗误显示
**现象**: 进入页面就弹出「确认删除记录」弹窗。  
**根因**: CSS 的 `display: flex` 覆盖了 HTML `hidden` 属性的 UA 默认 `display: none` — 弹窗、聊天页、Mock 横幅这些带 display 规则的元素从页面加载起就全部可见（弹窗带遮罩在最上层）。  
**修复**: 全局规则 `[hidden] { display: none !important; }`。  
**经验**: 「HTML 属性 vs CSS 优先级」前端经典坑 — 凡是用 `hidden` 属性控制显隐的元素，若 CSS 里声明了 display，必须显式覆盖。

### 2026-08-13 17:50 | `web/static/app.js` — 输入框禁用无法作答
**现象**: 创建面试后输入框禁用、发送按钮灰色、提示「本场面试已结束」。  
**排查过程**: ① 服务端验证 — 用户会话状态 in_progress、`can_resume: True`，排除后端；② 静态分析全部 JS 逻辑均正确；③ 交叉比对前端所有字段访问和后端返回的 JSON key — 发现 `state.canResume = data.canResume`（camelCase）读取的是 `data.can_resume`（snake_case）→ **永远 undefined → 假值 → 输入框禁用**。  
**根因**: 后端 Python 用 snake_case 命名 JSON 字段，前端 JS 习惯 camelCase — 典型的多语言命名约定不一致 bug。  
**修复**: `state.canResume = data.can_resume`。  
**经验**: ① 前后端字段命名约定要在一开始统一（本项目的约定是 snake_case，`display_name`/`total_score` 等字段因此没踩坑，只有 `can_resume` 一个漏网的）；② 服务端测试通过 ≠ 前端正常 — 端到端测试要覆盖字段名本身，最好用 TS/JSON Schema 做契约校验。

### 2026-08-13 18:10 | `core/mock_llm.py` + `interview/evaluator.py` — 评分恒等问题
**现象**: 两个完全不同的回答（RAG 讲解 vs "GOGOGO" 乱码）拿到**完全相同**的深度 8/10、结构 7/10 和同一条评语。  
**排查**: 复现 mock 的回答提取正则 `## 面试者回答\s*\n(.+?)(?=\n*$)` — lookahead `(?=\n*$)` 只会在**字符串末尾**成功，所以 `.+?` 惰性匹配一路吃到 prompt 结尾，把「## 分析要求」的全部指令文本也截进了 answer。指令文本本身就有 ~500 字 → **任何回答的"长度"都恒大于 200** → 永远走 `深入/清晰` 分支 → 深度 8、结构 7 恒定，与回答内容无关。  
**修复**（两层）:
1. `mock_llm.py` — ① 正则改为截到下一个 `## ` 标题为止 `(?=\n+## |\Z)`；② 评分逻辑从"只看长度"升级为**关键词命中率（对照期望回答要点）+ 长度 + 信息密度**三重规则 — 与真实评估器的确定性引擎同思路，长但不相关的回答不能再拿高深度分（RAG 回答命中 0% → 深度 5 而非 8）；③ 评语/优劣势数据化（"命中 0%，内容偏离了考察方向"）。
2. `evaluator.py` — 确定性层新增**垃圾输入拦截**：重复字符（信息密度 < 0.15）直接短路返回 1 分，不浪费一次 LLM 调用 — 真实 LLM 模式下同样生效（"GOGOGO" 被拦截后不会发给 LLM 评分）。  
**验证**: Go 题 + GOGO spam → 全维度 1 分 + deepen 追问；Go 题 + RAG 长文 → 3.9 分 + challenge 追问（"没有触及核心概念"）— 两种回答得分不同且合理。  
**经验**: ① 正则提取段落内容时用"下一个标题"做边界，别用行尾；② 评分系统必须有"无关内容识别"能力 — 只按长度评分会被冗长跑题的答案钻空子，这是面试官提问"你的评分系统怎么保证公平性"时的好素材；③ 垃圾输入要在最便宜的确定性层拦截。

### 2026-08-13 18:30 | `interview/evaluator.py` — 评估器健壮性加固（7 个边界）
**做了什么**: 按「异常得分测试用例矩阵」系统性加固评估器，新增确定性拦截链 + 2 个真 bug 修复：

**确定性拦截链**（全部 0 API 调用，按顺序短路）:
```
空回答 → 超短(<20字) → 重复字符垃圾 → 复读题目 → [关键词匹配] → [LLM 深度评估]
```

| # | 边界 | 修复 |
|---|------|------|
| A4 | **复读题目原文**（只抄题不回答） | 相关性会因命中题面词虚高 → `_is_question_restate`：回答中 >70% 词来自题目即拦截，正确性/相关性封顶 + 要求重新组织 |
| A5 | **关键词堆砌**（只罗列要点词不加解释） | 极短回答(<60字)命中 ≥60% 要点 → 正确性封顶 6 分 + 提示"缺少展开说明" |
| A6 | **同句重复 N 遍凑字数** | `_is_padded_repetition`：同一句话出现 ≥3 次 → 结构分封顶 4 分 |
| B1 | **expected_points 为空**（真 bug） | 原逻辑直接返回 rate=1.0 → 正确性恒 9 分虚高。改为返回 None → 正确性取中性值 5 |
| C1 | **follow_up_decision 非法枚举**（真 bug） | `FollowUpDecision("skip_bad_value")` 抛 ValueError → submit_answer 崩溃、整场面试挂掉。`_safe_decision()` 安全转换回退 move_on |
| C3/C4 | **LLM 异常/非 JSON 静默降级** | 降级后评语为空（空气泡）→ 现在填"（语义评估暂不可用，按关键词命中评分）" + 按命中率自动决策追问 |
| D1 | **追问文本为空** | decision≠move_on 但文本为空 → `_default_follow_up()` 按决策类型给默认话术 |

**测试**: 新增 `tests/test_evaluator_robustness.py` — 15 个用例覆盖 A/B/C/D 全矩阵，用 FakeLLM 注入异常（非法枚举/抛异常）验证降级路径，**15/15 通过**（pytest，0 API 调用）。

**设计思想（面试可讲）**: 评估器的防御层次 — ① 最便宜的确定性层拦截可判定的异常（垃圾输入/复读/灌水），② LLM 层只处理语义，③ 输出层做枚举安全转换和默认值兜底。任何一层出问题都有下一层接住，评分系统不会因为异常输入或模型幻觉而崩溃或失真。

---

## 阶段九：指标体系（Benchmark + 判题器真 bug 修复）

### 2026-08-13 19:20 | `interview/code_judge.py` — 判题器核心 bug：从不比较输出
**现象**: benchmark 评测判题检出率时发现 3 道题 6 个解法的判题结论只有 3 个正确。  
**根因（真 bug）**: 判题器生成的测试脚本只检查"测试代码是否抛异常"，**从未把实际输出和 `expected` 字段做比较** — 任何不抛异常的错误实现（返回错误结果、算法逻辑错）都会被判通过。demo.py 案例 B 的"bug 实现 4/4 通过"不是测试用例不够全面，而是判题逻辑本身缺失输出比对。  
**修复**:
1. `_build_test_script` — 每个测试用例用 `contextlib.redirect_stdout` 捕获真实输出，与 `expected` 做字符串比较，一致才打 PASS 标记
2. `_parse_test_output` — 解析「期望 X 实际 Y」格式，分别填入 expected/got 字段
3. 修正 LRU 用例 2 的期望值错误（"-1\n-1\n3" → "-1\n2\n3" — 旧值连正确实现都会判失败，注释里作者自己都发现了但没改值）  
**验证**: demo 案例 B 现在正确判为 4/5（新 recency 用例拦截），案例 A 5/5。  
**经验**: 这是比"用例不全"深一层的 bug — 判题器本身的判据错了。面试讲项目时："先做对判据，再谈覆盖度"。

### 2026-08-13 19:40 | `benchmark.py` — 指标基准测试套件
**做了什么**: 离线、确定性、0 API 调用的 benchmark — 用同一套语料/用例对比 v1（优化前配置，由代码模拟）与 v2（当前版本），量化五大指标。  
**为什么用"配置对比"**: Agent 工程类项目没有模型准确率可报，优化收益体现在工程手段上（规则引擎/题库/边界防护）— baseline 就是"不用这些手段的配置"。同一 benchmark 可随时复跑，指标可验证、可写进简历。  
**实测结果**:
| 指标 | v1 | v2 | 提升 |
|------|:---:|:---:|:---:|
| 规则解析覆盖率（8 JD 语料） | 52.4% | 69.8% | +17.4% |
| 题库领域匹配率 | 73% | 92% | +19%（前端 0→100%，大数据 0→100%） |
| 判题缺陷检出率 | 5/6 | 6/6 | 漏检 → 全检出 |
| 评估异常 LLM 调用节省 | 0 | 57% | 确定性层短路 |
| 单场面试 LLM 调用 | ≈7 | 6 | + 输入 token 减 70% |

**过程中又修了一个 benchmark 自己的 bug**: `lru_q = PRESET_CODE_QUESTIONS[0]` 是引用而非拷贝，第一次调用把预设题用例截成 4 个后污染了第二次调用 → `deepcopy` 修复。可变共享状态的坑。

**简历用法**: "自建离线 benchmark 套件，量化优化收益：题库领域匹配率 73%→92%、判题缺陷检出率 83%→100%、评估异常拦截 LLM 调用节省 57%"。

---

## 阶段十：工程化（Git / CI / Lint）

### 2026-08-13 20:00 | git 仓库初始化 + 8 个阶段性提交
**做了什么**: `git init` + 按模块分组的 8 个提交（面试核心链路 → Agent 框架 → 可靠性层 → Web Demo → 测试与指标 → 文档 → 工程化）。  
**安全**: 提交前扫描全仓密钥（`sk-`/`api_key`/`password` 模式）— 零命中；`.env`/`data/`/`logs/` 已 gitignore，仓库内仅含 `.env.example` 占位符模板。

### 2026-08-13 20:10 | `pyproject.toml` + ruff 全仓清理
**做了什么**: ruff（E/F/W/I/UP 规则集）首跑发现 **97 个问题**，逐类修复：
- 79 个自动修复（未使用导入、导入排序、弃用 typing 写法、f-string 无占位符等）
- 3 个 F821 真问题: `question_gen.py` 的 `JDAnalysis` 类型注解未导入 → 移入 TYPE_CHECKING
- `question_bank.py` 重复导入（dataclasses 导了两次）
- `skill_taxonomy.py` 模块中部导入 → 移到顶部
- 循环变量 `field` 遮蔽 dataclasses 导入 → 改名 `field_name`
- `UP042`（StrEnum）按项目统一 `str, Enum` 模式加入 ignore，并注释原因

### 2026-08-13 20:20 | 修复预存在的坏测试 + 规则引擎跨行误判
**问题 1**: `tests/test_agent.py` 的 JD 解析测试从未通过（项目此前从没跑过 pytest）——JD 文本太短不触发 LLM 兜底（阈值 50 字符），且 stub 返回结构是旧版设计（`required_skills` 字段，当前代码读的是 `missing_skills`）。重写测试匹配当前设计。  
**问题 2（真 bug，测试暴露）**: 规则引擎判断「必须 vs 加分技能」用 ±50 字符上下文窗口，会把"了解 Docker 者优先"的影响扩散到同段的其他技能（Python 被误判为加分）。修复: **上下文窗口限定在当前行内**——真实 JD 每条要求独立成行，行内判断准确率更高。  
**结果**: 22 个测试全部通过，CI 从此有真实回归保障。

### 2026-08-13 20:30 | GitHub Actions CI
**做了什么**: `.github/workflows/ci.yml` — push 自动跑 pytest（最小依赖安装，利用 SDK 懒加载）+ benchmark + demo 冒烟 + ruff lint。

---

## 阶段十一：核心链路补全（容错生效 + 记忆接入）

### 2026-08-13 21:10 | `core/retry.py` + `core/llm.py` — 重试层两个真 bug
**做了什么**: 把 interviewer/evaluator/jd_parser/question_gen/report 全部改用 `chat_with_retry()`，重试/熔断/降级从死代码变为实际生效。接入时暴露两个真 bug：  
**Bug 1（严重）**: `with_retry` 用 `iscoroutinefunction(fn)` 判断是否 await — lambda 包裹协程时返回 False，协程被**原样返回从未执行**，整个重试层形同虚设。修复: 调用后 `inspect.isawaitable(result)` 判断。  
**Bug 2**: `chat_with_retry` 位置参数调用 `chat()`，与 `**kwargs` 签名的实现不兼容（TypeError）。修复: 关键字参数调用。  
**测试**: FlakyLLM 注入 429 限流异常，验证自动重试恢复（2 次调用）→ 重试链路可被测试证明。  
**经验**: 容错代码本身需要故障注入测试 — 没有 FlakyLLM 这类测试，重试层坏了都发现不了（此前 mock 全走成功路径，bug 一直潜伏）。

### 2026-08-13 21:40 | `interview/memory_context.py` — 记忆模块接入面试链路
**背景**: 此前 ChromaDB/ContextOptimizer 等"已写未接"——简历写了面试会翻车。本次真正接入两条链路：  
**轮内记忆**: `build_history_summary()` 确定性压缩前几轮 Q/A（类别/得分/弱项，0 API 调用）→ 注入评估 prompt → 追问能"翻旧账"（"你刚才说用了 Kafka，为什么现在又说用 MQ？"）。  
**跨会话记忆**: `InterviewMemory` 每题结束异步写入 (题目/回答/评分/技能标签)，新面试开始时语义检索**历史弱项**（得分<7）注入追问策略提示 → "候选人之前在数据库类题目表现弱，重点验证"。  
**三级降级**: ChromaDB 向量检索 → 进程内兜底存储 → no-op（CI/精简环境不崩）— 记忆是增强功能，绝不阻塞主链路。  
**接入点**: interviewer（评估上下文注入 + 异步记忆写入）/ evaluator（`history_context`、`memory_hints` 参数）/ web（共享记忆实例 + 会话 ID 回填）。  
**测试**: 新增 12 个 — 摘要生成（空/内容/跳过/截断）、prompt 注入验证（捕获 LLM 消息）、降级检索（低分召回/高分排除/空记忆）、全链路离线记忆写入。

---

## 阶段十二：真实 LLM 联调（DeepSeek v4-pro）

### 2026-08-13 22:30 | Provider 配置 + 推理模型适配
**做了什么**:
1. **DeepSeek 接入** — DeepSeek API 是 OpenAI 兼容协议，`OpenAIClient` 自定义 `base_url` 即可使用（架构最初就支持多 Provider，无需改代码）。用 `/models` 接口实测账号可用模型为 `deepseek-v4-flash` / `deepseek-v4-pro`（`deepseek-chat` 别名在账号中不存在 — **模型名以 /models 接口返回为准，不要照抄文档**）。
2. **推理模型适配（联调发现的真问题）** — v4-pro 是推理模型，先输出 `reasoning_content` 再作答：`max_tokens=10` 时 10 个 token 全部被推理过程吃掉，`content` 为空。原预算（评估 800/暖场 300）存在同样风险。全链路预算上调：评估 2000、暖场 600、JD 解析 2000、报告 3000、出题 3000。
3. **全链路实测通过**（Web API + CLI 双路径）:
   - 暖场自然个性化（提到岗位技能、题目数量、安抚话术）
   - challenge 追问质量达到真实面试官水平："channel 阻塞或锁阻塞时，M 还会与 P 解绑吗？"
   - 相关性检测真实生效: 跑题回答被判 3.6 分并指出"完全偏离题目"
   - 报告优劣势分析有理有据

**经验**: ① 真实模型联调是 Mock 永远替代不了的 — 推理模型的 content 空值问题只有真跑才能发现；② 适配推理模型的核心是"给足输出预算"（模型自己会 stop），而不是压缩 prompt；③ 换任何新 Provider 先查 /models 再定配置。

---

## 阶段十三：延迟优化与追问质量（真实用户体验驱动）

### 2026-08-13 23:20 | 模型路由 + 并行化 + 上下文追问兜底
**现象**: 真实使用反馈 — ① 简历解析 51 秒、单次评估 17.6 秒（推理模型 v4-pro 先思考后作答）；② 评估偶尔降级后追问"能展开说说吗？"与题目无关（"乱问"）。  
**根因**: ① 全链路都压在最强的推理模型上；② 出题微调与暖场串行执行；③ LLM 输出偶尔被 max_tokens 截断导致 JSON 解析失败 → 静默降级（无日志）；④ 降级后的追问是通用话术。  
**修复（四项）**:
1. **模型路由** — `LLM_FAST_MODEL` 配置：评估/JD解析/暖场/出题用 `deepseek-v4-flash`（实测 7.8s vs pro 17.6s），最终报告用 pro。`Interviewer` 新增 `llm_strong` 参数，web 层按模型缓存客户端。
2. **并行化** — 出题生成与暖场互不依赖，`asyncio.gather` 并发。
3. **截断 JSON 修复** — `repair_truncated_json()`：尾部回退到字段边界、补齐未闭合括号、重试解析，抢救截断前已完整的字段。评估降级路径现在**留日志**（之前静默返回 {}，线上无法定位）。
4. **上下文追问兜底** — 追问文本为空时优先用 `missed_points` 生成"你刚才的回答没有提到「写屏障、混合写屏障」，能展开说说吗？"——贴题，不再乱问。

**性能指标落地**（用户提出"时间也可以作为一个指标"）: `InterviewState.timings` 记录各阶段耗时（jd_parse / question_gen+warmup / evaluate 累计 / report），持久化到会话快照，`GET /api/interviews/{id}` 暴露 `timings` 字段，面试结束打日志。  
**实测**: 创建面试 51s→22.1s，单次评估 17.6s→5.4s（3.3 倍），flash 评语质量无下降（"回答完全偏离问题，把'SQL查询执行流程'偷换成了'索引结构优化'"）。

**经验**: ① 推理模型不适合高频小任务——模型路由（快模型日常 + 强模型关键节点）是 LLM 应用成本/延迟优化的第一课；② 降级路径必须留痕，静默降级 = 线上问题无法定位；③ 兜底行为也要"贴题"，通用话术会暴露降级。

---

## 阶段十四：LLM-as-judge 评测体系（回答"你的评估器到底好不好"）

### 2026-08-13 23:50 | `eval/` 评测框架 + 中文关键词匹配缺陷修复
**做了什么**: 构建量化评测闭环回答 Agent 岗必问题「你怎么评价自己的系统」:
1. **数据集** — 10 题 × 高/中/低三档人工标注回答（30 样本，标注分数为 ground truth）
2. **三项指标** — 评分一致性（同答评 3 次 std）、评分准确性（MAE + Pearson vs 人工标注）、追问贴题率（追问与题目/要点/回答的关键词呼应）
3. **双模式** — `--mock` 离线验证框架（CI 可跑，6 个测试）；真实 API 完整评测，报告写入 `docs/eval_report.md`

**评测立刻发现真 bug** — 高分回答被系统性低估（high 档 MAE 2.63、LLM 均分 6.0 vs 人工 8.7）。定位到关键词引擎的中文匹配缺陷：① `B+树结构` 拆词后单字母 `b` 被过滤 → 永不命中；② 中文连续串要求整串相等（`范围查询优势`），真实回答不会逐字复述；③ 单关键词要点阈值 `max(1, 0.5)=1.0` 要求完美命中。  
**修复** — 混合 token 保留符号后缀（`b+`/`c++`）；中文长词（≥4 字）字符覆盖率匹配（≥60% 视为命中）；单关键词要点阈值 0.6。  
**eval→fix→re-eval 闭环**: MAE 1.50→**0.94**（-37%）、Pearson 0.923→**0.991**、追问贴题率 89%→**100%**、一致性 std 0.16 无退化。  
**经验**: ① 评测体系的最大价值不是"证明系统有多好"，而是暴露盲区——这次发现的评分偏差在日常使用中完全无感，只有和 ground truth 对比才显形；② 中文关键词匹配是经典坑：中文没有天然分词边界，整串匹配和字级匹配都不对，字符覆盖率是工程折中；③ 评测必须能一键复跑，否则修完无法证明。

---

## 阶段十五：可观测性（调用级延迟/token/成本）

### 2026-08-13 24:20 | 指标链路落地
**做了什么**: 把「阶段计时」升级为完整的调用级可观测体系：
1. **LLMClient.usage_stats** — `chat_with_retry` 统一入口累计每次调用的延迟 + prompt/completion token（来自 API usage 字段，mock 按字符数估算）
2. **会话级阶段指标** — Interviewer 每个阶段（jd_parse/出题+暖场/evaluate/report）记录 `{latency, prompt_tokens, completion_tokens, model}`，随状态快照持久化
3. **成本估算** — `session_cost_estimate()` 按模型分别计价（模型路由下 flash 与 pro 价格差异大），价格表 `settings.llm_pricing` 支持环境变量覆盖
4. **Web 展示** — 面试结束自动追加「本场统计」卡片（耗时/Token/成本/阶段明细）；`GET /api/stats` 聚合全局用量

**实测数据**（真实 DeepSeek 8 题面试）: 输入 29.4K + 输出 32.6K tokens、成本 ¥0.049、阶段耗时（规则解析 0s / 出题+暖场 21.7s / 评估 310s / 报告 45s）。  
**指标立即体现价值**: 评估阶段 310s（平均 ~20s/次）与之前实测的 5.4s 差异巨大，日志确认无重试 — 是 API 端延迟波动。没有这套指标，这类问题完全不可见。  
**经验**: 可观测性不是锦上添花 — 它是成本优化和性能治理的前提。"测不到就优化不了"，LLM 应用的延迟/token/成本三件套应该从第一天就埋点。

---

## 阶段十六：Streaming（SSE 流式报告 + 追问逐字显示）

### 2026-08-14 00:30 | 报告 SSE 流式 + 流式路径补 usage_stats 计数

**做了什么**:
1. **流式入口补齐指标** — `core/llm.py` 新增 `stream_chat_with_retry()`，与 `chat_with_retry` 对称：完整消费后累计 `usage_stats`（prompt 用消息字符数估算、completion 用累计文本字符数估算，与 Mock 同口径）。抽取 `_record_usage`/`_estimate_prompt_tokens` 共享 helper，`chat_with_retry` 改为复用。重试语义：**只在首块到达前失败才重试**（连接/限流/超时），首块已发出后失败直接抛出——绝不重复输出已发文字。
2. **报告拆成两段** — `report.py` 新增 `generate_stream()`：结构化字段（分数/等级/优劣势/结论）确定性计算（`_compute_stats`/`_verdict_from_score`/`_aggregate_evals`），改进建议这类长文本由 LLM 流式生成（`stream_chat_with_retry`），逐字推出。事件序列 `stats → delta* → done`。
3. **状态机延迟报告** — `Interviewer` 新增 `defer_report` 标志 + `stream_report()`：Web 面试结束时不再内联生成报告（避免重复生成），改由 SSE 端点流式产出，`done` 前记录 report 阶段指标。
4. **Web SSE 端点** — `GET /api/interviews/{id}/report/stream` 用 `StreamingResponse` 流式返回；`/answer`、`/skip` 增加 `stream_report` 标志（面试结束且报告未内联时置 true）。
5. **前端** — `app.js` 用 `fetch` + `ReadableStream` 手动解析 SSE 帧，报告文字逐字 append 到"生成中"气泡，`done` 后重拉消息渲染完整报告卡；追问气泡走客户端打字机（历史回放不重放动画）。
6. **FakeStreamLLM 单测** — 8 个新测试覆盖：流式 usage 计数、首块前重试（调用 2 次只计 1 次用量）、首块后失败不重试、`generate_stream` 事件序列/降级、`defer_report` 集成。

**为什么这么做**: 真实 DeepSeek 报告生成实测 45s，用户干等无反馈；追问/报告文字逐字显示能显著提升"AI 在思考"的体感。更重要的是——`stream_chat` 此前完全不走 `usage_stats`，一旦接入会漏计 token/成本，可观测性数据失真。

**实测**: ruff 零错误；54 个测试全绿（新增 8 个）；`node --check` 通过；benchmark/demo 无回归；mock 模式流式报告端到端可用。

**经验**: ① 流式重试的边界是"首块"——首块前失败可安全重试，首块后失败重试会向客户端重复输出，这是流式与普通重试的本质区别；② 流式 API 通常不返回 usage，token 只能按字符数估算，口径要和 Mock 保持一致才能让 mock/真实两条路径的成本对比有意义；③ 报告这类"结构化 + 长文本"混合产物，拆成"确定性统计 + 流式叙事"最合适——JSON 不适合逐字流式，但纯叙事文字天然适合。

---

## 阶段十七：测试补全（核心模块覆盖率 86% + CI 覆盖率门禁）

### 2026-08-14 01:00 | 测试从 46 → 192 个，暴露并修复校验级别传播 bug

**做了什么**:
1. **清单 8 模块全补** — 按 next_steps 优先级补齐：`jd_parser`（词边界/兜底阈值/岗位猜测/malformed 降级）、`question_bank`（倒排索引/分层配额/去重/难度分层）、`question_gen`（五类配比/LLM 微调失败降级/补充/通识兜底）、`session_manager`（CRUD/置顶排序/重命名/对比/进度摘要）、`output_validator`（extract_json 各格式/截断修复/Schema 校验修正）、`code_judge`（输出比对回归/超时 kill/AST 拒绝）、`retry`（退避/熔断三态/降级链/协程判断）、`interviewer`（状态机跳转/序列化）。
2. **core 基础设施补测** — `agent.py`（ReAct 主循环/工具调用/强制总结）、`orchestrator.py`（串行/并行/辩论）、`error_handler.py`（降级注册表/safe_execute/超时/健康检查）。
3. **覆盖率工程化** — pyproject 配置 `--cov=interview --cov=core` 口径；CI 加 `pytest-cov` 与 `--cov-fail-under=80` 门禁；README 加覆盖率徽章 + 测试说明。

**测试立刻发现真 bug** — `output_validator._validate_array` 用枚举的字符串 value 比较级别：`sub.level.value > result.level.value`。但 `"fatal" > "ok"` 按字母序为 **False**，导致数组子项校验出的 ERROR/FATAL 级别永远无法传播到顶层结果（缺失必填字段的数组项被判为 OK，不会触发重试）。修复：改用 `_LEVEL_RANK` 字典按严重度排序比较。

**实测**: 46 → **192 个测试**全绿；核心模块（interview+core）覆盖率 44% → **86%**，CI 门禁 80% 通过；ruff 零错误；benchmark/demo 无回归。

**经验**: ① 补测试的最大价值不在数字，而在暴露盲区——级别传播 bug 在日常使用中无感（output_validator 的数组校验路径不在面试主链路上），只有测试才触发；② 覆盖率必须明确口径：全仓库含 benchmark/demo/main/web/agents/tools/memory 等需真实 openai/chromadb 依赖的代码，无法离线覆盖，用「核心模块」口径（interview+core）才诚实且可达；③ 枚举级别比较是经典坑——str Enum 的成员比较走字符串语义，必须用显式的严重度排序表。

---

## 阶段十八：交付叙事（Docker 一键启动 + 博客 + 简历）

### 2026-08-14 01:30 | 交付物补齐

**做了什么**:
1. **Docker 一键启动** — `Dockerfile`（python:3.13-slim，先 COPY requirements 利用层缓存，`--app-dir /app` 固定导入路径）+ `docker-compose.yml`（web 服务、`.env` 注入、`./data` 数据卷持久化）+ `.dockerignore`（排除 `.env`/`data`/缓存，绝不让密钥进镜像）。未配 Key 时自动降级 Mock 演示模式。
2. **技术博客** — `docs/blog.md`《从 0 实现一个面试 Agent：混合架构、双引擎评分与容错设计》，按「混合架构→双引擎评分→容错→模型路由→可观测性→评测闭环→Streaming」复盘，重点讲中文关键词匹配的坑与流式重试的边界。
3. **简历定稿** — `docs/resume.md`：可裁剪的 6 条 bullet + 量化指标速查表 + 高频追问预案（对应 interview_qa.md）。
4. **README 更新** — Docker 一键启动小节、博客/简历链接、uvicorn 命令补 `--app-dir`（与铁律一致，避免 cwd 漂移）。

**为什么这么做**: 代码能力再好，没有「一键跑起来」的路径和「讲得清」的叙事，对面试/评审的价值就打折。Docker 解决「环境劝退」，博客/简历把 18 个阶段的工程决策沉淀成可复述的表达。

**实测**: 192 测试全绿、ruff 零错误、benchmark/demo 无回归、覆盖率门禁 80% 通过。（docker 环境本机未安装，Dockerfile/compose 为标准写法未做本地 build 验证，CI 只跑 pytest/benchmark/demo 不构建镜像。）

**经验**: ① 交付物的本质是「降低别人的上手成本」——一键启动 + 清晰叙事比堆功能更重要；② `.dockerignore` 和 `env_file` 是 Docker 场景下的安全边界，密钥和数据必须排除在镜像之外；③ README 的启动命令要与实际验证过的命令保持一致（`--app-dir` 这种坑要同步到文档，否则用户照抄必踩）。

---

## 阶段十九：代码题沙箱判题接入主链路 + 多语言 + 代码编辑器

### 2026-08-14 11:30 | 修复「宣传了沙箱判题但主链路没用」的硬伤

**做了什么**:
1. **code_judge 接入 evaluator** — `evaluator.evaluate()` 对 `type=CODING 且带 code 元数据` 的题走 `_evaluate_code()`：把回答当代码交给 `run_judge` 真实执行测试用例，pass/fail 通过率覆盖正确性（全过=10 分，全挂/编译错/安全拦截/超时=1 分），结果透出到 `EvaluationResult.code_judge`。此前 `code_judge` 只在 benchmark/demo 里被调，面试主链路从未真正执行用户代码。
2. **多语言** — `code_judge` 重构为「语言执行器」模式：Python（AST 白名单，强）+ C++（g++ 编译 + 黑名单 + 超时，demo 级）。`run_judge` 支持 `language` 参数，编译型语言先 `g++ -std=c++17` 编译（编译错误单独返回），解释型直接执行。
3. **题库绑定测试用例** — `BankQuestion`/`InterviewQuestion` 新增 `code` 字段；COD001（LRU）绑定 5 个测试用例、COD002（日志统计）绑定 2 个、新增 CPP001（C++ 两数之和）；COD003（线程池）因并发判题复杂保持 LLM 评估。`question_gen` 转换时透传。
4. **前端代码编辑器** — 编程题卡片出现「💻 写代码」按钮 → 全屏代码编辑器模态框（语言徽标 + 函数签名 + 大 textarea）→ 提交后聊天流展示「代码气泡 + 判题结果卡片（逐用例 ✅/❌ + 期望/实际）」。复用 `/answer` 端点（后端自动识别 coding 题）。
5. **顺带修一个既有 bug** — `_parse_test_output` 里 `msg = line.split("__TEST_{i}_FAIL__: ", 1)` 的 `{i}` 不是 f-string，导致 expected/got 字段混入 marker 前缀（测试只断言了 pass/fail 名没暴露）。

**为什么这么做**: 简历/README 写了「真实沙箱判题（AST 白名单 + subprocess 真实执行）」，但主链路从未调用——面试官问「代码题怎么判的」会被当场追穿。这是「宣传与实现一致」的底线。

**实测**: 200 测试全绿（新增 C++ 多语言 + evaluator coding 接入共 8 个）；覆盖率 85.97%；ruff 零错误；benchmark/demo 无回归；端到端验证正确 LRU 代码 `correctness=10`、序列化 round-trip 保留 code 元数据。

**经验**: ① 文档里写的能力必须是主链路真实走通的——「演示模块」和「产品能力」是两回事，前者只能算 demo，后者才经得起追问；② 多语言沙箱的难点不在「执行」，在「安全」——Python 有 AST 白名单能强约束，C++ 只能黑名单 + 超时做 demo 级隔离，讲清楚这个边界比假装全语言都安全更专业；③ 判题结果的「expected/got 解析」这类格式化细节最容易出隐性 bug，测试要断言字段值而不只是 pass/fail 名。

---

## 阶段二十：追问 Agent 化（从固定 5 分类到自主决策）

### 2026-08-14 13:00 | 兑现「Agent」这个名字——追问环节交给 LLM 自主决策

**做了什么**:
1. **新增 `interview/follow_up_agent.py`** — `FollowUpAgent`：追问环节的「大脑」。输入「题目 + 回答 + 评估（命中率/未命中要点/评语）+ 本轮历史追问」，让 LLM 自主输出 `{continue, question, reason}`——是否继续追问、追什么、何时停，**不再硬编码 5 分类**。
2. **混合 Agent 架构落地** — `Interviewer` 保留状态机管骨架（出题/收答/评分这些确定性步骤），追问自由度交给 `FollowUpAgent`；追问上限（max_follow_ups）保留为**安全网**。
3. **三级降级** — ① 确定性边界（超短/垃圾/复读）由评估器明确判断，直接保留规则结果不被 Agent 覆盖；② Agent 正常时用其决策；③ Agent 不可用（LLM 失败/JSON 非法）返回 `continue=None`，回退评估器 5 分类——**绝不因 Agent 挂了中断面试**。
4. **Mock 确定性路由** — Mock 按「未命中要点」生成贴题追问（"你刚才没有提到「三色标记」，能展开说说吗？"），保证无 Key 演示也走 Agent 路径。

**为什么这么做**: 项目叫「Agent」，但此前追问是评估器 JSON 里的 5 分类（deepen/challenge/upgrade/example/move_on），`core/agent.py` 的 ReAct 是独立 demo 从未接入——面试官问「你的 Agent 怎么自主决策」会答不上来。这一步把「状态机」升级为「混合 Agent」，状态机退化为骨架，Agent 在自由度最高的追问环节真正自主。

**实测**: 217 测试全绿（新增 7 个 FollowUpAgent 测试）；ruff 零错误；benchmark/demo 无回归；端到端验证跑题回答被贴题追问「你刚才没有提到「三色标记」，能展开说说吗？」。

**经验**: ① 「混合 Agent」的落点要选对——不是把整个状态机推倒，而是让 Agent 只接管「自由度最高」的环节（追问），确定性步骤（出题/评分）保留规则，这才是工程上可控的 Agent 化；② 降级链是 Agent 的命门——LLM 决策可能挂，必须保留「规则兜底 + 确定性边界不覆盖」两层，否则 Agent 化反而降低可靠性；③ 确定性短路（超短/垃圾回答）必须优先于 Agent 决策，否则 Agent 会因为"无未命中要点"而放过明显该追问的回答。

---

## 阶段二十一：记忆能力画像（跨会话强弱项 + 进步趋势）

### 2026-08-14 13:40 | 把零散的历史答题记录聚合成「可视化画像」

**做了什么**:
1. **`interview/profile.py` 能力画像聚合器** — `ProfileBuilder` 从 SessionManager 里所有会话的 `interviewer_state.state.answers` 按 `question.category` 聚合 evaluation 的 4 维得分，产出 `AbilityProfile`：每技能的平均分/4 维均分/答题次数/最早与最近得分（进步趋势）。**零 LLM 依赖**，直接读磁盘 JSON。
2. **踩掉一个序列化坑** — `EvaluationResult.total_score` 是 property，`dataclasses.asdict` 不会序列化它，画像里必须用 4 维加权重算（`score_from_ev`，与 total_score 同口径），否则聚合出来全是 0 分。
3. **Web 端点 + 前端** — `GET /api/profile` 返回画像；侧边栏新增「📊 能力画像」按钮 → 弹窗展示：总场次/答题数、弱项（红）/强项（绿）标签、每技能卡片（均分 + 4 维明细 + 进步 ▲/退步 ▼）。
4. **画像驱动追问（闭环）** — 创建新面试时，把画像弱项追加进 `memory_hints`，评估/追问会对候选人的历史短板重点验证——从「翻旧账」升级为「画像驱动的个性化面试」。

**为什么这么做**: 记忆模块此前只有 `recall_weaknesses`（向量检索弱项），能力画像把记忆升级成「结构化统计出口」——回答「你的 Agent 怎么记住我、怎么个性化」时，有图有数据，不再是一句"检索了历史弱项"。向量检索（语义相似）与画像（结构化统计）互补，前者解决"相关"，后者解决"统计"。

**实测**: 224 测试全绿（新增 7 个画像测试，覆盖加权分/聚合/强弱项/进步趋势/空画像）；ruff 零错误；端到端验证 `/api/profile` 正确聚合 3 场会话 4 次答题。

**经验**: ① dataclass 的 property 不参与 asdict 序列化——跨模块读序列化数据时，要么显式序列化 property，要么像这里一样重算，这是序列化契约里最容易踩的暗坑；② 能力画像和向量检索是两种互补的记忆出口：统计要快、要零成本（读磁盘），语义要准（向量），混用一层反而两头不讨好；③ 画像的价值在「闭环」——只有把弱项喂回下一场面试的追问，画像才从"报表"变成"能力"，否则只是事后看的统计页。

---

## 阶段二十二：RAG 面经（参考答案升级为检索增强生成）

### 2026-08-14 14:10 | 把参考答案从「关键词要点」升级为「真实面经 + 检索增强」

**做了什么**:
1. **`interview/qa_bank.py` 面经库 + 轻量检索器** — 内置 12 条高频考点面经（Python GIL/MySQL 索引/Redis/Kafka 可靠性/RAG/雪花算法等，答案均为面试级精简回答）。`QaRetriever` 用「关键词 Jaccard + 字符 n-gram 相似度」打分（0.6/0.4 加权），**零 LLM、零向量库依赖**——ChromaDB 未装也能跑，装了可平替向量检索（接口不变）。
2. **参考答案 RAG 融合** — `_build_fallback_reference` 升级：每题先检索面经库（`top_k=1`），命中就给真实面经答案（标记 `source: 面经库`），检索不到才回退 expected_points 关键词兜底。Mock 模式也可见 RAG 效果。
3. **踩掉检索噪音坑** — n-gram 对「单个字符对重合」太敏感，无关条目也有微小分数（如 "feature" 的 `re` 撞上 "redis" 的 `re`），加 `MIN_SCORE=0.05` 阈值过滤。
4. **Web 端点** — `GET /api/qa/search?q=...` 演示检索环节（面试必问 RAG 时可直接现场演示）。

**为什么这么做**: RAG 是 LLM 应用面试必问，而项目此前 ChromaDB 一直"进程内降级"、向量检索从未真正跑起来。先落地一个**零依赖可跑通**的检索增强（关键词+相似度），把「检索→增强→生成」的链路打通并展示，再升级向量化只是换检索器的事。

**实测**: 231 测试全绿（新增 7 个面经检索/RAG 融合测试）；ruff 零错误；端到端验证 `/api/qa/search?q=MySQL索引B+树` 命中 QA002（score 0.316）、参考答案带 `source: 面经库`。

**经验**: ① RAG 的核心是「检索质量」——在没上向量库前，关键词 Jaccard + 字符 n-gram 的混合打分已经能覆盖中文场景的大部分相关检索，关键是**阈值**要能滤掉"单个字符撞车"的噪音；② 检索器接口先行（`retrieve(query, top_k)`），向量化只是换实现，这让「零依赖演示」和「生产向量检索」可以平滑切换；③ RAG 的落地价值不在"用了 embedding"，而在「检索到的东西真的增强了输出」——参考答案从"答题要点：X、Y、Z"变成"GIL 是 CPython 的全局解释器锁…"，用户感知立竿见影。

---

## 阶段二十三：功能增强量化评测（Before / After 指标对比）

### 2026-08-14 14:50 | 让工程改进有量化证据，而不是"我感觉变好了"

**做了什么**:
1. **`eval/feature_eval.py` 前后对比评测脚本** — 把最近的工程增强做成可复现的量化对比：
   - **追问贴题率**：Before=评估器 5 分类规则 vs After=FollowUpAgent 自主决策（复用 judge_eval 的 `follow_up_is_relevant` 判定，30 样本评测集）
   - **参考答案质量**：Before=expected_points 关键词要点 vs After=RAG 面经（平均长度 + 信息密度）
   - **面经检索覆盖率**：题库多少题能命中相关面经（RAG 数据源覆盖度）
2. **评测立刻发现指标设计坑** — 初版用「要点覆盖率」对比 RAG 答案，结果 After 反而下降（66.7% vs 98.9%）：面经答案覆盖的是它自己的要点，不是题库题目的 expected_points 词表，指标对 RAG 不公平 → 改用「信息密度（技术关键词数）」。
3. **面经库 12 → 22 条** — 补 Go GMP / Java synchronized / Docker 分层 / Kafka ISR / HNSW / Function Calling / HTTP2 等高频方向，检索命中率 37.6% → 57.0%。
4. **`docs/feature_eval_report.md`** — 沉淀「Agent 开发常用指标速查表」（任务完成/质量/效率/检索/可靠性五类）+ 前后对比结果 + 复现方式。

**实测结果**（全离线可复现）:
| 指标 | Before | After |
|------|:---:|:---:|
| 追问贴题率 | 0%（规则通用话术） | **100%** |
| 参考答案平均长度 | 36.3 字 | **85.8 字** (+136%) |
| 信息密度（关键词数） | 7.2 | **13.4** (+86%) |
| 面经检索命中率 | — | **57.0%**（53/93 题） |

**为什么这么做**: 项目改进如果只有"新增了功能"没有"指标变化"，面试时没有说服力。把「追问 Agent 化」「RAG 面经」这些改动全部转成可复现的 Before/After 数字，配合 benchmark（延迟/成本/调用次数）和 judge_eval（MAE/Pearson），形成完整的量化证据链。

**经验**: ① 对比评测的指标必须对「两条路径」都公平——初版「要点覆盖率」用题库词表衡量面经答案，是拿错尺子；② 评测脚本本身也是产品：一键复现 + 输出可贴进 README/简历的表格，比临时跑一次脚本价值大得多；③ Agent 项目的说服力 = 指标体系（完成率/质量/效率/检索/可靠性）+ 前后对比，缺一不可。

### 2026-08-14 15:10 | 证据链收口：三脚本统一 + 指标设计三处修正

**三处指标修正**（跑完整评测时发现）:
1. **judge_eval 追问贴题率改测 FollowUpAgent** — 项目已 Agent 化，追问来源是 Agent 而非评估器；原逻辑 Mock 下测的是评估器通用话术（0%），改后 100%，与 feature_eval 叙事一致。
2. **benchmark "other" 阶段 → follow_up_agent** — FollowUpAgent 调用此前归入 "other"，导致「单场 LLM 调用 6→10」无法解释；现在明确标注「追问自主决策（Agent 化，贴题率 100%）」。
3. **单场 LLM 调用 6→10 如实叙事** — +4 次为追问决策（Agent 化的成本），换来贴题率 0→100%，不回避代价。

**完整证据链成稿**（docs/feature_eval_report.md）：benchmark（混合架构/判题）+ judge_eval（评分质量）+ feature_eval（Agent/RAG 增强）+ 真实联调（延迟/成本），四层指标 + Before/After 对比 + Agent 指标速查表，全部一键复现。

**经验**: ① 指标体系必须「叙事一致」——同一个"追问贴题率"，三个脚本要测同一个来源（Agent），否则自相矛盾；② 调用次数的变化要能解释——"other" 这种笼统分类会掩盖 Agent 化的成本，标注清楚才敢写进简历；③ 证据链要分层（工程/质量/增强/性能），单靠一个脚本说服力不够。

---

## 阶段二十四：Knowledge 知识库接入 RAG（面经 22 → 419 条）

### 2026-08-14 15:50 | 把外部 Agent 知识库解析成可检索的 RAG 数据源

**做了什么**:
1. **`docs/knowledge/` 知识库归档** — 用户新增的 Agent 开发面试知识库（15+ 主题、36 个 md、约 400 道题，含架构/RAG/评测/多智能体/工程踩坑/辅导方法论）从根目录移入 `docs/knowledge/`。
2. **`interview/knowledge_loader.py` 加载器** — 解析两种格式：单题 md（`## Q：`+`**高手答**`）精确提取；聚合 index.md（长文里多个 `### Q：`）按 Q 切分取「高手答」。按问题去重、答案截断 2000 字、目录名（去编号前缀）作维度 tag。**共解析出 397 条**。
3. **RAG 数据源扩展** — `qa_bank.get_all_qa_entries()` = 内置 22 条 + 知识库 397 条 = 419 条；报告参考答案检索范围扩大 19 倍。惰性加载 + 模块级缓存，失败/缺失返回空不阻塞主流程。
4. **修一个解析 bug + 一个检索坑**：
   - index.md 的 `### Q：`（三级标题）被误判为单题（只取第一个 Q），修正判断逻辑（`###`→聚合、`##`→单题），条目数 27 → 397。
   - 知识库条目多后 n-gram 巧合噪音变多（无意义查询也"命中" 0.12 分），`MIN_SCORE` 0.05 → 0.15 过滤。
   - 检索质量关键发现：题库题的 **tags（英文技能词）比 question 文本更匹配面经**，但转换时 tags 丢了——给 `InterviewQuestion` 补回 `tags` 字段（BankQuestion → InterviewQuestion → 序列化全链路透传），检索 query 拼 tags 后命中率 23.7% → 43%。
5. **指标更新** — 参考答案 36.3→**100.2 字**（+176%）、信息密度 7.2→**14.3**（+99%）、面经覆盖 **43%**（419 条覆盖 93 题，严格阈值无噪音）。

**为什么这么做**: RAG 的价值 = 数据源规模 × 检索质量。知识库 397 条是用户积累的宝贵资产，解析接入后参考答案质量明显提升（86→100 字）；同时补上了「InterviewQuestion 转换丢 tags」这个数据建模缺口。

**实测**: 236 测试全绿（新增 5 个知识库加载器测试）；ruff 零错误；feature_eval 命中率 43%、答案长度 100 字（严格阈值，无噪音匹配）。

**经验**: ① 解析外部文档的格式判断要精确——「`###` vs `##`」一级之差导致只解析出 27/397 条，解析器先小样验证再全量；② 数据源扩大会放大检索噪音——阈值要跟着数据量调，且用「无意义查询不命中」做验证用例；③ 数据建模缺口（转换丢 tags）会悄悄削弱下游能力（RAG 匹配），全链路透传关键字段是基本功。

---

## 阶段二十五：题库扩充 LeetCode 100 题（93 → 193 题）

### 2026-08-14 16:30 | 接入 LeetCode Problems JSON Dataset，代码题选择面扩大 17 倍

**做了什么**:
1. **`tools/import_leetcode.py` 导入器** — 从 LeetCode JSON Dataset（2913 题）按「难度均衡（Easy 40/Medium 45/Hard 15）+ 主题覆盖（41 个主题，每主题≤6）+ 经典优先（frontend_id 靠前）」选取 100 道，生成 `interview/leetcode_bank.py` 并合并进 `QUESTION_BANK`（93 → **193 题**，CODING 6 → 106 道）。
2. **examples → 测试用例自动生成（尽力而为）** — 从题目 examples 提取 Input/Output：解析参数（`ast.literal_eval` 安全解析 Python 字面量）、从 python3 code_snippet 提取方法签名、输出规范化（list 用 repr 对齐 `print`、`true/false/null` 转 Python、字符串去引号）。**49/100 道**成功生成可判题用例（Two Sum 实测 3/3 AC）；树/链表等复杂题不带判题，走 LLM 评估。
3. **RAG 数据源再扩展** — 生成 `leetcode_solutions.py`（100 条英文面经，从官方 solution 提取算法思路），面经库 **419 → 519 条**（22 内置 + 397 知识库 + 100 LC），让 RAG 也能覆盖英文 LeetCode 题。
4. **指标大幅提升** — 参考答案 36→**143.6 字**（+296%）、信息密度 7.2→**15.9**（+174%）、面经覆盖 **72.5%**（140/193 题，严格阈值）。

**为什么这么做**: 题库太少是产品短板（93 题里代码题仅 6 道）。LeetCode 数据集是公开的经典题库，接入后代码实操题选择面从 6 → 106 道，且大部分题目自带示例可自动生成判题用例——「出题 → 判题 → 参考答案」全链路对 LeetCode 题也成立。

**实测**: 236 测试全绿（题库 193 题不影响现有断言）；ruff 零错误；LC001（Two Sum）判题 3/3 AC；feature_eval 命中率 72.5%、答案 143.6 字。

**经验**: ① 外部数据集接入的核心是「字段映射 + 降级」——examples 解析失败就安全降级为 LLM 评估，不阻塞；② LeetCode 输出格式（`[0,1]`、`true`）和 Python `print`（`[0, 1]`、`True`）不一致，期望输出必须规范化，否则判题永远失败；③ 难度/主题配额让选取可解释（简历能说"Easy 40/Medium 45/Hard 15"），而不是"随便挑 100 道"。

---

## 阶段二十六：链表/树题自动判题（50 → 69 道用例）

### 2026-08-14 18:00 | 判题环境注入 ListNode/TreeNode 工具，LeetCode 复杂题真实跑用例

**做了什么**:
1. **`code_judge.py` 注入节点工具** — 新增 `NODE_UTILS_SOURCE`：判题脚本在用户代码**之前**注入 `ListNode`/`TreeNode` 类定义 + 数组↔对象转换器（`list_to_linkedlist`/`linkedlist_to_list`、`list_to_tree`/`tree_to_list`、`trees_to_list`，树用层序数组、尾部 null 省略，与 LeetCode 输出格式一致）。用户代码里的 `Optional[ListNode]` 注解在 def 时求值，必须先有类定义，所以注入块放在用户代码前。
2. **`tools/import_leetcode.py` 用例生成器升级** — `extract_signature` 增加返回类型提取（`-> Optional[ListNode]`）；`gen_test_cases` 按参数/返回类型分派：
   - ListNode/TreeNode 参数 → `list_to_linkedlist([...])` / `list_to_tree([...])`（`List[ListNode]` 逐项构造，mergeKLists；null → None）
   - ListNode/TreeNode 返回 → `linkedlist_to_list` / `tree_to_list` / `trees_to_list` 序列化后 print
   - 返回 None（原地修改，recoverTree）→ 构造节点变量、调用后序列化
   - 普通类型（int/bool/List[int]）→ 直接 print
3. **顺带修复既有 bug（LC095 generateTrees）** — 参数是 int 不含节点类型，旧逻辑走了普通路径生成用例，但返回 `List[TreeNode]` 打印的是对象内存地址，**该用例永远 WA**。现在按返回类型包装 `trees_to_list`，实测 2/2 AC。
4. **AST 白名单补缺** — 链表/树题用户代码常用 `is not None`、列表推导、`nonlocal`，但白名单缺 `ast.Is/IsNot/comprehension/Nonlocal`，会误杀合法解法。补齐（纯语法节点，无安全面扩大）。
5. **新增 `tools/verify_lc_judge.py` 验证器** — 手写 20 道链表/树题参考实现，逐题断言「正确实现 AC + 错误实现非 AC（区分度）」，防生成器回归。
6. **测试 +44**（236 → 255）：`test_code_judge.py` 新增节点判题/白名单用例、`tests/test_import_leetcode.py`（生成器 12 例：签名提取、节点参数包装、原地修改、generateTrees 修复）。

**结果**: 自动判题用例 **50 → 69 道**（+19），LeetCode 链表/树经典题（Add Two Numbers、Merge k Sorted Lists、Level Order、Max Path Sum、House Robber III 等）全部可真实跑用例判 AC/WA；覆盖率 86% → **87%**。

**为什么这么做**: 之前「题干注释里有 ListNode 构造定义为什么不能判题」——注释只是提示，判题沙箱里并没有 ListNode 类、没有数组→对象构造器、没有对象→数组序列化器，三样缺一不可。LeetCode 平台是隐藏注入这三样，我们的沙箱补上同样能力，复杂题从「LLM 评估」升级为「真实用例判定」。

**实测**: `tools/verify_lc_judge.py` 100 题全绿（20 道参考实现 AC + 区分度 OK，其余 SKIP/LLM 评估）；255 测试全绿；ruff 零错误；benchmark 无回归（判题检出 6/6）。

**经验**: ① 判题不只是「跑起来」，输入要能构造对象、输出要能序列化比较，缺一环就只能靠 LLM 猜；② 生成器按「参数类型 × 返回类型」双维分派，比按题目人工分类可扩展（新增数据源题也能自动生成）；③ 白名单缺节点会让「合法解法被误杀」，用参考实现跑一遍全量用例是发现这类隐性 bug 的最快路径。

### 2026-08-14 18:40 | 无自动判题用例的代码题降级为 LLM 代码评审（修 0/0 AC 假阳性）

**背景**: 阶段二十六把链表/树题补上用例后，还剩 31 道无 test_cases 的 coding 题（类设计题 MinStack/BSTIterator、SQL/Shell 无 Python 模板题、特殊 API 题等）。用户问「不能自动判题该怎么判」——排查发现**假阳性 bug**: `run_judge` 在 test_cases 为空时 `passed(0) == len(0)` 判定全部通过 → **verdict AC**，用户提交任何代码都得满分。

**修复**:
1. **`code_judge.py` 守卫** — `run_judge` 无 test_cases 直接返回 SE「无测试用例」，从根上杜绝 0/0 判 AC。
2. **`evaluator.py` 降级链** — `_evaluate_code` 检测到 test_cases 为空 → 走新增 `_evaluate_code_review`：LLM 按 `CODE_REVIEW_PROMPT` 评审代码正确性/复杂度/质量（返回 correctness 1-10 + 评语 + strengths/weaknesses + 追问决策）；LLM 不可用给中性 5 分并明确说明「语义评估不可用」，不伪造通过。结果透出 `code_judge.verdict="REVIEW"`。
3. **`mock_llm.py` 路由** — 新增 `_code_review` 确定性响应（按「是否完整实现 + 是否含函数定义/返回值」给 2/4/5/8 分），Mock 模式全链路可跑。
4. **Web 适配** — `/api/code/run` 无 test_cases 返回 400 提示「无法自测，由 AI 代码评审」；前端无用例时禁用「运行」按钮 + `VERDICT.REVIEW` 卡片（🤖 AI Code Review + 评语）+ 蓝色样式。
5. **测试 +6**（255 → 261）：0/0 守卫 SE 回归、REVIEW 裁决透出、好代码高分/坏代码低分、无 LLM 中性降级、LLM 输出不可解析降级。

**为什么这么做**: 判题的底线是「不给错误答案发奖」。0/0 判 AC 等于告诉面试者「代码随便写都满分」——比不判更伤。无法自动判的题要**诚实降级**（LLM 评审 + 明示无用例），而不是用假阳性掩盖。

**实测**: 261 测试全绿；ruff 零错误；node --check 通过；benchmark 无回归；verify_lc_judge 69 道用例题不受影响。

**经验**: ① 判题框架的「空用例 = 全通过」是经典假阳性——任何 total_tests 可能为 0 的判定都要显式守卫；② 降级不是降质量，是「有证据用证据、没证据诚实说没有」——LLM 评审明示「无自动判题用例」比伪造 AC 可信得多；③ 判题能力分三档: 自动用例（事实）→ LLM 评审（语义）→ 中性分（不可用），逐级降级且全程留痕。

### 2026-08-14 16:35 | 真实 DeepSeek 8 题面试成本复测（¥0.049 → ≈¥0.02）

**背景**: 简历口径「单场 8 题面试成本 ¥0.049」出自阶段十五实测（2026-08-13，输入 29.4K + 输出 32.6K tokens）。用户质疑数据来源，要求用真实 API 复测验证当前价格/模型路由下是否站得住。

**做了什么**: 新增 `tools/realtime_cost_probe.py` 成本探针——用 `.env` 真实 DeepSeek key + 模型路由（flash 快模型评估/追问 + pro 强模型报告）跑完整 8 题面试（预置 8 条贴近真实长度的回答），输出分阶段 token 明细 + `session_cost_estimate()` 成本估算，结果归档 `logs/realtime_cost_probe.json`。

**实测结果**（2026-08-14，deepseek-v4-flash [0.2, 1.0] + deepseek-v4-pro [1.0, 4.0] 元/百万 token）:
- 输入 9,663 + 输出 11,193 tokens，成本估算 **¥0.0243**
- 分阶段: jd_parse in 416/out 1999 (19.4s) · 出题+暖场 in 628/out 1393 (10.9s) · 评估×28 in 5906/out 4801 (53.9s) · 报告 in 2713/out 3000 (61.4s)
- 面试结论 2.7/10（预置回答质量中等，追问次数正常）

**口径对比与原因**: 旧 ¥0.049 是阶段十五实测（62K tokens），当时追问 Agent/记忆注入/RAG 尚未加入；本次全链路 8 题实测 token 仅 20.9K——prompt 更精简、上下文注入更省。两次均为真实 API 实测、同一价格表，差异来自功能演进后 token 用量下降。

**文档同步**: 简历（resume.md / resume_agent_dev.md）、README、feature_eval_report、next_steps 的成本口径统一更新为「≈¥0.02（2026-08-14 复测，随追问深度波动 ¥0.02-0.05）」；CHANGELOG 阶段十五的历史实测保留原文（可追溯）。

**经验**: ① 简历数字必须能当场复现——「¥0.049」被追问「你怎么算的」时，有 CHANGELOG 原始记录 + 可重跑的探针脚本才有底气；② 成本口径要写清「估算 vs 账单」「哪次实测」「价格表」三要素，否则换模型/换价格后数字漂移会被质疑；③ 单样本成本波动大（追问深度/回答长度影响 token 用量），简历写范围比写死数字更诚实。

### 2026-08-14 18:50 | 消除「宣传与实现不一致」：ChromaDB 真实链路验证 + Docker 承诺修正

**背景**: 代码里写「ChromaDB 向量记忆 + 三级降级」，但 chromadb 从未安装过——真实链路零验证，简历承诺有穿帮风险；「Docker 一键启动」同样未经验证（本机无 Docker）。按「宣传与实现一致」原则逐一兑现或修正。

**做了什么**:
1. **安装 chromadb 1.5.9 + sentence-transformers 5.7.0 + tiktoken 0.13.0**，首次跑通向量记忆真实链路：VectorMemory 语义检索排序正确（"Python 技能" → Python 条距离 0.17 最近、Java 条 0.76 最远）、InterviewMemory 跨会话弱项召回正确（低分召回、高分 8.5 不召回）、持久化跨实例有效。
2. **修复可选依赖导入隐患** — `memory/__init__.py` 原顶层 `from .vector_store import VectorMemory`（→ 顶层 `import chromadb`），缺依赖时 `import memory` 直接崩溃。改为懒加载 `get_vector_memory()`（主链路本就走 `interview/memory_context.py` 内 `from memory.vector_store import ...`，不受影响）。
3. **新增 `tests/test_vector_memory.py`（5 个测试）** — 真实语义检索排序/元数据过滤/持久化/chroma 后端启用/弱项召回。设计: `importorskip` 保证 CI（最小依赖无 chromadb）整组跳过；模型离线加载（缓存缺失 → skip 不联网）；环境变量在**所有 HF 相关 import 之前**设置。
4. **修复测试回归（test_memory_context）** — 装 chromadb 后 `InterviewMemory()` 默认持久化目录 `data/chroma` 读到真实历史数据，2 个测试失败。改为 `_fresh_memory()` 用独立目录 + 清空。
5. **requirements.txt 补 sentence-transformers** — 此前声明 chromadb 但缺 embedding 依赖，Docker 镜像内 chroma 会因缺 sentence-transformers 降级。
6. **Docker 承诺修正** — 本机无 Docker 无法验证 → README 标注「标准写法未做本地 build 验证」，简历「Docker 一键启动」改为「Dockerfile + Compose（标准配置）」。

**排障实录（本机环境特异问题）**: 测试反复「卡死」无输出——根因: `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`/`HF_ENDPOINT` 环境变量最初在 `importorskip` **之后**才设置，而 huggingface_hub 在 import 时把值缓存为模块常量，运行期改环境变量无效 → offline 失效、每次模型加载发 HEAD 校验请求 → 打向 huggingface.co（直连不通，WinError 10060）→ huggingface_hub 5 次超时重试 × 每次数十秒 = 每次测试伪卡死 5-10 分钟。修复: 环境变量移到模块最顶部（任何 HF import 之前）+ 只走离线加载路径。修复后连续 5 次稳定 12-14 秒通过。

**实测**: 266 测试全绿（261 + 5）；ruff 零错误；benchmark/demo 无回归；真实语义检索链路验证通过（排序/召回/持久化三项断言）。

**经验**: ① 「三级降级」的代码只验证了降级路径、没验证主路径——宣传 ChromaDB 前必须先装依赖跑通真实链路，这是「宣传与实现一致」的基本功；② 可选依赖的顶层 import 是隐性炸弹（缺依赖时 import 包即崩），懒加载函数是标准解法；③ huggingface_hub 类库在 import 时缓存环境变量是常见坑——离线/镜像配置必须早于 import，测试文件里环境变量要放最顶部；④ 测试读真实数据目录（默认持久化路径）是环境敏感回归的典型来源，测试必须用独立目录；⑤ 「卡死无输出」先怀疑网络超时重试链（每次重试数十秒 × N 次 = 分钟级伪挂起），用 faulthandler/分步打印定位比盲猜快。

---

## 技术决策速查表

| 决策 | 选型 | 为什么不选替代方案 |
|------|------|-------------------|
| Agent 框架 | 自主实现 ReAct | LangChain 黑盒抽象，面试中会被认为是"调包" |
| JD 解析 | 规则引擎 + LLM 兜底 | 纯 LLM 成本高、速度慢、结果有随机性 |
| 出题策略 | 题库检索 + LLM 微调 | LLM 凭空出题质量不可控，没有 expected_points |
| 答案评分 | 关键词匹配 + LLM 双引擎 | LLM 有"看起来不错就给高分"的 bias |
| 代码判题 | AST 审计 + subprocess 真实执行 | LLM 评价代码正确性不靠谱 |
| 向量数据库 | ChromaDB | Pinecone/Weaviate 需要额外部署和付费 |
| Embedding | all-MiniLM-L6-v2 | OpenAI Embedding 需要 API 调用(成本+隐私) |
| Context 管理 | 优先级混合保留 v2 | FIFO 滑动窗口会误淘汰 system prompt |
| LLM Provider | OpenAI + Anthropic 双适配 | 绑定单一供应商有单点故障风险 |
| CLI | Rich | 纯 print 无法展示表格/面板/进度条 |
| 异步方案 | asyncio 全链路 | 同步阻塞导致 8 轮评估串行耗时 16s+ |
| 重试策略 | 指数退避 + jitter | 固定间隔重试会导致惊群效应 |
| Web 框架 | FastAPI | Flask 非异步原生；Django 太重；FastAPI 与项目 asyncio 架构无缝对接 |
| Web 前端 | 原生 HTML/CSS/JS | 两个视图 + 一个列表的体量，框架反而引入 node 构建链 |
| PDF 解析 | pypdf | 纯 Python 轻量；PDFMiner 复杂；扫描件需 OCR（超出 demo 范围） |
| 演示降级 | MockLLMClient | 无 Key 环境也能跑通全流程；LLMClient 抽象基类的第三实现 |
| 断点恢复 | Interviewer.to_dict/from_dict | 状态机快照序列化到磁盘，服务重启从 SessionManager 重建 |
