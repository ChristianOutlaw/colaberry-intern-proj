
## HotLeadSignal (MVP v1)

`HotLeadSignal` is computed on read inside `execution/leads/get_lead_status.py`
by calling `execution/leads/compute_hot_lead_signal.py`. The rule spec is defined
in `directives/HOT_LEAD_SIGNAL.md` (three gates: invite sent, completion ≥ 25 %,
last activity within 7 days). The result is returned as `hot_lead.signal`
(`"HOT"` or `"NOT_HOT"`) and `hot_lead.reason` in the `LeadStatus` dict.

The `hot_lead_signals` table exists in the schema but is **not written to in
MVP v1**. It is reserved for future snapshot or audit use (e.g., GHL push records).
