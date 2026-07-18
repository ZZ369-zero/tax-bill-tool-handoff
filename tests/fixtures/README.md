# 测试样本说明

这个目录用于存放可提交到 GitHub 的“固定脱敏样本”，帮助后续优化解析、Excel 匹配和 PDF 生成流程时做回归验证。

请只把已经脱敏、可长期保留的样本放到这里；日常真实上传文件仍然应该放在 `uploads/` 或你的业务工作目录中，不要提交到仓库。

推荐每个样本单独建一个 case 目录：

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

添加新样本的时机：

- 遇到一种新的 7501 PDF 版式。
- 遇到一种新的 Excel 第二张表格式或列名。
- 修复了一个重要 bug，希望以后自动防止复发。
- 需要覆盖特殊税率、Chapter 99、KG/NO 单位、MPF/HMF 等边界情况。

真实业务文件脱敏建议：

- 替换公司名、地址、联系人、电话、邮箱、Entry No、Invoice No。
- 保留字段位置、页数、行数、金额格式、单位格式和 HTS 格式。
- 金额可以整体缩放或替换，但要同步更新 `expected.json`。
