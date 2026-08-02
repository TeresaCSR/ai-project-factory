# Artifact Manifest

Record the important outputs that Git does not track, that `.gitignore`
excludes, that live somewhere external, or whose version cannot be told from
the filename alone. Ordinary source code does not need an entry here.

For DOCX, PDF, COMSOL/ANSYS models, datasets, images, spreadsheets, and other
significant binaries, prefer recording the relative path, version, size, and
SHA-256. Use a stable URI or an explicit description of where an external file
lives, and never paste a link that carries a password.

`Availability` takes only `VERIFIED`, `EXTERNAL`, `MISSING`, or `UNKNOWN`.
A `VERIFIED` local file has its size and SHA-256 recomputed on `check` and on
`export`.

| ID | Relative path or URI | Version | Size bytes | SHA-256 | Availability | Notes |
|---|---|---|---:|---|---|---|
| none | none | none | 0 | none | UNKNOWN | Fill in as artifacts appear |

To summarise a local file:

```text
python .ai/project_memory.py hash-file <artifact-path> --project .
```
