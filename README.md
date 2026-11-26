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
scripts/build_result.py   按 case 的 output_contract 生成 result.json
tests/make_fixtures.py    生成合成样本（双栏 / 单栏对照 / 扫描页）与真值 truth.json
tests/selftest.py         基线 vs Skill 的公平对照，输出 tests/selftest_report.md
tests/fixtures/           真值与说明；PDF 由 make_fixtures.py 现场生成，不随包分发
examples/                 最小输入、调用与示意输出（明确标注为示例）
```

## 快速开始

```bash
pip install pymupdf pdfplumber          # tesseract 可选，仅扫描页需要
python3 scripts/pdf_outline.py --pdf paper.pdf --out work/
python3 scripts/build_result.py --case case.json --blocks work/blocks.json \
        --markdown work/document.md --out out/result.json
```

产物：`blocks.json`（结构化、带页码与 bbox）、`document.md`（人读版，逐块带定位注释）、
`summary.json`、`run.log`。

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

## 真实论文冒烟测试（本地，不随包分发）

除合成样例外，另在两篇公开 arXiv 论文上跑过解析（仅本地验证，PDF 未打包分发，也不含任何论文摘录作为答案）：

| 论文版式 | 结果 |
|---|---|
| 单栏（arXiv 1706.03762，15 页） | `columns=1` 全部页面，1 个标题、13 个小节、19 个公式块；无异常，1.0 s（前 6 页） |
| 双栏（arXiv 1512.03385，12 页） | 第 1–4、6 页检出双栏、第 5 页为通栏大表（`columns_by_page=[2,2,2,2,1,2]`）；第 2 页逐块核对为"左栏读完再右栏"；7 个表格、13 个题注、7 个公式块；2.1 s（前 6 页） |

这次冒烟测试暴露并修掉了三个真实缺陷（记在此处以示可信）：标题页的递归栏切分把右栏再切成两栏、
arXiv 侧边竖排印章被当成 7 个"标题"、以 method 开头的正文行被误判为小节标题。

## 适用范围与限制

- 适用：文字层 PDF 的科研论文（单栏、双栏、多栏），需要阅读顺序、章节/题注层级、表格结构、
  公式保真与逐块定位的场景。
- 退化的情形：正文绕图、三栏中插通栏图、竖排、极端不规则版式；此时看 `layout.columns` 与
  `document.md` 即可发现，需要人工兜底。
- 表格依赖可检测线框或稳定列距；无线框表格只能给低置信度结构。
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
