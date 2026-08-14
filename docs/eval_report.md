# 评估器评测报告 (LLM-as-judge)

> 生成时间: 2026-08-13 | 模式: Mock（框架验证） | 重复次数: 3 | 样本数: 30 | LLM 调用: 90 次 | 耗时: 0.0s

## 1. 评分一致性（同一回答评 N 次的标准差）

- 平均标准差: **0.0**（满分 10 分制）
- 不稳定样本占比 (std > 0.5): **0%**

## 2. 评分准确性（LLM vs 人工标注）

- MAE (平均绝对误差): **1.31**
- Pearson 相关: **0.929**（越接近 1 越能区分好坏回答）

| 质量档 | 样本数 | 人工均分 | LLM 均分 | MAE |
|--------|:---:|:---:|:---:|:---:|
| high | 10 | 8.6 | 7.6 | 0.98 |
| mid | 10 | 5.1 | 6.0 | 1.03 |
| low | 10 | 2.1 | 4.1 | 1.91 |

## 3. 追问质量（贴题率 — 追问与题目/要点/回答的关键词呼应）

- 非空追问: 25 条，贴题率: **100%**

## 4. 逐样本明细

| 题 | 档 | 人工分 | LLM 均分 | std | 决策 | 追问贴题 |
|----|----|:---:|:---:|:---:|:---:|:---:|
| PY001 | high | 8.5 | 7.8 | 0.0 | move_on | ❌ |
| PY001 | mid | 5.5 | 6.4 | 0.0 | move_on | ✅ |
| PY001 | low | 2.5 | 4.1 | 0.0 | challenge | ✅ |
| DB001 | high | 9.0 | 8.2 | 0.0 | move_on | ❌ |
| DB001 | mid | 5.0 | 6.2 | 0.0 | example | ✅ |
| DB001 | low | 2.0 | 4.1 | 0.0 | challenge | ✅ |
| AI001 | high | 8.5 | 7.1 | 0.0 | example | ✅ |
| AI001 | mid | 5.0 | 5.0 | 0.0 | challenge | ✅ |
| AI001 | low | 2.0 | 3.9 | 0.0 | challenge | ✅ |
| GO001 | high | 8.0 | 7.5 | 0.0 | example | ❌ |
| GO001 | mid | 5.0 | 5.5 | 0.0 | challenge | ✅ |
| GO001 | low | 2.5 | 4.1 | 0.0 | challenge | ✅ |
| NET003 | high | 8.5 | 7.3 | 0.0 | example | ✅ |
| NET003 | mid | 5.0 | 6.2 | 0.0 | example | ✅ |
| NET003 | low | 2.0 | 4.1 | 0.0 | challenge | ✅ |
| SD001 | high | 9.0 | 7.4 | 0.0 | move_on | ✅ |
| SD001 | mid | 5.5 | 4.8 | 0.0 | challenge | ✅ |
| SD001 | low | 2.5 | 4.5 | 0.0 | challenge | ✅ |
| JV004 | high | 8.5 | 7.8 | 0.0 | move_on | ✅ |
| JV004 | mid | 5.0 | 7.6 | 0.0 | move_on | ✅ |
| JV004 | low | 2.0 | 3.9 | 0.0 | challenge | ✅ |
| FE003 | high | 8.5 | 7.8 | 0.0 | move_on | ✅ |
| FE003 | mid | 5.0 | 6.8 | 0.0 | example | ✅ |
| FE003 | low | 2.0 | 3.9 | 0.0 | challenge | ✅ |
| MQ003 | high | 8.5 | 6.8 | 0.0 | challenge | ❌ |
| MQ003 | mid | 5.0 | 6.1 | 0.0 | challenge | ✅ |
| MQ003 | low | 2.0 | 4.1 | 0.0 | challenge | ✅ |
| DB002 | high | 8.5 | 8.0 | 0.0 | move_on | ❌ |
| DB002 | mid | 5.0 | 5.3 | 0.0 | challenge | ✅ |
| DB002 | low | 2.0 | 3.9 | 0.0 | challenge | ✅ |

---

*报告由 eval/judge_eval.py 生成，可随时复跑。*
