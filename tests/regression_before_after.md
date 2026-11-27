# 两个回归测试：修复前后的实际输出

环境：本地，`pkg-v5`。before = 打补丁前的真实运行输出（同一会话内、同一命令、同一输入）；
after = 打补丁后重跑同一命令。三个案例文件在 `tests/cases/` 下，可随时复跑：
`python tests/test_cases.py`（37/37 项检查通过）。

---

## 例 1：合同必填 `tables`，文档确认无表

输入：`cases/case4_tables_required_empty.json` + `tests/fixtures/single_column.pdf`（1 页，确实没有表格）
合同：`tables`（structural, required）、`text`（optional）

### 修复前

```json
{"case_status": "incomplete",
 "answers": {"tables": null, "text": "<markdown>"},
 "contract_fields": [
   {"field": "tables", "required": true, "role": "structural", "type": "structured"},
   {"field": "text",   "required": false, "role": "structural", "type": "text"}],
 "pending": ["tables"],
 "issues": ["structural_missing"]}
```

问题：解析器确实"没找到表"，但空列表被当成"没有产出"，于是必填项判为缺失，案例永远无法完成。

### 修复后

```json
{"case_status": "complete",
 "answers": {"tables": [], "text": "<markdown>"},
 "contract_fields": [
   {"field": "tables", "required": true, "role": "structural", "type": "structured",
    "filled": true,
    "note": "empty list accepted: the document parsed cleanly and contains no such object"},
   {"field": "text", "required": false, "role": "structural", "type": "text", "filled": true}],
 "pending": [],
 "issues": []}
```

规则：**空列表是一个真实答案，但只在文档被干净读完时成立**（`parse.status == ok`）。
若解析是 `partial`（有不可读页或表格结构不确定），同一份输入会得到
`structural_empty_unconfirmed` 并保持待确认——"没有表"不能建立在我们没读清的文档上。

---

## 例 2：合同同时给出 `properties` 与 `required`

输入：`cases/case5_properties_required.json` + `tests/fixtures/two_column.pdf`
合同：

```json
{"type": "object",
 "properties": {"answer":     {"type": "text",   "description": "..."},
                "confidence": {"type": "number", "description": "..."},
                "text":       {"type": "text",   "description": "..."}},
 "required": ["answer"]}
```

### 修复前

```json
{"case_status": "incomplete",
 "contract_fields": [{"field": "answer", "required": true, "role": "judgement", "type": "text"}],
 "pending": ["answer"]}
```

问题：只读到 `required` 里的名字，`properties` 被忽略。`confidence`、`text` 两个字段直接消失，
类型也丢了（`confidence` 的 `number` 没了）。**丢字段是危险的**：如果某个被丢的字段里有必答内容，
案例可能在没答的情况下被判完成。

### 修复后

```json
{"case_status": "incomplete",
 "contract_fields": [
   {"field": "answer",     "required": true,  "role": "judgement", "type": "text"},
   {"field": "confidence", "required": false, "role": "judgement", "type": "number"},
   {"field": "text",       "required": false, "role": "structural", "type": "text"}],
 "pending": ["answer"]}
```

规则：`properties` 提供字段、类型、描述；`required` 提供必填性；
**没被 `required` 点名的字段是可选的**。补上答案后该案例变为 `complete`（已在测试中断言）。

---

## 例 2b：无法识别的字段声明

输入：`cases/case6_unrecognized_field.json`

```json
{"fields": ["answer",
            {"description": "the judge wants this, but it declares no name"},
            {"name": 42}]}
```

### 修复前

进程直接崩溃：

```
AttributeError: 'int' object has no attribute 'strip'
```

### 修复后

```json
{"case_status": "incomplete",
 "contract": {"warnings": [
   {"kind": "unrecognised_declaration", "field": null,
    "detail": "field declaration without a usable name: {\"description\": \"the judge wants this, ...\"}"},
   {"kind": "unrecognised_declaration", "field": null,
    "detail": "field declaration without a usable name: {\"name\": 42}"}],
   "coverage_complete": false},
 "issues": ["contract_unrecognized_field", "contract_unrecognized_field"]}
```

规则：读不懂的声明必须显式报告并**阻塞"完成"**——即使其他字段都答对，
案例也保持 `incomplete`（测试里专门验证了"给出有效答案仍然不能完成"）。
这样可以避免"漏掉一个字段 → 少问一个问题 → 却被判为完成"。
