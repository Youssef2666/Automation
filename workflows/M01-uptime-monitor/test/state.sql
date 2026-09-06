-- Current escalation state and the last probes.
select target, state, failures, since, last_alert_at from uptime_state order by target;
select target, ok, status_code, latency_ms, checked_at from uptime_checks order by id desc limit 8;
-- Reset the simulated outage so the next three runs escalate again:
-- update uptime_state set state='up', failures=0, last_alert_at=null where target='simulated-outage';
