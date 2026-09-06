# M01 test inputs

M01 probes live services; the inputs are the targets in the "Targets" Code node (mock-api, n8n, minio and the
always-failing `simulated-outage`). `state.sql` shows the state machine and how to reset it.
Run three times: `simulated-outage` goes up -> warn -> warn -> down and one DOWN e-mail lands in Mailpit.
