# Test fixtures

这个目录在提交包里只保留 **真值（truth.json）与说明**。PDF 是二进制的，本题约定 Skill 资源为
UTF-8 文本，所以不放二进制样本，改为用 `tests/make_fixtures.py` 现场生成：

```bash
pip install reportlab pymupdf      # 生成与自测需要；正式解析不需要 reportlab
python3 tests/make_fixtures.py     # 写出 two_column.pdf / single_column.pdf / scanned.pdf
```

| file | what it exercises |
|---|---|
| `two_column.pdf` | 双栏正文按视觉行序绘制、通栏标题、跨栏小节标题、图题注、跨 1-2 页且不重复表头的表 1、脚注、页眉页脚、带上下标与编号 `(2)` 的公式 |
| `single_column.pdf` | 单栏对照页，用来确认不会在句子中间劈出假栏 |
| `scanned.pdf` | 第二页栅格化、无文字层，用来走 OCR / 如实标注分支 |

`truth.json` 记录期望阅读顺序、表 1 的表头与三行映射、公式的编号与上下标。样本文字由本项目自写，
不含第三方论文内容。
