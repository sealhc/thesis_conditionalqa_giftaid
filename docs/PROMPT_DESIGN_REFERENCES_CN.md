# A/B 每步设计及文献依据

## 当前实验比较的是什么

- A（Condition-mediated）：A1 显式生成原子条件和 AND/OR/NOT 表示；A2 显式匹配客户证据；A3 **原文 + IR + 状态**由 LLM 决策。
- B（Direct）：**相同原文**由 LLM 决策，无强制结构化中间状态。
- A 比 B 多两次调用；这是一项 **workflow 对比**，不是已经排除所有调用次数混杂的 IR 纯因果测量。按研究者明确要求，本轮**不增加第三个控制组**。

## A1 `prompts/A1_extract.txt`

输入：Policy + Question + Scenario。输出：可查证的原子命题、适用对象、引用 E-ID、布尔逻辑树。模型不能使用 Gold。取材于下列先例，而不是复制原论文原 Prompt：

1. [Chain of Condition — `prompts_conditionalqa.py`](https://github.com/Infinite-set/Chain-of-Condition/blob/main/prompts_conditionalqa.py)，尤其 `build_construct_prompt`：定位相关先决条件、识别 AND/OR。
2. [Code Prompting — `translation.py`](https://github.com/UKPLab/emnlp2024-code-prompting/blob/main/src/conditionalqa/translation.py)：自然语言政策到显式伪代码/变量条件。
3. [MDCR `parsed.json`](https://github.com/peterbaile/mdcr/blob/main/data/scholarships/parsed.json)：条件原子及嵌套的 ALL/OR 关系数据表示。

**我们的关键改动**：为可逐步审计而要求 JSON AST；要求同时输入 Question + Scenario，保障与 B 同样的外部材料；不向 A1 提供 suggested answer 或标签。

## A2 `prompts/A2_match.txt`

输入：相同原始 Policy/Question/Scenario + 真正的 A1 模型输出。输出：每个模型条件的 TRUE/FALSE/UNKNOWN/CONFLICTING 与引用到的客户原句。参照 [Chain of Condition `build_verify_prompt`](https://github.com/Infinite-set/Chain-of-Condition/blob/main/prompts_conditionalqa.py)，它区分 satisfied/contradicted/not mentioned/inapplicable；我们改成 4 类状态并明确“沉默不是 FALSE”“不要跨人/跨交易迁移事实”。不为模型修正 A1 条件。

## A3 `prompts/A3_llm_decide.txt`

输入：**相同原始 Policy + Question + Scenario + A1 真正输出 + A2 真正输出**。

输出：LLM 自己给出 YES/NO/NEED_MORE_INFORMATION，引用政策、指出 IR 与原文不一致处。依据 [Code Prompting `code_prompt.py`](https://github.com/UKPLab/emnlp2024-code-prompting/blob/main/src/conditionalqa/code_prompt.py) 中**让语言模型根据中间代码/结构推理、不执行代码**的做法，但我们保留原文，允许不确定答案，并且不把原论文强制 YES/NO 的限制复制过来。A3 **不准使用 Python 最终执行器**。这也是与前一版 v1 最重大的修复。

## B1 `prompts/B1_direct.txt`

输入：完全相同的原始 Policy/Question/Scenario。输出：最终标签、理由、缺失信息、条件性说明。与 A3 用相同的 YES/NO/NEED_MORE_INFORMATION 判定标准。

参考 [Code Prompting `text_prompt.py`](https://github.com/UKPLab/emnlp2024-code-prompting/blob/main/src/conditionalqa/text_prompt.py) 的 Direct 文本答题 baseline，但取消其强制 YES/NO 的设计。新版 B1 删除了专门提示“确认排除项时可直接判 NO”的句子，因为它过度贴合 R2；保留无偏向的通用语义：只要现有证据足以确定结果就回答，否则指出不足。

## Prompt 中刻意没有的内容

- `Gift Aid`、`dev-38`、C1–C3/X1–X5 编号、R0–R4、已知参考结果、税额计算预先答案。
- 引导某一条 Pipeline 获胜的示例。
- 限定 Direct 只能 YES/NO 的指令。
- A3 依靠 Python 计算最终决定的指令。

## 版权和复用

本包是**根据公开论文思想原创改写的 Prompt/调度代码**，不是声称原版复现，也没有将整份第三方实现复制进来。UKPLab 仓库是 Apache-2.0 许可；其他项目是否允许直接复制具体源码须另查许可证。我们只提供可追溯源链接与学术引用。
