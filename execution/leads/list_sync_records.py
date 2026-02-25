"""
execution/leads/list_sync_records.py

Read-only query of sync_records with optional filters.
No business logic, no writes, no network calls.
"""

from execution.db.sqlite import connect, init_db


def list_sync_records(
    db_path: str | None = None,
    status: str | None = None,
    lead_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return sync_records rows ordered newest-first, with optional filters.

    All arguments are optional.  When a filter is None it is not applied.
    Results are ordered by updated_at DESC so the most recently touched rows
    appear first.  The LIMIT cap prevents unbounded result sets.

    Args:
        db_path: Path to the SQLite file; defaults to tmp/app.db.
        status:  If given, restrict to rows where status = this value
                 (e.g. "NEEDS_SYNC", "SENT", "FAILED").
        lead_id: If given, restrict to rows for this lead.
        limit:   Maximum number of rows to return.  Defaults to 200.

    Returns:
        list of dict, one entry per matching sync_records row.
        Empty list when no rows match.
    """
    clauses: list[str] = []
    params: list[object] = []

    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    if lead_id is not None:
        clauses.append("lead_id = ?")
        params.append(lead_id)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    sql = f"""
        SELECT *
        FROM sync_records
        {where}
        ORDER BY updated_at DESC
        LIMIT ?
    """

    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
