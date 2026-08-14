# 功能增强量化评测报告（Before / After）

> 生成: `python eval/feature_eval.py`（全离线，Mock LLM + 规则判定，可一键复现）

## Agent 开发常用指标速查

| 类别 | 指标 | 说明 | 本项目出处 |
|------|------|------|-----------|
| **任务完成** | 任务成功率 / Pass@k | k 次尝试内完成任务比例 | benchmark.py（判题检出率） |
| **质量** | 正确率 / 贴题率 | 输出与目标的匹配度 | feature_eval.py（追问贴题率） |
| | 一致性 std | 同输入多次输出方差 | judge_eval.py（评分一致性 0.16） |
| | MAE / Pearson | vs 人工标注的误差/排序 | judge_eval.py（0.94 / 0.991） |
| **效率** | 延迟 Latency | 单次/整场耗时 | benchmark.py（评估 5.4s） |
| | Token / 成本 | 单场消耗 | benchmark.py（¥0.049/场） |
| | LLM 调用次数 | 完成任务所需调用 | benchmark.py（6 次/3 题） |
| **检索（RAG）** | 检索命中率 | Top-k 是否含相关文档 | feature_eval.py（57%） |
| | MRR / NDCG | 相关文档排名质量 | （后续可加） |
| **可靠性** | 降级成功率 | 主路径失败后降级是否成功 | core/retry.py（测试覆盖） |
| **增强对比** | 答案长度 / 信息密度 | 内容完整度 proxy | feature_eval.py（36→86 字） |

## 一、追问贴题率（Before: 5 分类规则 → After: FollowUpAgent 自主决策）

| 指标 | Before | After | 变化 |
|------|:---:|:---:|:---:|
| 追问贴题率（30 样本评测集） | 0%（22 条追问） | **100%**（25 条） | +100pp |
| Agent 自主决定继续追问比例 | — | 83.3% | — |

> Before 为 Mock 模式评估器的通用话术（如"能展开说说吗？"），与题目/要点/回答无关键词呼应；
> FollowUpAgent 用「未命中要点」生成贴题追问（"你刚才没有提到「写屏障」，能展开说说吗？"）。
> 真实 LLM 评估路径的贴题率见 judge_eval.py = 100%。

## 二、参考答案质量（Before: 关键词要点 → After: RAG 面经）

| 指标 | Before | After | 变化 |
|------|:---:|:---:|:---:|
| 平均答案长度 | 36.3 字 | **85.8 字** | +136% |
| 信息密度（技术关键词数） | 7.2 | **13.4** | +86% |
| RAG 检索命中率（面经库覆盖） | — | **57.0%**（53/93 题） | — |

> Before 的参考答案是"答题要点：GIL定义、CPU密集vsIO密集"这类关键词罗列；
> After 是检索到的真实面经内容（"GIL 是 CPython 的全局解释器锁，保证同一时刻只有一个线程执行 Python 字节码…"），
> 完整性/可读性显著提升。面经库 22 条覆盖题库 93 题的高频考点（57%），未命中的题回退关键词要点兜底。

## 三、可复现

```bash
python eval/feature_eval.py     # 功能增强前后对比（本次报告）
python eval/judge_eval.py --mock  # 评估器三指标（MAE/Pearson/一致性）
python benchmark.py             # 工程指标（延迟/token/成本/调用次数）
```

全部离线可跑，无真实 API 依赖。
