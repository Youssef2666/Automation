# R03 test inputs

- `docrequest-sample.json` - the exact structured document the workflow sends to docgen (captured from a live run).
  Iterate on layout without n8n:

```bash
curl -s -X POST localhost:8090/render/docx -H 'Content-Type: application/json' -d @workflows/R03-arabic-rtl-report/test/docrequest-sample.json -o /tmp/report.docx
curl -s -X POST localhost:8090/render/pptx -H 'Content-Type: application/json' -d @workflows/R03-arabic-rtl-report/test/docrequest-sample.json -o /tmp/report.pptx
```

The data itself comes from the seeded `employees` (Arabic names/departments) and `attendance` tables.
