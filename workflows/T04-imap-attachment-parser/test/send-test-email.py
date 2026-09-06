#!/usr/bin/env python
"""send-test-email.py [file] - push a message with a CSV attachment into GreenMail (SMTP localhost:3025) for T04."""
from __future__ import annotations

import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

attachment = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("orders-sample.csv")
msg = EmailMessage()
msg["From"] = "supplier@example.com"
msg["To"] = "inbox@lab.local"
msg["Subject"] = f"Weekly export {time.strftime('%Y-%m-%d %H:%M:%S')}"
msg.set_content("Attached is the weekly export. Synthetic test message from T04.")
msg.add_attachment(attachment.read_bytes(), maintype="text", subtype="csv", filename=attachment.name)
with smtplib.SMTP("localhost", 3025, timeout=15) as smtp:
    smtp.send_message(msg)
print(f"sent to inbox@lab.local via GreenMail with {attachment.name} ({attachment.stat().st_size} bytes)")
