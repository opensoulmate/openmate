# invoice-flow — 发票报销（真实业务流，装了就干活）

发票进来 → 识别 → 登记 → 人工确认 → 报销台账。整合既有资产：

- 识别链：**二维码**（zbarimg，发票左上角二维码=号码/金额/日期/校验码 100% 准）
  + **ocrmypdf --sidecar / tesseract**（补销售方/税号/商品名称；scan/图片走 OCR）
- 分类：复用 `~/Documents/Hermes/dhcc-reimbursement/invoice_classifier.classify_invoice`
  （差旅/支出 + 报销类别【普】业务费/市内车费…），不可用走同规则兜底
- 台账：`~/Documents/Hermes/dhcc-reimbursement/ledger/报销台账.json`
  （与 dhcc 报销工作区同归口；`INVOICE_LEDGER_FILE` 可覆盖）

## 流程（flows/invoice-reimburse.yaml）

manual（`inputs: {file: "/path/发票.pdf"}` 或 `{dir: "/path/目录"}`）
+ event `invoice-dropped`：

```
recognize（tool 二维码+OCR 识别）→ has-invoices（decision）
  ├─ 识别到 → register（tool 登记 status=待确认，同号码去重）
  │          → confirm（human_node：/tasks 逐张核对，缺字段在此补全）
  │          → commit（tool 确认回流：approve=已确认入账 / reject=已驳回留档）
  └─ 无发票 → finish（通知）
```

缺字段（OCR 认不出的销售方/商品名）进 `warnings`，**不瞎编**——人工确认环节补全。

## 工具

- `invoice-flow__recognize {file?, dir?}` → `{records[], count}`
- `invoice-flow__register {records, note?}` → `{registered[], duplicates[], entry_ids[], confirm_text}`
- `invoice-flow__commit {entry_ids, confirmation}` → `{decision, updated[], ledger}`
- `invoice-flow__ledger {limit=20}` → 台账查询（只读）

## 环境（全部可选）

- `INVOICE_LEDGER_FILE`：台账文件（测试隔离）
- `DHCC_REIMBURSE_DIR`：dhcc-reimbursement 目录（分类器来源）
