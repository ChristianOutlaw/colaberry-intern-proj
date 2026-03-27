"""
execution/ingestion/bulk_ingest_leads.py

Minimal bulk ingestion seam: validates and upserts a list of lead payloads,
one at a time, using the existing upsert_lead() single-lead function.

No batching, no retries, no queues, no async — this is a thin loop with
per-lead error capture so callers can see partial failures.

Required payload fields (per upsert_lead / leads table schema):
  - id  (str, non-empty) — PRIMARY KEY; the only required field

Optional payload fields (passed through to upsert_lead when present):
  - name   (str | None)
  - email  (str | None)
  - phone  (str | None)
"""

from execution.leads.upsert_lead import upsert_lead


def _validate_payload(payload: object) -> str | None:
    """Return an error message string, or None if the payload is valid.

    A payload is valid when it is a dict with a non-empty string 'id'.
    All other fields are optional.
    """
    if not isinstance(payload, dict):
        return f"payload must be a dict, got {type(payload).__name__}"
    lead_id = payload.get("id")
    if not isinstance(lead_id, str) or not lead_id.strip():
        return f"'id' must be a non-empty string, got {lead_id!r}"
    return None


def bulk_ingest_leads(
    payloads: list[dict],
    db_path: str | None = None,
) -> list[dict]:
    """Ingest a list of lead payloads, capturing success or failure per lead.

    Iterates over the list in order. Each payload is validated then passed
    to upsert_lead(). Errors are caught per lead so one bad payload does not
    abort the rest.

    Args:
        payloads: List of dicts. Each dict must have at minimum:
                    { "id": "<non-empty-string>" }
                  Optional keys: "name", "email", "phone".
        db_path:  Path to the SQLite file. Uses default dev DB when None.

    Returns:
        A list of result dicts, one per input payload, in the same order:
            { "lead_id": str, "success": True }
            { "lead_id": str, "success": False, "error": str }

        When a payload fails validation before upsert, lead_id is the raw
        value of payload.get("id") (may be None or the invalid value).
    """
    if not payloads:
        return []

    results: list[dict] = []

    for payload in payloads:
        error = _validate_payload(payload)
        raw_id = payload.get("id") if isinstance(payload, dict) else None

        if error:
            results.append({"lead_id": raw_id, "success": False, "error": error})
            continue

        lead_id = payload["id"].strip()
        try:
            upsert_lead(
                lead_id=lead_id,
                name=payload.get("name"),
                email=payload.get("email"),
                phone=payload.get("phone"),
                db_path=db_path,
            )
            results.append({"lead_id": lead_id, "success": True})
        except Exception as exc:  # noqa: BLE001
            results.append({"lead_id": lead_id, "success": False, "error": str(exc)})

    return results
