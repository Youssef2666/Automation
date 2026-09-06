#!/usr/bin/env bash
# Submit the sample contact form (fields are posted in form order: field-0 .. field-4).
set -euo pipefail
BASE=${N8N_URL:-http://localhost:5678}
curl -s -X POST "$BASE/form/t05-contact" \
  -F "field-0=${NAME:-Lina Haddad}" -F "field-1=${EMAIL:-lina.haddad@lab.local}" -F "field-2=${COMPANY:-Sample Bakery}" \
  -F "field-3=${TOPIC:-Demo request}" -F "field-4=${MESSAGE:-Please show me the invoice workflow.}" | grep -o "Form Submitted" || echo "no confirmation page - is T05 published?"
