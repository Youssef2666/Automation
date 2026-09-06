# T01 test inputs

- `payload.json` - a valid order event (copy of `seed/payloads/t01-order.json`). First POST -> 201, second -> 200 duplicate.
- `invalid.json` - wrong types everywhere (copy of `seed/payloads/t01-invalid.json`) -> 400 with six errors.

See the README "Try it" section for the curl commands (header `X-Lab-Key` required).
