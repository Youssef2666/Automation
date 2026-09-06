# T05 test inputs

- `submission.sh` - posts the sample form (override NAME/EMAIL/COMPANY/TOPIC/MESSAGE as env vars).
- `submission.json` - the item the Form Trigger emits for it (keys = field labels).
Expected: one `leads` row (`source=form`, score 60), one confirmation e-mail in Mailpit, one `notifications` row.
