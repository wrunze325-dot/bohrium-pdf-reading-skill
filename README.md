# pdf-reading-order — 科研 PDF 阅读顺序与结构解析 Skill

一个可被 Agent 直接调用的 PDF 解析 Skill：把双栏 / 多栏 / 跨栏 / 跨页的科研 PDF 变成**顺序正确、结构清楚、
每条都能回到原页核对**的结构化结果，并在读不出来的时候如实说明原因，而不是编一段"看起来像全文"的文字。

选题方向：**多栏阅读顺序恢复 + 每段内容的原页定位**（题目给出的五个方向之一，做深一种）。

## 目录

```
SKILL.md                  Skill 本体：触发条件、调用方式、输出契约、边界与限制
scripts/pdf_outline.py    解析入口：字符 -> 行 -> 栏 -> 分带 -> 阅读顺序 -> 结构块
scripts/po_tables.py      表格：pdfplumber 结构化，几何切分兜底，跨页续表合并
scripts/po_formulas.py    公式：编号、上下标、希腊字母与符号保真
scripts/po_ocr.py         扫描页 OCR 后端探测与调用（多后端，失败即报告）
scripts/build_result.py   骨架 / 合并答案 / 校验：把"解析完成"和"案例完成"分开
scripts/po_contract.py    读取 output_contract，区分解析字段与判断字段（不按字段名猜语义）
scripts/po_evidence.py    证据解析与校验：逐字引用、页码范围、数字溯源、表格行列
tests/make_fixtures.py    生成合成样本（双栏 / 单栏对照 / 扫描页）与真值 truth.json
tests/selftest.py         解析层对照：基线 vs Skill，输出 tests/selftest_report.md
tests/test_cases.py       交付层三例对照 + 反例，输出 tests/runs/cases/report.json
tests/reproduce_bug.py    复现 v4 的"status=ok 但答案为空"缺陷
tests/cases/              三个本地案例（第 1 页作答 / 表格单元格 / 无表格且未报告）
tests/v4_vs_v5_report.md  三例的 v4/v5 对照表
tests/fixtures/           真值与说明；PDF 由 make_fixtures.py 现场生成，不随包分发
examples/                 最小输入、调用与示意输出（明确标注为示例）
```

## 快速开始

```bash
pip install pymupdf pdfplumber          # tesseract 可选，仅扫描页需要

# 1. 解析（只负责"读文档"）
python3 scripts/pdf_outline.py --pdf paper.pdf --out work/

# 2. 骨架：解析产物自动填，判断字段留空，case_status=incomplete
python3 scripts/build_result.py --case case.json --blocks work/blocks.json \
        --markdown work/document.md --out out/result.json

# 3. 作答（agent 读原文，答案必须带 page + 原文引用），合并并校验
python3 scripts/build_result.py --case case.json --result out/result.json \
        --apply-answers answers.json

# 4. 任何手改后重新校验（未通过时退出码 2）
python3 scripts/build_result.py --case case.json --result out/result.json --validate
```

产物：`blocks.json`（结构化、带页码与 bbox）、`document.md`（人读版，逐块带定位注释）、
`summary.json`、`run.log`，以及 `result.json`（`parse.status` 与 `case_status` 分开）。

### 两层状态

| 状态 | 回答的问题 | 取值 |
|---|---|---|
| `parse.status` | 文档解析成功了吗 | `ok` / `partial` / `failed` |
| `case_status`（= `status`） | 案例的合同字段都答完了吗 | `complete` / `incomplete` / `blocked` / `failed` |

`case_status=complete` 需要每个必需字段都通过证据校验；只把 blocks 或坐标填进 `evidence`
不算答完。答不出来时写 `missing_information`（`no_such_object` / `not_reported` / `parse_failure`），
状态为 `blocked`，不会伪造成 `ok`。

三条合同规则（都有回归测试）：required 的 `tables` 在文档确认无表时接受 `[]`
（仅当 `parse.status=ok`，否则 `structural_empty_unconfirmed` 待确认）；
`properties` + `required` 同时出现时合并，保留类型与必填性；
读不懂的字段声明记入 `contract.warnings` 并阻塞 `complete`，不会被悄悄丢掉。

## 方法（为什么顺序会变对）

1. **字符 -> 行**：按基线聚类，按行内空隙切分；上下标字形（`x^2`、`H_2O`）先挂回它所属的那一行，
   再恢复 `^{}` / `_{}` 结构 —— 否则一个公式会被拆成三行。
2. **行 -> 栏**：扫描候选切分位置，选"被最少行跨越、两侧都有稳定左边界与足够行数"的那条作为栏间通道。
   用"跨越行数"而不是"空白直方图"，是为了不被通栏标题和摘要骗过去。
3. **栏 -> 分带**：跨越栏间通道的元素（通栏标题、通栏图、宽表）是分带边界。
4. **带内排序**：每一带从左到右、每栏从上到下读。这正是"左栏读完再右栏"的真实阅读顺序，
   而不是把同一水平线上的左右两栏拼成一句。
5. **结构分类**：标题 / 作者 / 摘要 / 章节 / 题注 / 正文 / 表格 / 公式 / 图内文字 / 脚注 / 页眉页脚 / 参考文献，
   逐行分类后再按列合并成段（短行结尾断开、连字符断词还原）。
6. **表格**：行列与单元格、表头行、每行来自哪一页；跨页续表按表号合并回首页表头。
7. **证据**：每个块都有 `page`（1 起）与 `bbox`（PDF 点、原点左上），`locator` 形如 `p.1 col=L bbox=[56.0,158.6,255.0,168.1]`。
   答案层再进一步：引用的原文必须在该页逐字出现（脚本回查 blocks/表格），答案里的数字必须能在引用里找到，
   表格答案必须给出 table/row/column 并与表头、行、列、值四者一致。

## 自测（同一 PDF、同一问题、同一机器）

样本：`tests/fixtures/two_column.pdf`（2 页，双栏正文按视觉行序绘制，含通栏标题、图题注、
跨页续表、脚注、页眉页脚、带上下标与编号 `(2)` 的公式）。真值：`tests/fixtures/truth.json`。

| 指标 | pypdf 纯文本 | pymupdf 纯文本 | 本 Skill |
|---|---|---|---|
| 阅读顺序逆序对 | 52 | 52 | **0** |
| 跨栏交替次数 | 17 | 17 | **1** |
| 拼接风险（相邻串栏且下句小写） | 3 | 3 | **0** |
| 元素能指回页码 | 0/38 | 0/38 | **38/38** |
| 元素 bbox 覆盖原文 | 0/38 | 0/38 | **38/38** |
| 表格结构 | 无 | 无 | 表头 + 3 行 + 行所属页 `[1,2,2]` |
| 公式编号 / 上下标 | 无结构 | 无结构 | `(2)` + `x^{2}` + `y_{−1}` + α β |

对照组能暴露的具体错误：纯文本按内容流给出 `L1a, R1a, L1b, R1b …` 的左栏/右栏交替，
把"Two-column papers set text in narrow columns," 与 "Gutter detection alone is not enough: a title,"
接成一句；跨页续表丢失首页表头，`0.98` 被归到无关的列上。修复后本 Skill 的逆序对与拼接均为 0。

另外两个对照：

- **单栏页**（`single_column.pdf`）：检出 `columns=1`，没有在句子中间劈出假栏，6 行文本按序齐全。
- **扫描页**（`scanned.pdf`，无文字层）：纯文本抽取返回 0 字符且**不报告任何异常**；
  本 Skill 返回 `status=failed` 并列出 `no_text_layer` / `ocr_unavailable` / `unreadable_page`，
  不产出猜测文字。装上 OCR 后端后同一路径会改走 OCR 并在块上标注置信度。

完整报告见 `tests/selftest_report.md` 与 `tests/selftest_report.json`。复现：

```bash
python3 tests/make_fixtures.py && python3 tests/selftest.py
```

## 交付层自测：三个案例，v4 与 v5 同一批输入

`tests/test_cases.py` 用同一批输入分别跑 v4（旧版）和 v5，产出一张对照表
（`tests/v4_vs_v5_report.md`）；26/26 项检查通过。三道题与结果：

| # | 题目 | 预期 | v4 输出 | v5 输出 | v5 证据 |
|---|---|---|---|---|---|
| 1 | 表 1 中 `Two-column (stream order)` 行报告了多少条拼接行？ | `5`，且引自第 1 页（文档共 2 页） | `status=ok`，`answer=null`，`unfilled=[answer, evidence]` | `status=complete`，`answer=5` | p.1 · row=`Two-column (stream order)` · col=`Spliced` |
| 2 | 表 1 中 `Two-column + skill` 行的准确率是多少？ | `0.98`，行/列/值一致，引自第 2 页（续表） | `status=ok`，`answer=null` | `status=complete`，`answer=0.98` | p.2 · row=`Two-column + skill` · col=`Order acc.` |
| 3 | 表 2 中 method B 报告的准确率是多少？（该文档没有表格，也没报告该量） | 不给数字，说明原因 | `status=ok`，`answer=null`，`unfilled=[answer, missing]` | `status=blocked`，`answer=null` | `missing_information=[no_such_object, not_reported]` |

反例（同一批输入，v5 必须拒绝）：把引用页码写成 2 而不是 1、答案写一个引用里没有的数字、
表格答案不写行列 —— 三种都被判不通过（`page_out_of_range` / `quote_not_found` /
`number_not_in_evidence` / `table_cell_row_column`），案例不会变成 `complete`；
改回正确作答后重新校验，状态回到 `complete`。

### 复现出来的缺陷（已修）

`tests/reproduce_bug.py` 在 v4 上稳定复现：**必需字段 `answer` 为空、`unfilled=["answer"]`，
`result.json` 却写着 `status="ok"`**。根因是旧版把"解析成功"直接当成"案例成功"，并且按字段名
正则猜语义。v5 把两者拆开（`parse.status` / `case_status`），骨架阶段就写 `incomplete`，
只有全部必需字段通过证据校验才 `complete`；答不出来则 `blocked` 并写 `missing_information`。

## 真实论文冒烟测试（本地，不随包分发）

除合成样例外，另在两篇公开 arXiv 论文上跑过解析（仅本地验证，PDF 未打包分发，也不含任何论文摘录作为答案）：

| 论文版式 | 结果 |
|---|---|
| 单栏（arXiv 1706.03762，15 页） | `columns=1` 全部页面，1 个标题、13 个小节、19 个公式块；无异常，1.0 s（前 6 页） |
| 单栏（arXiv 2010.11929 ViT，22 页）**开发后才选，未针对它调规则** | 走完 v5 全流程：问"best ViT 在 ImageNet 上的准确率"，答 `88.55%`，证据 p.2，逐字引用核对通过；渲染该页人工确认原文写作 "the best model reaches the accuracy of 88.55% on ImageNet"。同一篇的 Table 1 结构被切碎（表头旋转、单元格内换行），表格问题被校验拒绝，未编造数值 |
| 双栏（arXiv 1512.03385，12 页） | 第 1–4、6 页检出双栏、第 5 页为通栏大表（`columns_by_page=[2,2,2,2,1,2]`）；第 2 页逐块核对为"左栏读完再右栏"；7 个表格、13 个题注、7 个公式块；2.1 s（前 6 页） |

这次冒烟测试暴露并修掉了三个真实缺陷（记在此处以示可信）：标题页的递归栏切分把右栏再切成两栏、
arXiv 侧边竖排印章被当成 7 个"标题"、以 method 开头的正文行被误判为小节标题。

## 适用范围与限制

- 适用：文字层 PDF 的科研论文（单栏、双栏、多栏），需要阅读顺序、章节/题注层级、表格结构、
  公式保真与逐块定位的场景。
- 退化的情形：正文绕图、三栏中插通栏图、竖排、极端不规则版式；此时看 `layout.columns` 与
  `document.md` 即可发现，需要人工兜底。
- 表格依赖可检测线框或稳定列距；无线框表格只能给低置信度结构。表头旋转、单元格内换行、
  跨页且无表号的表会被切碎（本地在 arXiv 2010.11929 的 Table 1 上实测到），此时答案校验会拒绝
  `table_cell` 作答，案例保持 `incomplete`，不会编数。
- 公式只做结构保真，不做数学语义或 LaTeX 转换。
- 图表只定位（caption + 图框 bbox），不识别图形内容。
- 无文字层且无 OCR 后端时，该页内容不会出现在结果里——这是有意的选择，不是遗漏。

## 依赖与来源说明

- `pymupdf`（AGPL-3.0，用户侧安装）、`pdfplumber`（MIT）、`pypdf`（BSD）：均通过 pip 安装，不随本仓库分发。
- `tesseract` 及各 OCR 封装为可选项，按可用性探测。
- 示例 PDF 由 `tests/make_fixtures.py` 用 reportlab 现场生成，**文中文字为本项目自写**，
  不含任何第三方论文内容。提交包内**不放任何二进制文件**：本题约定 Skill 资源为 UTF-8 文本，
  所以包内只有可再生成样本的脚本与真值，跑 `python3 tests/make_fixtures.py` 即可得到样例。
  reportlab 只在生成/自测时需要，正式解析不依赖它。
- 本仓库不含任何平台真实案例的数据、答案或评分。

## 状态与维护

- 作者：本题参赛作品；版本见仓库提交记录。
- 修改解析逻辑后的固定动作：`make_fixtures.py` → `selftest.py`，先看对照表有没有退化，再看真实案例。
- 已知待改进：无线框表格的列对齐、跨三页以上续表的合并、竖排/日文版式。
