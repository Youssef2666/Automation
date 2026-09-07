# A02 test inputs

A02 reads the demo database, so the "input" is a set of rows. `new-tickets.sql` puts four synthetic tickets at
the head of the queue (dated 90 days back; every seeded open ticket is younger than 45 days), and re-running it
resets them, so the same run can be repeated. `expected.json` is the JSON contract the model has to satisfy plus
what a good run looks like on those four tickets.

```bash
docker compose exec -T postgres psql -U n8n -d demo < workflows/A02-ticket-classifier/test/new-tickets.sql
python scripts/dev/run-workflow.py A02                      # 21 s for the four (76 s cold / contended)
docker compose exec -T postgres psql -U n8n -d demo -c \
  "select ticket_no, category, priority, status, assigned_to from tickets where ticket_no like 'TCK-9%' order by ticket_no"
curl -s "localhost:8025/api/v1/messages?limit=5" | python -m json.tool | grep Subject
```

The fixture is not required: without it the workflow classifies the four oldest seeded `open` tickets instead
(`select ticket_no, subject from tickets where status = 'open' and assigned_to is null order by created_at limit 4`).

## Forcing the failure lane (deterministic, no model needed)

The interesting half of A02 is what happens when the model returns something unusable. To see it without waiting
for a 3B model to misbehave, point the chat model at a model that does not exist and run again:

1. Open **A02 - Ticket Classification and Routing** in n8n, select the **Ollama chat model** node and set the
   model to `no-such-model:1b` (or stop Ollama: `docker compose stop ollama`).
2. `docker compose exec -T postgres psql -U n8n -d demo < workflows/A02-ticket-classifier/test/new-tickets.sql`
3. `python scripts/dev/run-workflow.py A02`

Expected: every ticket takes the **Model output unusable** lane (after one automatic re-sample), four e-mails to
`triage@lab.local` with *Why a human: model output unusable: ...*, four `notifications` rows with severity
`warning`, **no** change to the `tickets` rows (still `open`, still unassigned), and one `execution_log` row with
status `warning` and notes `classified 4 ticket(s): 0 routed, 4 to the triage desk; 4 unparseable`. Set the model
name back afterwards.

To see the *invalid values* lane instead (valid JSON, values outside our enums), change the system message in
**Classify ticket (LLM)** to ask for a category such as `refund`: the parser is happy, **Validate classification**
rejects it, and the ticket still goes to `triage@lab.local` untouched.
