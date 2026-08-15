# 面试模拟 Agent — 后续开发 Prompt（交接文档）

> 用途: 给新的 AI 会话/协作者提供完整上下文，可直接复制全文作为新会话的起始 prompt。
> 最后更新: 2026-08-15（A/B/C 组优化全部完成后，此文档与 README/CHANGELOG 同步）

---

## 一、项目现状

### 1.1 定位

AI 驱动的全真模拟面试系统：输入简历/JD → 规则引擎+LLM 混合解析 → 题库检索出题 → 双引擎评估（关键词+LLM）+ 智能追问 → 评估报告（SSE 流式）。

技术栈: Python 3.13 / asyncio / FastAPI + 原生 JS（DeepSeek 式 UI）/ DeepSeek API（OpenAI 兼容协议）/ ChromaDB + sentence-transformers（向量记忆，已装）/ 沙箱判题（Python AST 白名单 / C++ g++）。

### 1.2 架构速览

| 目录 | 职责 |
|------|------|
| `interview/` | interviewer(7 状态机+自适应难度) / jd_parser(规则+LLM 兜底+语义缓存) / question_bank(193 题+倒排索引) / evaluator(双引擎+多评委+评分校准+注入防护) / report(SSE 流式+RAG 参考答按) / code_judge(69 用例沙箱判题) / multi_judge(双评委仲裁) / score_calibration / adaptive / cost_control / context_budget / semantic_cache / injection / loop_detector |
| `core/` | llm(多 Provider+重试+流式+reasoning_content)、retry、agent(ReAct)、orchestrator(多 Agent)、mock_llm(离线演示)、loop_detector |
| `web/` | FastAPI server.py（懒加载 shared_memory/jd_cache/multi_judge + 4 项能力全启用）+ 原生前端 |
| `eval/` | judge_eval（30 样本三指标，支持并行）、feature_eval、tool_use_eval、failure_injection_eval |
| `benchmark.py` | 六项工程指标（S1-S6，v1/v2 配置对比，离线可复现） |
| `tests/` | 390 个测试（全离线可跑），覆盖率 87%（core/llm.py 仅 50% 待补） |
| `docs/` | README、CHANGELOG（面试复习材料）、architecture、optimization、interview_qa、eval_report、feature_eval_report、resume*、knowledge（397 条知识库） |

### 1.3 关键指标现状（简历引用，全部可复现）

| 指标 | 数值 |
|------|------|
| 自动化测试 | **390 个**，CI 全绿，覆盖率 87%（门禁 80%） |
| 题库 | 193 题（93 原创 + 100 LC）+ 69 道判题用例 |
| RAG 数据源 | 519 条（22 内置 + 397 知识库 + 100 LC 题解），索引加速 **6.6×** |
| 评估评分 MAE / Pearson | 真实 30 样本 **0.76 / 0.952**（校准后；未校准 1.2/0.773） |
| 多评委一致性 std | 0.22 → **0.14**（std -36%） |
| 追问贴题率 | 真实 **96.7%** / mock 100% |
| 单场面试成本 | ≈¥0.02（8 题真实 DeepSeek 实测） |
| 能力落地 | 多评委 + 校准 + 自适应难度 + JD 缓存 **全接入 Web 生产**，`test_web_capabilities.py` 回归锁住 |

### 1.4 已完成（全部阶段）

- 阶段 0-2b: 基础设施/记忆/重试/模型路由/评测闭环/可观测性/SSE 流式
- 阶段 3: 测试 390 个 + 覆盖率 87%（>80% 门禁）
- A 组: 多评委接入 Web、评分校准接入 Web、自适应难度接入 Web（能力落地三件套 + 回归测试）
- B 组: 真实 30 样本评测、多评委仲裁、评分校准、tool_use_eval、failure_injection、JD 语义缓存、成本预算、上下文预算、Web 统计页
- C 组: RAG 索引加速（6.6×）、多题评估并行化（Semaphore 限流）、报告延迟优化（参考答按并行 + 与叙事重叠）

---

## 二、环境与运行

- 目录: `C:\Users\13741\Desktop\code\agent`（Windows）
- Python: base 环境（Python 3.13；fastapi/uvicorn/pytest/ruff/httpx/pytest-asyncio/chromadb/sentence-transformers/tiktoken 均已装）
- 启动 Web:
  ```bash
  python -m uvicorn web.server:app --app-dir C:/Users/13741/Desktop/code/agent --host 127.0.0.1 --port 8000
  ```
- `.env` 已配置 DeepSeek（LLM_MODEL=deepseek-v4-pro、LLM_FAST_MODEL=deepseek-v4-flash、真实 key）— **绝不提交**
- Git: origin `https://github.com/yhw1374171546/interviewagent.git`，当前分支 `feat/code-judge`（feature 分支工作流，merge main 需用户亲自验证）；**push 常因网络失败，用 `git status -sb` 确认 ahead 消失**
- 网络: 本机 HuggingFace 直连断（需 hf-mirror + NO_PROXY + HF_HUB_OFFLINE 测试环境变量）；系统代理 127.0.0.1:7888 会干扰 Web POST（单测用纯函数避开网络层）

## 三、每步改动后的强制验证流程

```bash
python -m ruff check .              # 必须 0 error
python -m pytest tests/ -q          # 必须全绿（390）
node --check web/static/app.js      # 改前端时
PYTHONIOENCODING=utf-8 python benchmark.py   # 改核心逻辑时（S1-S6 全过）
PYTHONIOENCODING=utf-8 python demo.py        # 冒烟
```

- git commit: Conventional 前缀（feat/fix/perf/test/docs/chore）+ 中文，**无 Co-Authored-By**
- **每次完成一个模块必须追加 CHANGELOG.md**（时间正序: 做了什么/为什么/实测数据/经验教训）——这是面试复习材料，不许跳过

---

## 四、剩余工作（按优先级）

### 4.1 C3 演示视频（可选加分项）

- 1 分钟屏录：JD 输入 → 面试问答（含追问）→ SSE 流式报告
- 前置: 写 `docs/demo_script.md` 分镜脚本 + 一键启动命令（视频需用户本人录屏）

### 4.2 B 组产品体验（未做项）

- B1 面试中途刷新不丢题（断点恢复已有 SessionManager 基础，缺 Web 层接线）
- B2 代码题自测 UX（用户在编辑器里写完先本地跑再提交）
- B3 报告导出 PDF（pypdf 已装，缺导出端点 + 前端按钮）
- B4 历史分数趋势图（/api/stats 数据已有，缺前端图表）

### 4.3 D 组架构/规范（未做项）

- D1 Prompt 集中管理（prompts.py 版本化 + A/B，当前散落在各模块）
- Docker 验证（Dockerfile 已有一版？需验证 docker-compose 一键启动 + 数据卷）
- `core/llm.py` 覆盖率仅 50% 待补（CI 门禁是全局 80%，llm.py 是最大洼地）

### 4.4 已关闭（不要再做）

- ❌ 2c streaming 接入 Web —— **已完成**（追问/报告 SSE 流式 + 指标计数）
- ❌ 语义缓存 —— **已完成**（JDSemanticCache，embedding 余弦 0.9 阈值，FIFO 50 条）
- ❌ 自适应难度 —— **已完成**（且已接入 Web）
- ❌ Web 统计页 —— **已完成**（GET /api/stats + 聚合统计）
- ❌ 全量 30 样本评测 —— **已完成**（docs/eval_report.md + feature_eval_report.md）

---

## 五、重要约定与坑（必须遵守）

1. **安全**: 绝不提交 `.env`/API key；提交前 `git ls-files | grep -i env` 应只有 `.env.example`
2. **字段命名**: 前后端 JSON 统一 snake_case；JS 端新字段读取同样用 snake_case
3. **推理模型**: DeepSeek v4 先推理后作答——新增 LLM 调用点 max_tokens 不要低于 1000（评估 2000/报告 3000 是现有标准）；**流式/工具调用必须回传 reasoning_content 与 tool_call_id**（OpenAI 协议 400 坑，已修）
4. **LLM SDK 懒加载**: `core/llm.py` 不顶层 import openai/anthropic；**一切可能触网/加载模型的组件（chroma/embedding）必须懒加载**（顶层初始化会卡死服务启动）
5. **测试离线原则**: 全部测试离线可跑；涉及 HF/embedding 的测试文件顶部先设 `HF_ENDPOINT=hf-mirror` + `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`（必须在 import huggingface_hub 之前）
6. **度量如实原则**: 真实 vs mock 双口径标注；性能数字分「本机可复现」与「机制演示」两类，不混写
7. **能力落地原则**: 评测证明有效的能力必须接入生产（Web Interviewer 实际传参），且用回归测试锁住——「宣传与实现一致」
8. **Windows**: 中文输出加 `PYTHONIOENCODING=utf-8`；uvicorn 用 --app-dir；PowerShell 下 git 进度走 stderr 报 exit 1 是误报，用 `git status -sb` 判断真实状态
9. **ruff**: UP042（str,Enum 模式）在 ignore；其余规则零容忍
10. **CHANGELOG 纪律**: 见第三节

---

## 六、最终验收标准

- [x] streaming 上线（SSE 逐字 + 指标不漏计）
- [x] pytest 覆盖率 ≥80%（当前 87%）
- [ ] Docker 一键启动验证
- [x] 全量 30 样本评测报告入库
- [x] 多评委/校准/自适应难度/语义缓存接入生产 + 回归测试锁住
- [x] RAG 索引加速 / 多题评估并行 / 报告延迟优化（C 组）
- [ ] 博客发布（docs/blog.md 已有草稿，待发布）+ 简历定稿（docs/resume* 已同步）
- [ ] 演示视频（可选）
