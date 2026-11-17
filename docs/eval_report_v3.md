# RAG 检索评测报告

- 评测集: `data/eval/retrieval_set.json`  - top_k = 5

## literal 层（15 题，纯小区名）

| 模式 | recall@5 | recall@1 | MRR | nDCG@5 | precision@5 |
|---|---|---|---|---|---|
| dense | 1.000 | 1.000 | 1.000 | 1.000 | 0.920 |
| sparse | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid_nofilter | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid_no_div | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| hybrid | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## mixed 层（20 题，小区名 + 户型）

| 模式 | 小区 recall@1 | 小区 recall@5 | 户型满足@1 | **strict@1** | distinct@5 |
|---|---|---|---|---|---|
| dense | 1.000 | 1.000 | 0.300 | **0.300** | 0.340 |
| sparse | 1.000 | 1.000 | 0.900 | **0.900** | 0.200 |
| hybrid_nofilter | 1.000 | 1.000 | 0.600 | **0.600** | 0.230 |
| hybrid_no_div | 1.000 | 1.000 | 1.000 | **1.000** | 0.200 |
| hybrid | 1.000 | 1.000 | 1.000 | **1.000** | 0.200 |

## semantic 层（15 题，无小区名锚点）

| 模式 | 约束满足@1 | 约束满足@5 | distinct@5 |
|---|---|---|---|
| dense | 0.556 | 1.000 | 0.947 |
| sparse | 0.778 | 1.000 | 0.907 |
| hybrid_nofilter | 0.889 | 1.000 | 1.000 |
| hybrid_no_div | 1.000 | 1.000 | 0.893 |
| hybrid | 1.000 | 1.000 | 1.000 |

## 总览

| 模式 | strict@1（literal+mixed 35 题） | 约束满足@1（mixed+semantic 35 题） |
|---|---|---|
| dense | 0.600 | 0.379 |
| sparse | 0.943 | 0.862 |
| hybrid_nofilter | 0.771 | 0.690 |
| hybrid_no_div | 1.000 | 1.000 |
| hybrid | 1.000 | 1.000 |

- 注 1：`hybrid` = 线上路径（正则抽 filters 参与约束分层）；`hybrid_nofilter` = 同一条召回链但
  filters=None，退化为纯相关性排序。两档在 mixed 层的差值就是「约束排序」本身的贡献。
- 注 2：strict@1 = 第一名「命中点名小区 **且** 户型/朝向约束全中」。
- 注 3：约束满足按 `page_content` 关键词包含判断，属弱标注，只做横向比较。
- 注 4：语料 288 小区 / 841 房源，一小区多套房，所以 recall@k 容易饱和，看 recall@1 与 strict@1。

## Supervisor 路由评测

- 准确率: 0.950 (38/40)

| 期望 \ 实际 | 次数 |
|---|---|
| analytics → analytics | 10 |
| house_search → house_search | 11 |
| hybrid → analytics | 2  ← 错 |
| hybrid → hybrid | 7 |
| knowledge → knowledge | 10 |

### 路由错误明细

| id | 问题 | 期望 | 实际 | 依据 |
|---|---|---|---|---|
| T036 | 望京那边怎么样 | hybrid | analytics | LLM 分类 |
| T039 | 刚需上车买哪里好 | hybrid | analytics | LLM 分类 |
