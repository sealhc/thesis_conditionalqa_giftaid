# A1 语义评价(并入 v9 的说明)

本目录保留你原来的 A1 评审提示(`A1_JUDGE_PROMPT_original.txt`,未改动)。其设计已并入统一的逐步评价框架,**不再有独立的 `a1_semantic_eval.py`**:它的验证太松(见审计),并入后由 `scripts/stepwise_review.py` 的严格验证器接管。

| 你的设计 | 现在在哪里 |
|---|---|
| 四档覆盖 FULL / PARTIAL / MISSING / INCORRECT | 工作表字段 `coverage`;定义写在 `judge_prompts/map.txt` |
| `extra_atoms` 的 SUPPORTED_ADDITIONAL / REDUNDANT / UNSUPPORTED / AMBIGUOUS | `model_atoms[].atom_assessment`(另加 `MATCHES_REFERENCE`,由验证器强制与 coverage 一致) |
| `logic` EQUIVALENT / NOT_EQUIVALENT / UNDETERMINED、`scope` MATCH / MISMATCH / UNCLEAR | `logic_assessment` / `scope_assessment`,不等价或不匹配时必须列出具体问题 |
| `uncertain_cases`、`confidence` | 顶层字段;置信度非 HIGH、有不确定项、逻辑 UNDETERMINED、范围 UNCLEAR、有 AMBIGUOUS 原子 → `needs_human_check=true` |
| 不让评审看到下游输出 | 保留(提示中没有客户决定、流水线或运行编号;有测试) |
| 请求文件 `A1_judge_request.json` + 手工粘贴回复 | `judge --manual-dir DIR`:提示写入 DIR,回复 JSON 存回 DIR,再运行同一命令 |

## 相对原版新增的约束
- 回复必须与工作表严格一致:每个参考条件恰好一次;FULL/PARTIAL/INCORRECT 必须列原子,MISSING 不能列;被 FULL/PARTIAL 覆盖的原子必须是 MATCHES_REFERENCE,其余每个原子恰好评价一次;INCORRECT 的尝试只能是 UNSUPPORTED 或 AMBIGUOUS;所有理由非空;置信度和枚举值必须合法。缺字段是明确拒绝,不是崩溃。
- 参考条件和 A1/A2/A3/B1 输出的内容哈希写进被冻结的 `provenance`:删减参考条件、评审后改动 A1 输出、换了 Gold,都会被拒绝。
- 评审身份(请求/返回的模型、温度、max_tokens、四个提示文件的哈希)写入 `judge_meta`;评审模型与被测模型同名会被拒绝。
- 有了每个原子的评价,**可以计算 precision**(MATCHES_REFERENCE + SUPPORTED_ADDITIONAL + REDUNDANT 占全部原子;AMBIGUOUS 按未支持计)。召回率同时给严格版(仅 FULL)和宽松版(FULL+PARTIAL)。
- 评审结果只是 LLM 标注;`agreement` 对人工标注样本做 Cohen's kappa(四档为类别 kappa),通过前所有评审数字在 `summary` 中标为 UNCALIBRATED。

## 仍然没有做的
- 没有真实调用过任何评审 API,也没有人工标注;一致率结论为空。
- R0 的官方条件语义评价仍需对照官方原文,**不能**用这里的 `G_*` 改写条件;本包不对 R0 运行评审。
