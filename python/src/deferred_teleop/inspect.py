"""Read-only causal-history inspector for M1 node databases."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from uuid import UUID


def _causal_history(data_dir: Path, correlation_id: UUID) -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    for node, filename in (("mission", "mission.db"), ("field", "field.db"), ("robot", "robot.db")):
        database = data_dir / filename
        if not database.is_file():
            raise FileNotFoundError(database)
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT 'inbox' AS direction, message_id, received_at AS stored_at,
                       payload_type, payload_json
                FROM inbox WHERE correlation_id = ?
                UNION ALL
                SELECT 'outbox' AS direction, message_id, created_at AS stored_at,
                       json_extract(payload_json, '$.message_type') AS payload_type, payload_json
                FROM outbox WHERE correlation_id = ?
                ORDER BY stored_at, message_id
                """,
                (str(correlation_id), str(correlation_id)),
            ).fetchall()
        finally:
            connection.close()
        for raw in rows:
            row = dict(raw)
            payload = json.loads(str(row.pop("payload_json")))
            history.append({"node": node, **row, "envelope": payload})
    return sorted(
        history,
        key=lambda item: (str(item["stored_at"]), str(item["message_id"]), str(item["node"])),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    causal = subparsers.add_parser("causal-history")
    causal.add_argument("--data-dir", type=Path, required=True)
    causal.add_argument("--correlation-id", type=UUID, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "correlation_id": str(args.correlation_id),
                "history": _causal_history(args.data_dir, args.correlation_id),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
