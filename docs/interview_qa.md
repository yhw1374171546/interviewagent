# 面试模拟 Agent — 面试 Q&A 参考

> 本文档针对秋招/社招面试中关于此项目的常见问题，提供完整的技术回答，覆盖 Agent 设计、优化、记忆、容错、上下文管理等核心领域。

---

## 1. 项目中 Agent 的完整流程是怎样的？

项目中有两层 Agent 概念：

### 1.1 通用 Agent 框架 (`core/agent.py`)

采用 **ReAct (Reasoning + Acting)** 模式：

```
User Input → System Prompt 注入 → [主循环]
                                      │
                            ┌─────────▼─────────┐
                            │  1. Think (思考)    │
                            │  LLM 分析需求        │
                            │  决定: 回答 or 调工具  │
                            └─────────┬─────────┘
                                      │
                            ┌─────────▼─────────┐
                            │  2. Act (行动)      │
                            │  执行工具调用         │
                            │  (搜索/代码执行/文件)  │
                            └─────────┬─────────┘
                                      │
                            ┌─────────▼─────────┐
                            │  3. Observe (观察)  │
                            │  收集工具返回结果     │
                            │  追加到对话历史       │
                            └─────────┬─────────┘
                                      │
                              回到 Step 1 继续
                              直到: 给出最终答案 or 达到 max_steps
```

关键实现细节：
- **Tool Registry**: 工具通过 `@tool` 装饰器注册，自动从函数签名推断 JSON Schema
- **Step Tracing**: 每一步的思考/行动/观察都记录在 `AgentStep` 中，用于调试
- **Token Tracking**: 累计 token 消耗，支持成本估算
- **终止条件**: 无 Tool Call 时自动终止，或达到 `max_steps` 后强制 LLM 总结

### 1.2 面试 Agent 专用流程 (`interview/interviewer.py`)

采用 **状态机模式**，7 个状态管理完整面试：

```
INIT → WARMUP → QUESTION → ANSWER → EVALUATE → FOLLOW_UP? → NEXT_QUESTION
                                                              ↓
                                                         CONCLUSION
```

具体每一步：
1. **INIT**: JD 解析（规则引擎 + LLM 兜底）→ 题目生成（题库检索 + LLM 适配）
2. **WARMUP**: LLM 生成暖场介绍（岗位 + 流程 + 放松引导）
3. **QUESTION**: 出题（含题型标签、难度、分类）
4. **ANSWER**: 等待面试者输入
5. **EVALUATE**: 双引擎评估（关键词匹配 + LLM 深度分析）
6. **FOLLOW_UP?**: 5 分类追问决策（deepen/challenge/upgrade/example/move_on）
7. **CONCLUSION**: 生成完整报告（统计计算 + LLM 分析）

---

## 2. 项目过程中针对 Agent 做过哪些优化？

### 2.1 成本优化

| 优化项 | 方法 | 效果 |
|--------|------|------|
| **规则引擎替代 LLM** | JD 解析 200+ 关键词匹配覆盖 70-90% | 减少 70%+ API 调用 |
| **题库检索替代 LLM 出题** | 80+ 道预置题库 + 倒排索引 | 出题环节零 API 调用（仅微调时调 LLM） |
| **关键词匹配替代 LLM 评分** | 评估器的正确性/相关性维度用规则计算 | 评分环节减少 40% LLM 调用 |
| **Prompt Caching** | Anthropic 原生 caching + system prompt 复用 | 降低 90% 输入 token 成本 |

### 2.2 性能优化

| 优化项 | 方法 | 效果 |
|--------|------|------|
| **异步全链路** | asyncio + AsyncOpenAI/AsyncAnthropic | 非阻塞 I/O，支持并发 |
| **流式输出** | SSE streaming (OpenAI + Anthropic) | 用户感知延迟降低 60% |
| **懒加载导入** | `__getattr__` 动态导入，不依赖的模块不加载 | 冷启动 < 500ms |
| **Token 预算控制** | 滑动窗口 + 优先级保留策略 | 防止超限导致的 API 错误 |

### 2.3 质量优化

| 优化项 | 方法 | 效果 |
|--------|------|------|
| **Structured Output** | JSON Schema 约束 + 格式校验 + 自动重试 | 输出解析成功率 > 99% |
| **双引擎评估** | 关键词匹配(客观) + LLM(语义) | 评分更公正，减少 LLM 评分偏差 |
| **沙箱判题** | AST 白名单 + subprocess 隔离 + 真实测试用例 | 代码题不再靠 LLM "猜"对错 |
| **追问策略树** | 5 分类决策而非无脑追问 | 更接近真实面试官行为 |

### 2.4 可靠性优化

| 优化项 | 方法 | 效果 |
|--------|------|------|
| **指数退避重试** | 429/超时/连接错误自动重试 + jitter | API 临时故障不中断面试 |
| **熔断器** | 连续失败 N 次后跳过等待期 | 防止对故障服务持续请求 |
| **降级策略** | GPT-4o 不可用 → GPT-4o-mini → 规则引擎 | 面试不因模型故障中断 |
| **自动保存** | 每次回答后自动持久化 | 面试中断后可恢复 |

---

## 3. Agent 优化有哪些常见手段？

### 3.1 Prompt 层
- **Few-shot prompting**: 给几个示例，稳定输出格式
- **Chain of Thought**: 让模型"一步步思考"，提高推理质量
- **System prompt 优化**: 明确角色、约束、输出格式
- **Prompt Caching**: 复用不变的 system prompt，减少 90% 输入成本

### 3.2 推理层
- **ReAct 模式**: Think → Act → Observe，适合工具调用场景
- **Plan-Execute**: 先制定计划再执行，适合复杂多步任务
- **Self-Reflection**: 让模型自己检查输出质量
- **Multi-Agent**: 多个 Agent 协作（辩论、并行、流水线）

### 3.3 输出控制层
- **Structured Output / JSON Mode**: 约束输出格式
- **输出校验 + 自动重试**: 校验失败时告诉模型修正
- **Rule-based 兜底**: 模型输出不合规时用规则修正
- **Temperature 调参**: 结构化任务用低温(0.1-0.3)，创意任务用高温(0.7-1.0)

### 3.4 基础设施层
- **重试 + 退避**: 处理临时 API 故障
- **熔断器**: 防止故障传播
- **降级策略**: 主模型挂了用备选
- **流式输出**: 降低用户感知延迟
- **Token 管理**: 滑动窗口、优先级保留、摘要压缩
- **缓存**: Prompt 缓存、结果缓存（相同 JD 不重复分析）

### 3.5 成本控制层
- **模型分层**: 简单任务用小模型(gpt-4o-mini)，复杂任务用大模型(gpt-4o)
- **规则引擎替代 LLM**: 确定性问题不用模型
- **题库/知识库**: 预置内容减少实时生成

---

## 4. 场景设计题：面试官突然不追问了怎么办？

**问题描述**: 线上环境发现面试官对某些回答从不追问，直接跳下一题。

**排查思路**:

```
1. 查看日志 → 定位具体哪道题的哪个回答不追问
2. 检查评估结果 → 看 FollowUpDecision 是什么
3. 如果 decision 是 MOVE_ON → 检查是否正确评分
4. 如果 decision 是 DEEPEN 但没有追问内容 → LLM 返回了空追问
5. 跟踪 LLM 原始输出 → 是模型没生成追问，还是解析丢了？
```

**根因分析**:

```python
# 可能的根因:
# 1. LLM 返回的 follow_up_decision 是 "move_on"（评分过高）
# 2. 关键词匹配 overfit，match_rate 虚高
# 3. LLM 输出的 JSON 结构异常，解析失败后走了默认值
# 4. 追问被 max_follow_ups 限制（配置问题）
```

**解决方案**:

```python
# 方案 1: 增加评估日志和监控
# 在 evaluator.py 中增加 debug 日志
logger.info(f"追问决策: {evaluation.follow_up_decision} | 原因: {evaluation.follow_up_reason}")

# 方案 2: 追问降级保护
# 在 interviewer.py 中，如果 LLM 返回 move_on 但关键词匹配率低于阈值
if follow_up_decision == MOVE_ON and keyword_match_rate < 0.4:
    # 强制追问（关键词都没答到几个，不该 move_on）
    follow_up_decision = DEEPEN
    follow_up_question = "能展开说说吗？你刚才的回答似乎没有覆盖到这个问题的核心。"

# 方案 3: 输出校验增强
# 在 output_validator.py 中增加业务规则
if data["follow_up_decision"] == "move_on" and match_rate < 0.3:
    result.warnings.append("匹配率低但判断 move_on，疑似误判")
```

---

## 5. 语义检索是如何实现的？

### 整体架构

```
用户查询 (自然语言)
    │
    ▼
┌──────────────────────────────┐
│  Sentence-Transformers        │  文本 → 向量
│  (all-MiniLM-L6-v2)           │  384 维 embedding
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  ChromaDB                      │  向量存储 + 检索
│  - HNSW 索引 (近似最近邻)      │  cosine 相似度
│  - 持久化存储                  │  Top-K 返回
│  - 元数据过滤                  │
└──────────────┬───────────────┘
               │
               ▼
        相似度排序结果
        [{content, metadata, distance}, ...]
```

### 核心代码路径

```python
# memory/vector_store.py
class VectorMemory:
    def remember(self, content, metadata=None, doc_id=None):
        """存储记忆 → Embedding → ChromaDB"""
        self.collection.add(
            documents=[content],
            metadatas=[meta],
            ids=[doc_id],
        )

    def recall(self, query, top_k=5, filter_meta=None):
        """语义检索 → Query Embedding → 最近邻搜索"""
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=filter_meta,
        )
        return formatted_results

# 面试场景中的应用:
# 1. 存储历史面试答案
vm.remember(answer, {"question_type": "technical", "score": 8})

# 2. 面试时检索相似问题
similar = vm.recall("用户说对GIL不太熟悉",
                      filter_meta={"question_type": "technical"})
```

### 为什么用 all-MiniLM-L6-v2？
- 轻量（~80MB），本地运行，无需 API
- 384 维向量，检索速度快
- 中英文混合场景表现好
- 隐私友好（不发送数据到外部）

---

## 6. 向量数据库在项目中具体承担什么作用？

ChromaDB 在项目中有三个角色：

### 6.1 长期记忆存储
```python
# 面试完成后存储关键信息
vm.remember("面试者对 GIL 理解停留在表面", metadata={"category": "weakness"})
# 下次面试时检索
weaknesses = vm.recall("Python 并发")
```

### 6.2 跨会话知识积累
```
面试 1 (Python 后端岗) → 存储弱项: GIL、asyncio
面试 2 (同一个岗位)    → 检索: "之前 Python 相关弱项"
                       → 面试官重点追问 GIL 和 asyncio
```

### 6.3 面试题目去重
```python
# 跨多场面试，避免重复出同样的题
asked_questions = vm.recall("已出过的 GIL 相关题目", top_k=5)
if similar_question_exists:
    pick_another_question()
```

### 对比：为什么不用传统数据库？

| 场景 | 传统 DB (SQLite) | ChromaDB |
|------|-----------------|----------|
| "找和 GIL 相关的历史回答" | LIKE '%GIL%' 只能精确匹配 | 语义匹配，能泛化到"Python 线程锁" |
| "之前面试者的薄弱点" | 需要人工打标签 | 自然语言直接检索 |
| 相似度排序 | 不支持 | 自带 cosine 距离排序 |

---

## 7. LangChain/LangGraph 这些框架为什么用/不用？

### 这个项目选择自己实现，原因：

**1. 学习目的**
这是秋招项目，核心目标是展示对 Agent 底层原理的理解。引用 LangChain 一行代码 `agent.run(task)`，面试官一眼看出你在调包。

**2. 控制力**
```
LangChain: 黑盒抽象，内部做了什么不透明
自己实现: 每一步的 prompt、tool call、retry 完全可控
```

**3. 项目体量**
LangChain 适合需要快速集成多种 LLM、多种工具的复杂场景。本项目只需要 OpenAI/Anthropic + 几类简单工具，自己写更轻量。

**4. 面试加分**
面试官更想听到：
- "我实现了一个 ReAct Agent，核心是一个 while 循环..."
- 而不是 "我调用了 LangChain 的 create_react_agent"

### 如果要用 LangGraph，什么场景适合？

LangGraph 适合**有复杂状态分支的 Agent 流程**。如果项目的面试流程变得非常复杂（多轮追问、多面试官并行、面试者中途打断等），LangGraph 的状态图会很合适：

```python
# LangGraph 伪代码 — 复杂面试分支
graph = StateGraph(InterviewState)
graph.add_node("jd_parse", parse_jd)
graph.add_node("ask_question", ask)
graph.add_node("evaluate", evaluate)
graph.add_conditional_edges("evaluate", decide_next, {
    "follow_up": "ask_question",     # 追问
    "next_question": "ask_question", # 下一题
    "report": "generate_report",     # 结束
})
```

---

## 8. 模型输出结果如何控制规则？

项目使用四层控制体系：

### 层 1: Prompt 约束
```python
# 在 system/user prompt 中明确输出格式
"请严格按照以下 JSON Schema 返回数据"
"follow_up_decision 只能是: deepen|challenge|upgrade|example|move_on"
```

### 层 2: Structured Output
```python
# OpenAI 原生 JSON Mode
response_format={"type": "json_schema", "json_schema": {...}}

# Anthropic: system prompt 中注入 Schema 要求
# 所有 Provider: core/llm.py 的 structured_chat() 方法
```

### 层 3: 输出校验 (output_validator.py)
```python
validator = OutputValidator()
result = validator.validate("evaluation", llm_output)
if result.needs_retry:
    # 告诉 LLM 格式不对，重新生成
    llm_output = await llm.chat(correction_prompt)
if result.fixed_data:
    # 轻微问题自动修正（如中文引号 → 英文引号）
    return result.fixed_data
```

### 层 4: 规则兜底
```python
# evaluator.py — LLM 评分失败时用关键词匹配代替
if not llm_available:
    depth = keyword_match_rate * 8
    structure = 5  # 默认值
    # 不中断面试流程
```

### 具体控制示例 (评估器)

```python
# 控制 1: 关键词匹配 → 正确性分数 (0 API 调用)
correctness = 3 + keyword_match_rate * 6  # [3, 9]

# 控制 2: LLM 深度评估 → 仅分析语义
depth_score = depth_map[llm_data["depth_level"]]  # "深入" → 8

# 控制 3: 追问决策 → 5 分类而非自由文本
if answer_too_short:  follow_up = DEEPEN
elif has_errors:       follow_up = CHALLENGE
elif answer_is_great:  follow_up = UPGRADE
elif too_abstract:     follow_up = EXAMPLE
else:                  follow_up = MOVE_ON

# 控制 4: 最终得分有上限和下限
score = clamp(correctness * 0.35 + depth * 0.25 + structure * 0.20 + relevance * 0.20, 1, 10)
```

---

## 9. 记忆模块是怎么设计的？

### 三层记忆架构

```
┌─────────────────────────────────────────────┐
│  工作记忆 (Working Memory)                    │
│  ContextOptimizer — 优先级混合保留            │
│  - 当前对话上下文                             │
│  - System prompt (永不淘汰)                   │
│  - 最近 N 段完整 Q&A                          │
│  - 历史段压缩为摘要                           │
│  Token 预算: ~8K                              │
├─────────────────────────────────────────────┤
│  短期记忆 (Short-term Memory)                 │
│  ContextManager — 滑动窗口                    │
│  - 保持最近的消息                             │
│  - 按 Token 计数自动截断                      │
│  - 滑动窗口: 先进先出                          │
│  Token 预算: ~4K (单次对话)                    │
├─────────────────────────────────────────────┤
│  长期记忆 (Long-term Memory)                  │
│  VectorMemory — ChromaDB 向量存储             │
│  - 跨会话持久化                               │
│  - 语义检索 (非关键词)                        │
│  - 元数据过滤                                 │
│  存储: 磁盘持久化 (./data/chroma/)            │
└─────────────────────────────────────────────┘
```

### 工作记忆 vs 短期记忆

| 特性 | ContextManager (v1) | ContextOptimizer (v2) |
|------|--------------------|-----------------------|
| 截断策略 | FIFO 滑动窗口 | 优先级评分 + 语义分块 |
| System Prompt | 可能被误淘汰 | CRITICAL 优先级，永不淘汰 |
| 历史处理 | 直接丢弃 | 压缩为摘要保留 |
| 适应性 | 固定窗口大小 | 自适应预算 (任务复杂度) |

---

## 10. 多轮、多会话场景下 Memory 如何处理？

### 场景 1: 单次面试中的多轮对话 (追问)

```
第 1 轮: 面试官出题 → 面试者回答 → 评估 → 追问
第 2 轮: 面试者补充回答 → 再次评估 → 继续追问 or 下一题
...
```

处理方式：**ContextOptimizer 的语义分块**

```python
opt.begin_segment()  # 开始一道题的对话段
opt.add(question_msg, Priority.HIGH)
opt.add(answer_msg, Priority.MEDIUM)
opt.add(follow_up_msg, Priority.HIGH)
opt.add(follow_up_answer, Priority.MEDIUM)
opt.end_segment()

# 裁剪时: 最近的完整段保留，老段压缩为摘要
# "Q1: 面试者回答了 GIL 相关问题，得分 6/10，对多进程方案理解深入但忽略了 asyncio 方案"
```

### 场景 2: 多场面试的跨会话 (不同岗位)

```
面试 1 (Python 后端): 存储 → session_1.json + ChromaDB 向量
面试 2 (Go 后端):    存储 → session_2.json + ChromaDB 向量
面试 3 (Python 后端): 加载 → session_1 的弱项 + session_2 的通识能力
```

处理方式：**SessionManager + VectorMemory 双层存储**

```python
# 1. 完整记录 → 磁盘 JSON
manager = SessionManager()
manager.save(record)  # 完整会话持久化

# 2. 关键信息 → 向量存储
vm.remember(f"面试者对{skill}的理解评分{score}/10",
            metadata={"session_id": sid, "skill": skill, "score": score})

# 3. 新面试开始时检索
weaknesses = vm.recall("Python 相关薄弱点",
                        filter_meta={"score": {"$lt": 6}})

# 跨会话对比
comparison = manager.compare_sessions([sid1, sid2, sid3])
# → {"trend": "📈 上升", "worst_dimension": "深度"}
```

### 场景 3: 面试中断恢复

```python
# 面试进行到第 5 题 → 浏览器崩溃

# 重新打开:
unfinished = manager.list_sessions(status="in_progress")
session = manager.load(unfinished[0].session_id)
# → 恢复到第 5 题，前 4 题的 Q&A 都在
```

---

## 11. 如果系统出现异常，整体的容错和异常处理机制怎么设计？

### 分层容错架构

```
┌─────────────────────────────────────────┐
│         Interview Loop (main.py)         │
│         InterviewSafeContext              │
│         异常时自动保存进度                │
├─────────────────────────────────────────┤
│         Interviewer (业务逻辑)            │
│         @safe_execute 装饰器              │
│         返回 fallback 值，不中断流程      │
├─────────────────────────────────────────┤
│         LLM Call (core/llm.py)           │
│         with_retry()                     │
│         ├─ L1: 指数退避重试 (3次)        │
│         ├─ L2: 熔断器 (5次失败→等待60s)  │
│         └─ L3: 降级 (备用模型/规则引擎)  │
├─────────────────────────────────────────┤
│         Data Persistence                 │
│         SessionManager                   │
│         ├─ 每次回答后自动保存            │
│         └─ 启动时检测未完成会话          │
└─────────────────────────────────────────┘
```

### 具体场景处理

| 异常场景 | 处理方式 | 用户体验 |
|---------|---------|---------|
| API 限流 (429) | 指数退避重试 3 次，间隔 1s/2s/4s + jitter | 无感，自动恢复 |
| API 超时 | 切换到备用模型 (gpt-4o → gpt-4o-mini) | 可能延迟 2-3s |
| 主模型完全不可用 | 降级到规则引擎（关键词评分、题库出题） | 功能降级但面试不中断 |
| LLM 返回格式错误 | 告诉 LLM 修正，最多重试 2 次 | 无感 |
| 磁盘空间不足 | 面试开始时健康检查告警 | 面试前阻止，不丢失数据 |
| 面试中崩溃 | InterviewSafeContext 自动保存 | 重启后可恢复 |
| 用户输入超长 | 截断到 2500 字符后仍然评估 | 无感 |
| 代码执行超时 | subprocess timeout 5s 后 kill | 返回"代码执行超时" |

### 代码示例

```python
# core/error_handler.py - 安全执行装饰器
@safe_execute("evaluator", "答案评估", fallback_value=default_evaluation)
async def evaluate(self, question, answer):
    ...

# core/retry.py - 带退避的重试
result = await with_retry(
    fn=lambda: self.llm.chat(messages),
    config=RetryConfig(max_retries=3, jitter=True),
    fallback_fn=lambda: self.rule_based_evaluate(answer),
)

# core/error_handler.py - 面试安全上下文
async with InterviewSafeContext(interviewer, session_manager):
    await interviewer.submit_answer(answer)
# 即使异常，进度也已被保存
```

---

## 12. 上下文处理场景题：如何优化 Context 管理？

### 场景描述

面试进行到第 12 轮（含追问），对话历史已经积累了：
- 1 条 system prompt (~200 tokens)
- 1 条 JD 分析 (~300 tokens)
- 8 道题 × 平均 3 轮 (Q+追+答) = 24 条消息 (~4000 tokens)
- 总 token 已接近 8K 限制

**问题**: 再继续，要么超出 context window，要么最早的消息被截断（可能含重要信息）。

### 优化方案

```python
# memory/context_optimizer.py 的实现

# 1. 优先级评分 — 不同消息不同权重
opt.add_system("你是面试官...")              # CRITICAL  — 永不淘汰
opt.add(Message(role=USER, content="JD分析结果..."), Priority.VERY_HIGH)
opt.add(current_question, Priority.HIGH)      # HIGH      — 当前题
opt.add(previous_answer, Priority.MEDIUM)     # MEDIUM    — 可压缩
opt.add(warmup_text, Priority.LOW)            # LOW       — 可丢弃

# 2. 语义分块 — 按 Q&A 段保留
# 8 道题 = 8 个段。裁剪时不会切断一道题的追问链。

# 3. 混合保留策略
# 保留: system prompt + 当前题 Q&A (完整)
#      + 最近 3 道题的完整 Q&A (recency)
#      + 最早 5 道题中评分最高的 2 道 (importance)
#      + 其余压缩为摘要 (1-2 句话/段)

# 4. 自适应预算
if current_question.difficulty >= 4:  # 困难题
    opt.adaptive_rebudget(0.8)  # 给回答留更多空间
```

### 效果对比

| | 简单滑动窗口 | 优先级混合保留 |
|------|------------|--------------|
| System Prompt 安全 | ❌ 可能被淘汰 | ✅ CRITICAL 优先级 |
| 历史信息保留 | ❌ 全丢 | ✅ 重要题保留 + 其余压缩 |
| 对话完整性 | ❌ 可能切断追问链 | ✅ 按语义段裁剪 |
| Token 利用率 | ~70% | ~95% |

---

## 13. 关键设计决策 & Trade-off

### 为什么自己写 Agent 循环而不是用 LangChain？

**自主实现的好处**:
- 完全掌控每一步的 prompt 构建和 tool call 处理
- 面试中可以解释"我实现了一个 ReAct Agent，while 循环 + tool registry"
- 更轻量，没有 LangChain 的额外抽象层开销

**如果换成 LangChain 会怎样**:
- 优点: 快速接入更多 LLM provider、内置更多 tool 集成
- 缺点: 黑盒调试困难、版本更新频繁 breaking change、面试中被认为是调包

### 为什么用规则引擎而不是全用 LLM？

- **成本**: LLM API 调用有费用，JD 解析如果每次都用 LLM 每次面试多花 $0.02-0.05
- **确定性**: 规则匹配同样的 JD 永远返回同样结果（利于调试和监控）
- **速度**: 规则匹配 < 10ms，LLM 调用 1-3s
- **可靠性**: 规则引擎不依赖外部 API，永不挂

规则覆盖 ~70-90%，LLM 只处理真正需要语义理解的部分（职责描述、面试重点推断）。

### 为什么引入复杂度（熔断器、降级链）而不直接用 try/except？

面试场景的特殊性：
- 用户等待时间长 → 体验差
- 面试中断 → 失去练习机会
- 面试结果是评估依据 → 需要可靠

生产级的 Agent 必须考虑故障场景，这不是过度设计。
