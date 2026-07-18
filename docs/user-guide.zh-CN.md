# 7501 Tax Bill Tool 使用与维护指南

这份文档面向日常试用、交接和后续优化。部署步骤请看 `DEPLOYMENT.zh-CN.md`，代码说明请看 `README.md`。

## 日常使用流程

### 浏览器方式：手动检查/调整

1. 启动服务：

   ```powershell
   python -m uvicorn web_app.app:app --host 127.0.0.1 --port 8000
   ```

2. 打开浏览器：

   ```text
   http://127.0.0.1:8000/
   ```

3. 上传原始 CBP 7501 PDF。
4. 检查解析出的 header、line item、HTS、数量、金额和税费。
5. 修改需要调整的字段。
6. 点击“重新计算”。
7. 确认无校验错误后生成更新税单 PDF。

### 浏览器方式：Excel 表2自动生成

网页左侧的“Excel 自动生成”区域可以直接处理一份原税单和一份 Sample Commercial Invoice & Packing List：

1. 选择原始 CBP 7501 PDF。
2. 选择对应的 Sample Commercial Invoice & Packing List `.xlsx`。
3. 点击“按 Excel 表2生成新税单”。
4. 浏览器会下载新 PDF。

这个流程会保留原始 PDF，不会覆盖原文件。后端会读取 Excel 第二个 worksheet，按 HTS 匹配税单行，并更新：

- 毛重。
- 净数量；如果税单行单位是 `KG`，优先使用 Excel 净重。
- 申报货值。
- duty、MPF、可选 HMF 和总金额。

如果 Excel 与 PDF 无法匹配，或者原 PDF 文本位置无法安全替换，系统会停止生成并显示错误原因。

### Excel 第二张表自动生成方式

文件夹内通常需要：

- 一个原始 7501 PDF。
- 一个包含至少两个 worksheet 的 `.xlsx`。
- Excel 第二张表里包含 HTS/HS 编码、数量、FOB 总价、毛重、净重等列。

运行：

```powershell
$env:TAX_TOOL_USERNAME="your-login-name"
$env:TAX_TOOL_PASSWORD="your-login-password"
python .\tools\excel_workflow.py "C:\path\to\case-folder"
```

如果文件夹里有多个 PDF 或 Excel，可以显式指定：

```powershell
python .\tools\excel_workflow.py "C:\path\to\case-folder" `
  --pdf "C:\path\to\original.pdf" `
  --excel "C:\path\to\invoice.xlsx" `
  --output "C:\path\to\new-tax-bill.pdf" `
  --url "https://tax-bill-tool.onrender.com" `
  --transport-mode auto
```

`--transport-mode auto` 表示按原始税单自动识别：原单有 `501-HMF` 就按新申报货值重新计算，原单没有 `501-HMF` 就不生成 HMF。日常批量建议统一使用这个模式；只有明确需要纠正原始模板时，再手动指定 `ocean` 或 `air`。

### 按月份或日期批量生成

你的桌面文件夹结构可以按下面这种方式处理：

```text
C:\Users\Administrator\Desktop\事项\7501
  6月
    6-26 A
      131-80596740
        131-80596740 税单.pdf
        131-80596740-Sample Commercial Invoice & Packing List.xlsx
```

先预览，不上传、不生成文件：

```powershell
python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --transport-mode auto --dry-run
```

确认列表无误后执行：

```powershell
python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --transport-mode auto
```

建议第一次正式处理先缩小到 10 个 case：

```powershell
python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --limit 10 --transport-mode auto --dry-run

python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --limit 10 --transport-mode auto
```

脚本会递归查找每个 case 文件夹，要求里面有：

- 一个原始税单 PDF。
- 一个 `.xlsx`。

脚本会自动排除名称包含 `副本`、`更新`、`自动修改`、`adjusted` 的 PDF，避免把已经处理过的税单当作原件。

常用范围参数：

```powershell
python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --entry 131-80596740 --transport-mode auto

python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --entry-pattern "^131-" --transport-mode auto

python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" `
  --from-entry 131-80596740 `
  --to-entry 131-80598722 `
  --transport-mode auto

python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --limit 10 --transport-mode auto
```

默认输出：

```text
原税单文件名 - 自动修改.pdf
batch_report_年月日_时分秒.csv
```

如果默认输出文件已经存在，脚本会跳过该 case，避免重复生成。需要重新生成时可以加：

```powershell
python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --transport-mode auto --regenerate
```

批量脚本默认调用线上网站地址 `https://tax-bill-tool.onrender.com`。如果要调用本地服务，先启动：

```powershell
python -m uvicorn web_app.app:app --host 127.0.0.1 --port 8000
```

再运行：

```powershell
python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --transport-mode auto --url "http://127.0.0.1:8000"
```

## 本地检查

每次改代码后建议运行：

```powershell
.\tools\check.ps1
```

如果是第一次配置环境，可以加上依赖安装：

```powershell
.\tools\check.ps1 -InstallDependencies
```

这个脚本会：

- 运行全部 unittest。
- 检查 FastAPI 应用可被正常导入。
- 检查 `/api/health` 对应的健康函数返回正常。

## 测试样本维护

`uploads/` 是临时上传目录，不适合放长期测试样本，也不会提交到 GitHub。

`tests/fixtures/` 是固定脱敏样本目录。只有这些情况才建议新增样本：

- 发现一种新的 7501 PDF 版式。
- 发现一种新的 Excel 第二张表格式。
- 修复了一个重要 bug，需要防止以后复发。
- 需要覆盖特殊税率、Chapter 99、KG/NO 单位、MPF/HMF 等边界场景。

推荐结构：

```text
tests/fixtures/
  case_001_excel_adjustment/
    input_lines.json
    worksheet2_rows.json
    expected.json

  case_002_real_7501_layout/
    original.pdf
    invoice.xlsx
    expected.json
```

提交真实业务样本前必须脱敏：

- 替换公司名、地址、联系人、电话、邮箱。
- 替换 Entry No、Invoice No、客户编号等识别信息。
- 保留字段位置、页数、行数、金额格式、单位格式和 HTS 格式。
- 如果金额被替换，需要同步更新 `expected.json`。

## 常见错误排查

### Excel 无法匹配税单行

通常原因：

- Excel 第二张表 HTS 与 PDF 税单 HTS 不一致。
- Excel 行数少于 PDF line items。
- HTS 有隐藏空格或格式被 Excel 自动改成数字。

建议：

- 把 HTS 列设置为文本格式。
- 确认 10 位 HTS 数字完整。
- 对照导出的 JSON 检查 PDF 解析出的 HTS。

### 无法安全生成 PDF

程序只替换原 PDF 中能精确定位的文本对象。如果原 PDF 版式特殊、文本被拆碎、字体不支持，程序会停止生成，避免输出错位税单。

建议：

- 保留这份 PDF 的脱敏版本。
- 添加到 `tests/fixtures/` 作为新 case。
- 再针对该版式优化解析或替换规则。

### KG 净重超过毛重

程序会阻止 `KG` 净数量大于 `KG` 毛重的修改。这通常表示 Excel 列选错、单位理解错误，或源表公式结果未刷新。

建议：

- 确认 Excel 已保存，公式结果可被读取。
- 检查毛重/净重列是否为 item-level 总重，而不是单箱或单件重量。
