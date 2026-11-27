---
name: pdf-reading-order
description: "把科研 PDF 解析成可复核的结构化内容并回答案例问题：恢复双栏/多栏/跨栏/跨页的阅读顺序，逐块保留页码与 bbox，区分正文、章节标题、图表题注、脚注、页眉页脚，保留表格表头与单元格归属、公式编号与上下标，并在交付前逐项校验答案与证据。用于文献文档解析、论文全文提取、阅读顺序修正、表格结构还原、公式保真、扫描页 OCR 兜底。Trigger on: '解析这份 PDF', 'reading order', 'extract the paper', 'parse this paper', 'PDF 转结构化', '表格解析', '公式提取', '扫描件识别', citing page numbers for quotes."
---

# PDF 阅读顺序与结构解析（pdf-reading-order）

把一份真实科研 PDF 变成"可以继续处理、并且能回到原文核对"的结构化结果，**并且把案例问题真正答完**。
两个目标分开管理：解析完成 ≠ 案例完成。

## 1. 这个 Skill 解决什么

| 问题 | 普通文本抽取 | 本 Skill |
|---|---|---|
| 双栏/多栏页面 | 按内容流输出，左右栏交替、相邻段落拼接 | 先检测栏间空白通道，再按栏分段读取 |
| 跨栏标题 / 通栏图表 | 当作普通段落，把左右两栏拼成一句 | 识别跨栏元素，作为分带边界 |
| 表格 | 单元格文字散落成行 | 行列结构 + 表头，跨页续表合并回头部 |
| 公式 | 符号丢失、编号与上下标散落 | 公式文本 + 编号 + 上下标 + 希腊字母 |
| 脚注 / 页眉页脚 | 混进正文 | 单独分类，不污染阅读顺序 |
| 扫描页 | 输出空文本，看起来"没有内容" | 明确报告"无文字层"，尝试 OCR，失败则如实标注 |
| 定位 | 无 | 每个块都有页码 + bbox + locator |
| 案例问答 | 把全文丢给模型，答案无出处 | 答案必须携带 page + 原文引用，脚本逐项校验 |

## 2. 依赖与调用

**运行环境**：Python 3.9+。依赖按可用性自动降级：

| 依赖 | 用途 | 缺失时 |
|---|---|---|
| `pymupdf`（首选） | 字符级坐标、字体、绘图区 | 降级到 pdfplumber，再降级到 pypdf（几何变粗，会在结果里标注） |
| `pdfplumber` | 表格行列与单元格 | 用几何切分做低置信度表格（confidence 0.5） |
| `tesseract` / `pytesseract` / `rapidocr` / `easyocr` / `paddleocr` | 扫描页 OCR | 标记"无文字层 + 无 OCR 后端"，不猜内容 |

**四步调用**（评测时 Skill 安装在 `/skills/weekly-skill`）：

```bash
SKILL_DIR=/skills/weekly-skill; WORK=/app/work/parse; CASE=/input/case.json

# 1) 解析 PDF -> blocks.json / document.md / summary.json / run.log
python3 "$SKILL_DIR/scripts/pdf_outline.py" --pdf /input/paper.pdf --out "$WORK"

# 2) 生成结果骨架：解析产物自动填，需要判断的字段留空、状态为 incomplete
python3 "$SKILL_DIR/scripts/build_result.py" --case "$CASE" \
    --blocks "$WORK/blocks.json" --markdown "$WORK/document.md" \
    --out /app/output/result.json

# 3) 读原文，把答案写成 answers.json（每条都带 page + 原文引用），合并并校验
python3 "$SKILL_DIR/scripts/build_result.py" --case "$CASE" \
    --result /app/output/result.json --apply-answers answers.json

# 4) 任何手改之后重新校验（不通过时退出码 2）
python3 "$SKILL_DIR/scripts/build_result.py" --case "$CASE" \
    --result /app/output/result.json --validate
```

需要看文档事实（页数、表格数、可检索词）时：`--blocks ... --facts`。

## 3. 输出结构

`blocks.json`（`schema: pdfoutline/blocks/v1`）：`source`、`layout`、`blocks[]`（`type/page/bbox/
column/locator/text/rich_text/scripts`，表格带 `header/rows/row_pages/header_page`，公式带
`formula_number/greek/symbols`）、`tables/formulas/figures`、`uncertainties`、`status`。

`result.json`（`schema: pdfoutline/result/v2`）：

| 字段 | 含义 |
|---|---|
| `parse.status` | **文档能否解析**：`ok` / `partial` / `failed` |
| `case_status`（同时写 `status`） | **案例是否答完**：`complete` / `incomplete` / `blocked` / `failed` |
| `answers` | 每个合同字段一项。结构字段来自解析；判断字段是 agent 的答案，含 `value/unit/type/evidence/locator` |
| `missing_information` | 答不出来时的说明，`kind` ∈ `no_such_object` / `not_reported` / `parse_failure`，并记录检查过程 |
| `contract` | 从 case 读到的字段清单：`role`（structural/judgement）、是否必需、当前是否已填；`warnings` 记录读不懂的声明，`coverage_complete` 表示合同是否被完整理解 |
| `answer_issues` / `validation` | 校验发现的问题与结论 |
| `change_log` | 解析、骨架、作答、校验各步的记录 |

`case_status=complete` 的条件：所有**必需**字段都有值，且每个判断字段都通过证据校验。
只把 blocks 或坐标塞进 `evidence` 字段**不算答完**——`evidence` 由已校验的答案派生。

## 4. 证据规则（脚本强制）

1. 每条答案必须带 `evidence: [{page, quote}]`；`quote` 必须在该页**逐字出现**（脚本会去 blocks/表格里找，
   找到后自动补 `block_id` 与 `bbox`）。
2. `page` 必须在 `1..总页数`；引用页码不等于文档页数，写错会被判 `page_out_of_range` 或 `quote_not_found`。
3. 答案里出现的每个数字，都必须出现在某条证据引用里，否则 `number_not_in_evidence`。
4. 表格单元格答案要写 `locator.table / row / column`，脚本会核对表头、行、列与值是否一致
   （`table_cell_row_column`、`table_cell_mismatch`、`table_cell_page_mismatch`）。
5. 答不出来时不能留空：写 `missing_information`，`no_such_object` 要记录 `checked.tables`，
   `not_reported` 要记录检索词及命中数（必须为 0），`parse_failure` 只在解析确实失败时用。
6. 合同字段按语义处理：`text/blocks/tables/...` 是解析产物；`answer/evidence/page/citation/...`
   一律视为需要作答的字段，**不会因为字段名里有 page/table 就把页数或表数填进去**。
7. 空集合是答案，不是缺失：structural 字段（如 `tables`）在 `parse.status=ok` 时返回 `[]`
   表示"文档确认没有这类对象"，算已填；若解析是 `partial`，同样的 `[]` 会判为待确认
   （`structural_empty_unconfirmed`），因为"没有表"不能建立在我们没读清的文档上。
8. 合同形状：`properties` + `required` 同时出现时合并处理——`properties` 给字段、类型与描述，
   `required` 给必填性，未被点名的字段是可选的。**任何读不懂的字段声明都会记进
   `contract.warnings` 并触发 `contract_unrecognized_field`，案例永远不能变成 `complete`**，
   避免"漏掉一个字段 → 少问一个问题 → 却被判完成"。

## 5. 边界与异常

- **没有文字层**：`uncertainties` 出现 `no_text_layer`；OCR 可用时加 `ocr_used`，不可用加 `ocr_unavailable`，
  该页不产出猜写文字。
- **乱码字体**（缺 ToUnicode）：保留原样并报 `garbled_text`。
- **表格无结构**：低置信度表格 `confidence: 0.5`；行列不全报 `table_structure_uncertain`。
  结构不可用时，表格类问题会被校验拒绝（`table_cell_mismatch`），案例保持 `incomplete`。
- **编号冲突**（两个 `Table 1`）：报 `caption_conflict`。
- **加密 PDF**：非零退出，`run.log` 写明原因；应报告"无法读取"。
- **超长文档**：`--max-pages N` 先读前 N 页出可用结果，再全量跑。
- **性能**：3 页双栏约 0.3 s；100 页一般 < 20 s（不含 OCR）。

## 6. 已知限制（如实说明）

- 阅读顺序基于"栏间空白通道 + 跨栏分带"，正文绕图、三栏中夹通栏图、竖排等极端版式会退化。
- 表格依赖可检测线框或稳定列距；**表头旋转、单元格内换行、跨页且无表号的表**可能被切碎。
  本地实测 arXiv 2010.11929（ViT）的 Table 1 就属于这一类：结构不可用，系统拒绝作答而不是编数。
- 公式只做结构保真，不做数学语义或 LaTeX 转换。
- 图表只定位（caption + 图框 bbox），不识别图形内容。
- 无文字层且无 OCR 后端时，该页内容不进结果。

## 7. 最小示例（示例数据，不是正式案例结果）

`examples/case.example.json` 问的是表 1 里某行的准确率；`examples/answers.example.json` 是 agent 的作答。

```bash
python3 tests/make_fixtures.py                      # 生成示例 PDF（需要 reportlab）
python3 scripts/pdf_outline.py --pdf tests/fixtures/two_column.pdf --out /tmp/ex
python3 scripts/build_result.py --case examples/case.example.json \
    --blocks /tmp/ex/blocks.json --markdown /tmp/ex/document.md --out /tmp/ex/result.json
python3 scripts/build_result.py --case examples/case.example.json \
    --result /tmp/ex/result.json --apply-answers examples/answers.example.json --validate
```

- `examples/example_result.incomplete.json`：只解析完、还没作答，`status=incomplete`
- `examples/example_result.json`：作答并校验后，`status=complete`，答案 `0.98`，证据 `page 2`，
  行列 `Two-column + skill` / `Order acc.`

## 8. 自查

```bash
python3 tests/make_fixtures.py      # 重建合成样本（含真值 truth.json）
python3 tests/selftest.py           # 解析层：基线 vs Skill（阅读顺序、表格、公式、证据）
python3 tests/test_cases.py         # 交付层：三个案例的 v4/v5 对照 + 反例（错页/编数/缺行列）
python3 tests/reproduce_bug.py      # 复现 v4 的"status=ok 但答案为空"缺陷
```

`tests/v4_vs_v5_report.md` 是三例对照表（题目 / 预期 / v4 输出 / v5 输出 / 证据位置）。
改解析逻辑先跑 `selftest.py`，改交付逻辑先跑 `test_cases.py`。
