# T04 test inputs

- `send-test-email.py [file]` - sends a message from supplier@example.com to inbox@lab.local through GreenMail's
  SMTP port (localhost:3025) with `orders-sample.csv` (or the given file) attached.
- `orders-sample.csv` - 2-row synthetic CSV.
Expected within ~20 s: one execution (mode trigger), `documents` row kind `email-attachment` with `csv_rows: 2`,
`artifacts/mail/orders-sample.csv` in MinIO, and an acknowledgement e-mail in Mailpit addressed to the sender.
