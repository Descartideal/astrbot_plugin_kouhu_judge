# CF 离线题库归档来源

优先使用 Codeforces 官方 problemset.problems API 的题目编号、标题和当前 rating，以及可访问的官方题面/题解正文。

当 CF 正文站返回 403 或不可访问时，后备读取 Hugging Face **Open R1** 发布的归档：

- 数据集：https://huggingface.co/datasets/open-r1/codeforces-cots
- 仅取 `solutions_w_editorials` 中的**原始题面和 editorial 字段**。
- 固定版本：`39ac85c150806230473c70ad72c31f6232fe3f41`；新页下载前核对发布版本，发生变化则保留离线库并停止补充，等待维护者审核。
- 原始题目来源及原作者题解字段说明：https://huggingface.co/datasets/open-r1/codeforces
- 数据发布方：Open R1 / Hugging Face；数据集卡标记 CC-BY-4.0。题目与原题解仍属于各自原作者。

`generation`、`prompt`、`messages`、模型生成的解答/检查器都**不进入题库缓存或判题输入**。模型生成的解答不能替代官方题解。

每道归档题必须有非空且未截断的原作者 editorial、完整输入输出及样例，题目编号/标题与官方元数据一致，难度来自官方 API 快照。每条记录保存数据集版本、行号、内容 SHA256、原题链接及归档 URL。

**归档不冒充现场官方网页核验**：CF 正文站不可用时，无法重新逐字核验其官方题解网页；此时 `editorial_url` 指向实际归档行，`official_editorial_direct_url_verified=false`，验证依据为发布方的原作者题解字段声明与官方元数据交叉核对。直接从官方正文取得的题解仍保留官方 URL 与目录关联证据。

缺少题解、内容被截断、标题不一致、交互题或依赖未支持图片的题跳过。抽题始终查询本地 SQLite 的合格题目，并严格按缓存的 CF rating 筛选。
