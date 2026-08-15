# 简历投递版 — Agent 开发岗（聚焦 LLM 应用工程与 Agent 生产化）

> 适用于 Agent / LLM 应用开发 / AI 工程岗投递。每条 bullet 一句话核心 + 量化，
> 全部数据可复现（`python eval/*.py` + `python benchmark.py`，真实 API 评测已跑）。
> 配套：`docs/resume_agent_detail.md`（详细版）+ `docs/qa_agent_enhancements.md`（问答）。

## 一句话定位

LLM 应用工程师：自主实现 ReAct Agent 框架（工具调用 / 记忆 / 多 Agent 编排），并完成
生产化治理（注入防护、循环检测、成本控制、混沌测试），全部有真实评测证据。

## 技术栈

Python · asyncio · FastAPI · DeepSeek/OpenAI/Anthropic 多 Provider · ReAct Agent ·
Function Calling · RAG · 结构化输出 · SSE 流式 · 重试/熔断/降级 · 可观测性

## 简历 Bullet（投递版，7 条）

**项目：AI 面试模拟 Agent（个人全栈项目）**

- **Agent 框架**：自主实现 ReAct Agent（推理-行动-观察循环）+ 工具注册中心
  （自动生成 Function Calling Schema）+ 多 Agent 编排（串行/并行/辩论），不依赖 LangChain。
- **工具调用**：真实评测（8 个多步任务 × 5 工具）**工具选择正确率 100%、任务成功率 100%**；
  支持 DeepSeek 推理模式（reasoning_content 回传），评测驱动修复 3 个 function calling 协议 bug。
- **健壮性**：循环检测（同工具/同参数/同结果重复提前终止，省 token）+ **混沌测试**
  （FlakyTool 注入瞬时/持续失败/超时/坏数据，真实 5 场景**无崩溃率 100%**）。
- **安全**：Prompt 注入防护——5 类注入模式（评分操纵/越狱/提示词泄露等）中英双语规则，
  评估/追问/报告三处 LLM 出口拦截降分，0 API 调用。
- **个性化**：自适应难度（答好升级/答差降级，同类型替换）+ 跨会话能力画像，
  弱项自动注入下一场追问，形成个性化闭环。
- **资源治理**：上下文预算守卫（评估 prompt 按优先级裁剪，token 不失控）+ 成本预算控制
  （成本/token 双上限，超 warn 评估自动降级省 LLM、超 hard 强制终止）。
- **量化证据链**：LLM-as-judge 真实 30 样本评测（MAE 1.2 / Pearson 0.773 / 一致性 std 0.22）、
  FollowUpAgent 真实追问贴题率 96.7%、单场 8 题面试成本 ≈¥0.02（真实联调）、348 个测试。

## 量化指标速查

| 指标 | 数值 |
|------|------|
| 工具选择正确率 / 任务成功率（真实评测） | **100% / 100%**（8 任务） |
| 失败注入无崩溃率（真实 5 场景） | **100%** |
| 追问贴题率（真实 30 样本 / mock） | **96.7% / 100%** |
| 评分 MAE / Pearson（真实 30 样本） | **1.2 / 0.773**（早期小样本 0.94 / 0.991） |
| 评分一致性 std | **0.22**（0% 不稳定） |
| 单场 8 题面试成本（真实 DeepSeek） | **≈¥0.02** |
| 自动化测试 / 覆盖率 | **348 个 / 87%** |
