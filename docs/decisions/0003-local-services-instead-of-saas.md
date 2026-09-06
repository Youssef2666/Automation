# 0003 - Local services instead of SaaS on every core path

Date: 2026-09-06
Status: accepted

**Context**: Goal G3 forbids paid services, API keys or cards on any core path, yet the catalog needs inboxes, S3,
LLMs, third-party APIs to poll and PDF generation. The PRD listed Mailpit, MinIO, Ollama and mock-api but no IMAP
server and no document renderer.
**Decision**: Every external dependency has a local stand-in with a canonical credential: Mailpit (SMTP sink),
GreenMail (IMAP, because Mailpit has none), MinIO (S3), mock-api (json-server: pagination, flaky, rate-limited,
RSS, HTML, FX endpoints), docgen (FastAPI: WeasyPrint PDF, DOCX/PPTX with Arabic RTL, pdfplumber, Tesseract;
`docs` profile), Ollama + Qdrant + Whisper (`ai` profile). Telegram and GitHub token variants stay optional.
**Consequences**: The core profile is seven containers, not four, and docgen is a custom image to maintain. In
exchange the smoke test runs on a bare CI runner and no "Try it" section says "get a key first". Swapping a
stand-in for the real service is a credential change, not a workflow change.
