-- A02 test fixture: four synthetic tickets that land at the head of the triage queue.
--
-- The workflow picks the oldest `open` tickets with no assignee, so these are dated 90 days back to make a demo
-- run deterministic (every seeded open ticket is younger than 45 days). Re-running the file resets them, so you
-- can classify the same four tickets as often as you like.
--
--   docker compose exec -T postgres psql -U n8n -d demo < workflows/A02-ticket-classifier/test/new-tickets.sql

BEGIN;

DELETE FROM tickets WHERE ticket_no IN ('TCK-9001', 'TCK-9002', 'TCK-9003', 'TCK-9004');

INSERT INTO tickets (ticket_no, customer_id, channel, subject, body, category, priority, status, assigned_to,
                     sla_due_at, created_at)
VALUES
  -- 1. unambiguous billing, clear business impact -> expect billing, high/urgent
  ('TCK-9001', (SELECT id FROM customers ORDER BY id LIMIT 1), 'email',
   'Invoice INV-2026-0187 was charged twice',
   'Hello, our card was charged twice for invoice INV-2026-0187 this morning - the same amount left our account '
   'at 09:14 and 09:16. Please refund the duplicate charge and confirm which of the two payments you keep on '
   'record. Finance needs this before the month closes.',
   'billing', 'normal', 'open', NULL, now() - interval '89 days', now() - interval '90 days'),

  -- 2. unambiguous technical, blocking -> expect technical, high/urgent
  ('TCK-9002', (SELECT id FROM customers ORDER BY id OFFSET 5 LIMIT 1), 'form',
   'API returns 500 on /orders since this morning',
   'Hi team, every call to GET /orders has answered 500 since about 08:00 today. The same token still works on '
   '/products, so it does not look like an auth problem on our side. Our order sync has been down for four '
   'hours and nothing is reaching the warehouse.',
   'technical', 'normal', 'open', NULL, now() - interval '89 days', now() - interval '90 days'),

  -- 3. Arabic shipping ticket - the hard one for a 3B model
  ('TCK-9003', (SELECT id FROM customers ORDER BY id OFFSET 11 LIMIT 1), 'email',
   'لم يصل الطرد الخاص بالطلب ORD-2026-0142',
   'مرحباً، لم يصل الطرد الخاص بالطلب ORD-2026-0142 حتى الآن، ورقم التتبع لا يظهر أي تحديث منذ أسبوع. '
   'نرجو المتابعة مع شركة الشحن وإبلاغنا بموعد التسليم المتوقع. شكراً لكم.',
   'shipping', 'normal', 'open', NULL, now() - interval '89 days', now() - interval '90 days'),

  -- 4. deliberately vague: no category is really right -> expect a low-confidence "other", ideally the review lane
  ('TCK-9004', (SELECT id FROM customers ORDER BY id OFFSET 17 LIMIT 1), 'chat',
   'Re: Fwd: quick question',
   'Hi, following up on the thing we discussed last week. Can someone have a look and let me know? Thanks.',
   'other', 'normal', 'open', NULL, now() - interval '89 days', now() - interval '90 days');

COMMIT;

SELECT ticket_no, channel, category AS label_in_seed, priority, status, assigned_to,
       left(subject, 44) AS subject
  FROM tickets
 WHERE ticket_no LIKE 'TCK-9%'
 ORDER BY ticket_no;
