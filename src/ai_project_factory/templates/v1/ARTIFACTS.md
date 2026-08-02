# Artifact Manifest

记录 Git 未跟踪、被 `.gitignore` 排除、存放在外部位置，或仅凭文件名无法确认
版本的重要成果。普通源代码无需在这里逐项重复。

对于 DOCX、PDF、COMSOL/ANSYS 模型、数据集、图片、表格和其他重要二进制
文件，优先记录相对路径、版本、大小和 SHA-256。外部文件使用稳定 URI 或明确
的存储位置说明，不要写入带密码的链接。

`Availability` 只使用 `VERIFIED`、`EXTERNAL`、`MISSING` 或 `UNKNOWN`。
`VERIFIED` 本地文件会在 `check` 和 `export` 时重新计算大小与 SHA-256。

| ID | Relative path or URI | Version | Size bytes | SHA-256 | Availability | Notes |
|---|---|---|---:|---|---|---|
| 尚无 | 尚无 | 尚无 | 0 | 尚无 | UNKNOWN | 初始化后按需填写 |

可用下面的命令生成本地文件摘要：

```text
python .ai/project_memory.py hash-file <artifact-path> --project .
```
