# Automation in small businesses: where it pays off first

Most small businesses do not have an "automation problem". They have a Tuesday problem. On Tuesday the invoices go
out, the supplier spreadsheet gets reconciled, the same six customer questions arrive by e-mail, and somebody copies
booking details from a form into a calendar. None of these tasks is hard. All of them are boring, all of them are
repeated, and each one is a small opportunity to make a typo that costs an afternoon to untangle.

This article looks at automation from the point of view of a team of three to thirty people. It does not assume a
developer on staff, a cloud budget, or patience for vendor demos. It assumes a laptop, a couple of hours, and a
willingness to write down what actually happens on Tuesday.

## Start with the handoffs, not the tools

The first mistake teams make is to start from a tool. They sign up for a platform, look at the catalogue of
integrations, and try to find something to connect. The better starting point is the handoff: any moment where
information leaves one place and has to be typed into another. An order arriving by e-mail that becomes a row in a
spreadsheet. A receipt photographed on a phone that becomes a line in the expense report. A support request that
becomes a ticket, a ticket that becomes a task, a task that becomes a status update.

Write those handoffs down. For each one, note three things: how often it happens, how long it takes, and how often
it goes wrong. The ranking that falls out of that list is your roadmap. It is almost never the flashy item. It is
usually the one that happens forty times a week and goes wrong twice.

## The three shapes of an automation

Nearly every useful workflow in a small business is one of three shapes.

The first is the trigger and record shape. Something happens outside, a form is submitted, a webhook fires, a file
lands in a folder, and the workflow validates it and stores it somewhere structured. The value here is not speed; it
is that the record is complete and consistent every single time. A validation step that rejects a malformed order
with a clear message is worth more than a fast import that accepts anything.

The second is the schedule and report shape. Every morning, every Friday, or every month, the workflow gathers rows
from one or more sources, does some arithmetic, and produces a document or a message. Attendance summaries, sales
digests, exchange-rate snapshots and uptime reports all live here. These are the automations people notice, because
they replace a document somebody used to build by hand.

The third is the watch and alert shape. The workflow polls or listens, compares the current state with the last one
it saw, and notifies a human only when something changed. A price crossed a threshold, a website stopped answering, a
new release was published. The hard part of this shape is not the check; it is the memory. A watcher that forgets
what it saw last time will alert forever.

## What goes wrong, and how to plan for it

Automations fail in predictable ways, and the predictability is good news, because it means the fixes are reusable.

Duplicates are the most common failure. Webhooks are delivered twice, a poll overlaps with the previous run, a person
clicks submit twice. The fix is an idempotency key: a unique identifier per event, checked before doing any work.
It takes one lookup and saves hours of cleanup.

Transient errors come next. An API times out, a mail server refuses a connection for ten seconds, a rate limit kicks
in. The fix is a retry with a growing delay and a hard cap on attempts. Retrying immediately is not a retry; it is a
second failure.

Silent errors are the most expensive. A workflow stops running and nobody notices for a week. The fix is an error
handler that is shared by every workflow and that records the failure somewhere a human actually looks, plus a
heartbeat that alerts when a scheduled job has not reported in.

Finally, there is drift. The spreadsheet gains a column, the supplier renames a field, a colleague changes the form.
There is no clever fix for drift; there is only a test payload kept next to the workflow and a habit of running it
after every change.

## Keeping it local and honest

A surprising amount of automation does not need to leave the building. A small database, a mail sink, a local
document renderer and an optical character recognition service cover invoices, receipts, reports and inbound mail
without a single external account. Local language models are now good enough to classify a support ticket, summarise
a meeting recording or draft a first reply, and they run on ordinary hardware.

Working locally has a second benefit that is easy to underestimate: it forces you to use synthetic data. When the
demo customers are invented, you can share the workflow, screenshot it, and hand it to a new colleague without a
privacy review. The habits you build with fake data are the habits that protect the real data later.

## A realistic first month

Week one: list the handoffs and pick one. Week two: build the trigger and record version of it with a test payload
and a validation step. Week three: add the error handler and the idempotency check, then let it run unattended.
Week four: build the report that proves it worked, and show it to the people whose Tuesday just got shorter.

That is the whole method. It is not glamorous, and it does not require a platform decision. It requires noticing
where information is copied by hand, and refusing to do that copying more than once.
