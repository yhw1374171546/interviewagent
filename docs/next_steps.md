# 面试模拟 Agent — 后续开发 Prompt（交接文档）

> 用途: 给新的 AI 会话/协作者提供完整上下文，可直接复制全文作为新会话的起始 prompt。
> 最后更新: 2026-08-13（阶段 0-2b 完成后）

---

## 一、项目现状

### 1.1 定位

AI 驱动的全真模拟面试系统：输入简历/JD → 规则引擎+LLM 混合解析 → 题库检索出题 → 双引擎评估（关键词+LLM）+ 智能追问 → 评估报告。

技术栈: Python 3.11+ / asyncio / FastAPI + 原生 JS（单视图 DeepSeek 式 UI）/ DeepSeek API（OpenAI 兼容协议）/ ChromaDB（可选，缺失时三级降级）。

### 1.2 架构速览

| 目录 | 职责 |
|------|------|
| `interview/` | 核心链路: interviewer(7 状态机) → jd_parser(规则+LLM 兜底) / question_gen(题库+LLM 微调) / evaluator(双引擎+边界防护) / report；memory_context.py(轮内摘要+跨会话弱项，三级降级) |
| `core/` | llm(多 Provider+重试+调用级指标)、retry(退避+熔断+降级)、agent(ReAct)、orchestrator(多 Agent)、mock_llm(离线演示)、error_handler |
| `memory/` | context(滑动窗口)、context_optimizer(优先级保留)、vector_store(ChromaDB)、summarizer |
| `web/` | FastAPI server.py + 原生前端（index.html/style.css/app.js） |
| `eval/` | LLM-as-judge 评测框架（dataset.py 30 样本 + judge_eval.py 三指标） |
| `benchmark.py` | 五项工程指标（v1/v2 配置对比，离线可复现） |
| `tests/` | 46 个测试（评估器健壮性/记忆/可观测性/judge_eval/基础） |
| `docs/` | README、CHANGELOG（开发日志，面试复习材料）、architecture、optimization、interview_qa、eval_report、next_steps |

### 1.3 已完成（阶段 0-2b）

- 阶段 0: Git 仓库 + GitHub Actions CI（pytest+benchmark+demo+ruff）+ ruff 零错误 + 徽章 + 截图
- 阶段 1: 记忆接入面试链路（轮内摘要"翻旧账" + 跨会话弱项检索）、重试/熔断/降级真正生效（修复 with_retry 协程未执行 bug）、DeepSeek v4 真实联调（推理模型 max_tokens 适配）、模型路由（flash 高频/pro 报告，评估延迟 17.6s→5.4s）、单视图 Web（历史常驻侧边栏）
- 阶段 2a: LLM-as-judge 评测集（10 题×3 档人工标注）→ 发现并修复中文关键词匹配缺陷 → MAE 1.50→0.94、Pearson 0.923→0.991、追问贴题率 100%
- 阶段 2b: 调用级可观测性（延迟/token/成本按阶段记录、会话快照持久化、本场统计卡片、GET /api/stats 全局聚合）

### 1.4 关键指标现状（简历引用）

| 指标 | 数值 |
|------|------|
| 题库领域匹配率 | 73% → 92%（91 题 12 方向） |
| 判题缺陷检出率 | 83% → 100% |
| 评估评分 MAE / Pearson | 0.94 / 0.991（vs 人工标注） |
| 评分一致性 std | 0.16（同答评 3 次） |
| 追问贴题率 | 100% |
| 单场面试成本 | ¥0.049（8 题真实 DeepSeek） |
| 自动化测试 | 46 个，CI 全绿 |

---

## 二、环境与运行

- 目录: `C:\Users\13741\Desktop\code\agent`（Windows + git-bash）
- Python: base 环境（有 fastapi/uvicorn/pytest/ruff/httpx/python-multipart/pypdf/pytest-asyncio；无 openai SDK/chromadb/tiktoken —— SDK 懒加载设计，测试/benchmark/demo 不需要）
- 启动 Web（注意 --app-dir，shell cwd 可能漂移）:
  ```bash
  python -m uvicorn web.server:app --app-dir C:/Users/13741/Desktop/code/agent --host 127.0.0.1 --port 8000
  ```
- `.env` 已配置 DeepSeek（LLM_MODEL=deepseek-v4-pro、LLM_FAST_MODEL=deepseek-v4-flash、真实 key）— **绝不提交**
- Git: origin `https://github.com/yhw1374171546/interviewagent.git`，main 分支；**push 经常因网络失败，重试或让用户手动推**
- CI: `.github/workflows/ci.yml`（最小依赖 pytest pytest-asyncio rich python-dotenv）

## 三、每步改动后的强制验证流程

```bash
python -m ruff check .              # 必须 0 error
python -m pytest tests/ -q          # 必须全绿（46+）
node --check web/static/app.js      # 改前端时
PYTHONIOENCODING=utf-8 python benchmark.py   # 改核心逻辑时
PYTHONIOENCODING=utf-8 python demo.py        # 冒烟
# 改后端且 Web 在跑 → 重启后台 uvicorn；改静态文件 → 用户 Ctrl+F5
```

- git commit: Conventional 前缀（feat/fix/perf/test/docs/chore）+ 中文 + 尾注 `Co-Authored-By: Claude <noreply@anthropic.com>` + push
- **每次完成一个模块必须追加 CHANGELOG.md**（时间正序: 做了什么/为什么/实测数据/经验教训）——这是面试复习材料，不许跳过

---

## 四、未完成工作（按优先级）

### 4.1 阶段 2c: Streaming 接入 Web（SSE 逐字显示）【下一个】

- 现状: `core/llm.py` 已有 `stream_chat()`（OpenAI/Anthropic 双实现），Web 完全没用；Mock 的 stream 是 yield 整块（天然兼容）
- 目标: 追问文本、报告文字经 SSE 逐字显示；评估 JSON 不适合流式（保持整块返回）
- 方案建议:
  1. Web 新增流式端点（如 `POST /api/interviews/{id}/answer` 返回 evaluation 整块 + `GET /api/interviews/{id}/stream?task=report` 流报告），或最小实现：报告生成改流式
  2. 前端用 fetch ReadableStream 逐字 append，复用 typing-dots 过渡
  3. **注意**: `stream_chat` 目前不走 `usage_stats` 指标收集，接入流式时必须补计数（否则可观测性漏数据）
- 测试: FakeStreamLLM 单测 + 前端冒烟

### 4.2 阶段 3: 测试补全（目标 80%+ 覆盖率）

现有测试只覆盖 evaluator/memory/observability/judge_eval。待补（全部离线可跑）:

- `jd_parser.py`: 词边界（已修过）、LLM 兜底 >50 字符触发、岗位猜测、malformed JSON 降级
- `question_bank.py`: 倒排索引、分层配额、去重、难度分层、排序
- `question_gen.py`: 五类配比、LLM 微调失败降级、补充生成、通识兜底
- `interviewer.py`: 状态机全跳转（正常/追问上限/跳过/提前结束）、序列化 round-trip
- `session_manager.py`: CRUD、置顶排序、重命名回退、index.json 并发写（考虑加锁）
- `output_validator.py`: extract_json 各格式、repair_truncated_json、Schema 校验/修正
- `code_judge.py`: 输出比对（新修的真 bug 必须有回归测试）、超时 kill、AST 拒绝
- `core/retry.py`: 退避计算、熔断三态、降级链、非可重试直通
- 用 pytest-cov 出覆盖率报告，README 加覆盖率徽章

### 4.3 阶段 4: 交付与叙事

1. **Docker**: Dockerfile(python:3.13-slim) + docker-compose（web 服务、.env 挂载、data 卷）+ README 一键启动
2. **技术博客**（加分项）: 《从 0 实现一个面试 Agent：混合架构、双引擎评分与容错设计》→ 牛客/掘金，README 放链接
3. **简历定稿**: 基于最终指标整合 bullet（docs/interview_qa.md 有 13 题详解可参考）
4. **演示视频**（可选）: 1 分钟屏录

### 4.4 产品级增强（低优先级，按兴趣）

- Web 统计页（/api/stats 数据已有，做个简单页面 + 侧边栏显示本场成本）
- 全量评测: `python eval/judge_eval.py --limit 10 --repeat 3` 出完整 30 样本报告
- Prompt 集中管理（prompts.py 版本化 + A/B）
- 语义缓存（相似 JD 复用解析）
- 自适应难度
- 安装 chromadb 后验证向量记忆真实链路（当前降级进程内）
- Web 侧边栏显示每场成本

---

## 五、重要约定与坑（必须遵守）

1. **安全**: 绝不提交 `.env`/API key；提交前 `git ls-files | grep -i env` 应只有 `.env.example`
2. **字段命名**: 前后端 JSON 统一 snake_case（`can_resume` 事故教训）；JS 端新字段读取同样用 snake_case
3. **hidden 属性**: CSS 有 `[hidden]{display:none!important}` 兜底，但新组件尽量用类名控制显隐
4. **推理模型**: DeepSeek v4 先推理后作答——新增 LLM 调用点 max_tokens 不要低于 1000（评估 2000/报告 3000 是现有标准）
5. **LLM SDK 懒加载**: `core/llm.py` 不顶层 import openai/anthropic；新增 Provider 遵循同模式
6. **异步包装**: 判断协程用 `inspect.isawaitable(result)` 而非 `iscoroutinefunction(fn)`（lambda 坑，已修过）
7. **ruff**: UP042（str,Enum 模式）在 ignore；其余规则零容忍
8. **Windows**: 中文输出加 `PYTHONIOENCODING=utf-8`；uvicorn 用 --app-dir
9. **测试原则**: 全部测试离线可跑（Mock/FakeLLM），CI 无真实 API；真实 LLM 评测先 --mock 验框架再跑真数据
10. **CHANGELOG 纪律**: 见第三节

## 六、最终验收标准

- [ ] 2c streaming 上线，追问逐字显示，指标不漏计
- [ ] pytest 覆盖率 ≥80% + cov 徽章
- [ ] Docker 一键启动
- [ ] 全量 30 样本评测报告入库（docs/eval_report.md）
- [ ] 博客发布 + 简历定稿
- [ ] README 截图随 UI 变化更新
