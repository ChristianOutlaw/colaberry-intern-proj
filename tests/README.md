# tests/

Tests are organized by domain into subdirectories. All tests are fast, deterministic, and use isolated SQLite files — no network calls, no production data.

## Directory structure

| Subdirectory | What it covers |
| --- | --- |
| `tests/admin/` | Admin tooling, dev-mode helpers |
| `tests/db/` | SQLite schema, init, connection helpers |
| `tests/decision/` | Next-action decision logic |
| `tests/progress/` | Course progress events and state |
| `tests/scans/` | Scan finders, scan runners, scan registry, export |
| `tests/leads/` | Leads, GHL intake/sync, Cory dispatch, invite, scoring |
| `tests/content/` | Course map loading, quiz library validation |

## Root-level files (intentional, not leftovers)

Three integration/e2e tests intentionally remain at `tests/` root because they exercise multiple layers simultaneously and do not belong to a single domain:

- `test_e2e_orchestration.py` — full cross-layer orchestration flow
- `test_failed_scan_requeue_integration.py` — scan → requeue → dispatch integration
- `test_completion_finalization_fallback_summary_integration.py` — completion finalization across layers

These are deliberate exceptions. Any new test that is not an integration test should go into the appropriate subdirectory above.

## How to find the right test

- **Leads, GHL, invites, Cory sync/dispatch** → `tests/leads/`
- **Scan finders or scan runners** → `tests/scans/`
- **Course content, quiz validation** → `tests/content/`
- **Scoring, temperature, label classification** → `tests/leads/`
- **Database schema or connection helpers** → `tests/db/`
- **Cross-layer behavior touching 2+ domains** → `tests/` root (integration)

## Running tests

```bash
# Full suite
python -m pytest tests/ --tb=line -q

# Single domain
python -m pytest tests/leads/ -v
python -m pytest tests/scans/ -v
python -m pytest tests/content/ -v

# Single file
python -m pytest tests/leads/test_sync_ghl_contact_id.py -v
```
