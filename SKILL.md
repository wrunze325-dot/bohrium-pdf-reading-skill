---
name: pdf-reading-order
description: "把科研 PDF 解析成可复核的结构化内容：恢复双栏/多栏/跨栏/跨页的阅读顺序，逐块保留页码与 bbox，区分正文、章节标题、图表题注、脚注、页眉页脚，保留表格表头与单元格归属、公式编号与上下标。用于文献文档解析、论文全文提取、阅读顺序修正、表格结构还原、公式保真、扫描页 OCR 兜底。Trigger on: '解析这份 PDF', 'reading order', 'extract the paper', 'parse this paper', 'PDF 转结构化', '表格解析', '公式提取', '扫描件识别', citing page numbers for quotes."
---

# PDF 阅读顺序与结构解析（pdf-reading-order）

把一份真实科研 PDF 变成"可以继续处理、并且能回到原文核对"的结构化结果。核心不是把文字倒出来，
而是**顺序对、结构清、能核回**。

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

## 2. 依赖与调用

**运行环境**：Python 3.9+。依赖按可用性自动降级：

| 依赖 | 用途 | 缺失时 |
|---|---|---|
| `pymupdf`（首选） | 字符级坐标、字体、绘图区 | 降级到 pdfplumber，再降级到 pypdf（几何信息变粗，会在结果里标注） |
| `pdfplumber` | 表格行列与单元格 | 用几何切分做低置信度表格（confidence 0.5） |
| `tesseract` / `pytesseract` / `rapidocr` / `easyocr` / `paddleocr` | 扫描页 OCR | 标记"无文字层 + 无 OCR 后端"，不猜内容 |

安装首选：`pip install pymupdf pdfplumber`，OCR 需要时 `pip install pytesseract` 并安装 tesseract 可执行文件。

**调用（两条命令）**：

```bash
# 评测时本 Skill 安装到 /skills/weekly-skill；SKILL_DIR 就是含 SKILL.md 的目录
SKILL_DIR=/skills/weekly-skill; PDF=/input/paper.pdf; WORK=/app/work/parse

# 1) 解析 PDF -> blocks.json / document.md / summary.json / run.log
python3 "$SKILL_DIR/scripts/pdf_outline.py" --pdf "$PDF" --out "$WORK"

# 2) 按 case 的 output_contract 生成 result.json（评测要求写入 /app/output）
python3 "$SKILL_DIR/scripts/build_result.py" \
  --case /input/case.json --blocks "$WORK/blocks.json" \
  --markdown "$WORK/document.md" --out /app/output/result.json
```

工作目录放在 `/app/work`，结果放 `/app/output`。先写出一份可用结果，再逐项改进——不要等到全部做完才落盘。

## 3. 输出结构

`blocks.json`（`schema: pdfoutline/blocks/v1`）关键字段：

- `source`：pdf 路径、sha256、页数、后端、元数据
- `layout`：栏数、检测到的栏间通道、正文字号
- `blocks[]`：按阅读顺序排列，每块含
  - `id`, `order`, `type`（`title|authors|abstract|heading|caption|body|table|formula|figure_label|footnote|header|footer|reference`）
  - `page`（1 起）、`bbox`（PDF 点，原点左上）、`column`（`L/R/full`）、`locator`
  - `text` 逐字文本；`rich_text` 带上标/下标标记（`x^{2}`、`H_{2}O`）；`scripts` 上下标区间
  - 标题：`heading_level`、`number`；题注：`caption_label`、`caption_number`
  - 表格：`header`、`rows`、`row_pages`、`header_page`、`header_bbox`、`row_cells`、`spans_pages`
  - 公式：`formula_number`、`greek`、`symbols`
- `tables` / `formulas` / `figures`：结构化的子集合
- `uncertainties[]`：`no_text_layer|ocr_used|ocr_failed|ocr_unavailable|unreadable_page|garbled_text|table_structure_uncertain|caption_conflict`
- `status`：`ok` / `partial` / `failed`

`document.md` 是同一内容的 Markdown 版本，每块后面跟一行 `<!-- block p.N locator -->`，方便人读和引用。

## 4. 怎么把它接进 case

评测为每个 case 提供 `/input/case.json` 与 `task.md`，每个 case 的 `output_contract` 不同。
**不要套用示例字段**：

1. 读 `/input/case.json`，取 `case_id` 和 `output_contract`；
2. 跑上面两条命令，`build_result.py` 会把 contract 里声明的字段逐条列出：
   - 认识的名字（`text/markdown/blocks/sections/tables/formulas/figures/references/pages/language/uncertainties`）
     自动填好，并在 `output_contract.fields[i].auto_filled` 标 `true`；
   - 需要判断的字段（对论文的问答、翻译、结论判断）留在 `answers[field] = null`，
     由你读 `parsed` 之后自己填——**不要用解析结果冒充答案**；
3. 按 `task.md` 的要求补齐 `answers`，保留 `case_id`、`evidence`、`uncertainties`、`change_log`；
4. 结果写入 `/app/output/result.json`。

## 5. 边界与异常

- **没有文字层的页面**：`uncertainties` 会出现 `no_text_layer`，尝试 OCR 时另加 `ocr_used`；
  没有 OCR 后端则加 `ocr_unavailable` 且不产出该页文字。**不猜测、不补写**。
- **扫描页 OCR**：`ocr_used` 记录用的后端，块文本 `confidence` 会低于 1.0，原页渲染图在 `OUT/rendered/`。
- **乱码字体**（缺 ToUnicode）：保留原样并报 `garbled_text`，不要试图"修复"成看起来合理的内容。
- **表格无结构**：低置信度被切出的表格带 `confidence: 0.5`；行列不全时另报 `table_structure_uncertain`。
- **同一编号出现两次**（如两个 `Table 1`）：报 `caption_conflict` 并给出首次出现的位置。
- **加密 PDF**：打开失败会以非零码退出，`run.log` 写明原因；此时应报告"无法读取"而不是空结果。
- **超长文档**：`--max-pages N` 先读前 N 页出可用结果，再全量跑。
- **性能**：3 页双栏约 0.3 s；100 页一般 < 20 s（不含 OCR）。OCR 只在无文字层的页触发。

## 6. 已知限制（如实说明）

- 阅读顺序基于"栏间空白通道 + 跨栏分带"，对**极端版式**（正文绕图、三栏中夹通栏图、竖排）可能退化，
  退化时会体现为 `layout.columns` 与明显不合理的块顺序，应先看 `document.md` 再决定是否手工调整。
- 表格结构依赖可检测的线框或一致的空隙；无线框且列间距不均时只能给低置信度结果。
- 公式只做**结构保真**（文本、编号、上下标、希腊字母），不做数学语义理解，也不转 LaTeX。
- 图表只定位（caption + 图框 bbox），不识别图形内容。
- 未做多语言分词/翻译；中日韩文本按字符即可，无需分词。

## 7. 最小示例（示例数据，不是正式案例结果）

`examples/case.example.json`：

```json
{"case_id": "example-001",
 "task": "解析这份双栏论文，给出阅读顺序、表格与公式",
 "output_contract": {"fields": ["text", "blocks", "tables", "formulas", "figure_captions"]}}
```

调用（示例 PDF 由 `tests/make_fixtures.py` 现场生成；生成脚本需要 `reportlab`，只用于示例与自测，
正式解析不依赖它）：

```bash
python3 tests/make_fixtures.py          # 生成 tests/fixtures/two_column.pdf 等示例样本
python3 scripts/pdf_outline.py --pdf tests/fixtures/two_column.pdf --out /tmp/ex
python3 scripts/build_result.py --case examples/case.example.json \
    --blocks /tmp/ex/blocks.json --out /tmp/ex/result.json
```

得到（节选，`examples/example_result.json` 为完整示例输出）：

```json
{"case_id": "example-001", "status": "ok",
 "parsed": {"tables": [{"header": ["Layout", "Spliced", "Order acc."],
                        "rows": [["Two-column (stream order)", "5", "0.61"],
                                 ["Two-column + skill", "0", "0.98"],
                                 ["Single column (control)", "1", "0.55"]],
                        "row_pages": [1, 2, 2], "spans_pages": [1, 2]}],
            "formulas": [{"text": "S = αx2 + βy−1", "rich_text": "S = αx^{2} + βy_{−1}",
                          "formula_number": "2", "page": 2}]},
 "output_contract": {"fields": [{"field": "text", "auto_filled": true},
                                {"field": "figure_captions", "auto_filled": false}]}}
```

## 8. 自查

`tests/selftest.py` 用同一份 PDF、同一组问题，对比"普通文本抽取"与"本 Skill"：
阅读顺序逆序数、跨栏交替次数、拼接风险、证据定位命中、表格结构、公式保真、耗时。
结果写在 `tests/selftest_report.md`。修改解析逻辑后先跑它，再跑真实案例。

```bash
python3 tests/make_fixtures.py   # 重建合成样本（自带真值 truth.json）
python3 tests/selftest.py        # 基线 vs Skill 对照
```
