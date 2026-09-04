"""Durable SQLite endpoint state for the M1 delayed-dummy runtime.

The store provides at-least-once message processing and an effect-once database
boundary for the dummy effect. It deliberately does not claim that SQLite can
make a future external physical action exactly-once.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from deferred_teleop.protocol import ContractState, ExecutionEvent, MessageEnvelope

CURRENT_SCHEMA_VERSION = 2
DEFAULT_BUSY_TIMEOUT_MS = 5_000
TERMINAL_STATES = frozenset(
    {
        ContractState.SUCCEEDED.value,
        ContractState.FAILED.value,
        ContractState.HELD.value,
        ContractState.CANCELLED.value,
    }
)


class StorageError(RuntimeError):
    """Base class for durable-store failures."""


class IncompatibleSchemaError(StorageError):
    """The database schema is newer than this implementation."""


class CorruptRecordError(StorageError):
    """A persisted record cannot be decoded without changing forensic data."""


class RecordConflictError(StorageError):
    """A stable identifier was reused for different immutable content."""


class InvalidStateTransitionError(StorageError):
    """A requested durable state transition is illegal."""


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _now_text() -> str:
    return _utc_text(datetime.now(UTC))


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _envelope_json(envelope: MessageEnvelope) -> str:
    return _canonical_json(envelope.model_dump(mode="json"))


def _result_json(result: Mapping[str, Any]) -> str:
    try:
        encoded = _canonical_json(result)
    except (TypeError, ValueError) as error:
        raise ValueError("terminal results must be JSON objects") from error
    if not isinstance(json.loads(encoded), dict):
        raise ValueError("terminal results must be JSON objects")
    return encoded


def _configure(connection: sqlite3.Connection, busy_timeout_ms: int) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")


def _migration_1(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE inbox (
            message_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_boot_id TEXT NOT NULL,
            source_sequence INTEGER NOT NULL CHECK (source_sequence >= 0),
            correlation_id TEXT NOT NULL,
            received_at TEXT NOT NULL,
            payload_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            processing_state TEXT NOT NULL
                CHECK (processing_state IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'FAILED')),
            processed_at TEXT,
            handler_result_reference TEXT,
            error_json TEXT
        )
        """,
        """
        CREATE TABLE outbox (
            message_id TEXT PRIMARY KEY,
            destination_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            not_before TEXT,
            expires_at TEXT,
            payload_json TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            next_attempt_at TEXT,
            ack_state TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (ack_state IN ('PENDING', 'ACKED')),
            acked_at TEXT
        )
        """,
        """
        CREATE TABLE execution_journal (
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL CHECK (contract_revision >= 1),
            operation_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            state TEXT NOT NULL,
            effect_key TEXT NOT NULL UNIQUE,
            effect_count INTEGER NOT NULL DEFAULT 0 CHECK (effect_count IN (0, 1)),
            accepted_at TEXT NOT NULL,
            dispatch_recorded_at TEXT,
            effect_started_at TEXT,
            terminal_at TEXT,
            terminal_result_json TEXT,
            PRIMARY KEY (contract_id, contract_revision)
        )
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _migration_2(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE execution_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY (contract_id, contract_revision)
                REFERENCES execution_journal (contract_id, contract_revision)
        )
        """,
        "CREATE INDEX inbox_processing_idx ON inbox (processing_state, received_at)",
        "CREATE INDEX outbox_pending_idx ON outbox (ack_state, next_attempt_at, created_at)",
        "CREATE INDEX inbox_correlation_idx ON inbox (correlation_id)",
        "CREATE INDEX outbox_correlation_idx ON outbox (correlation_id)",
    )
    for statement in statements:
        connection.execute(statement)


MIGRATIONS = {1: _migration_1, 2: _migration_2}


def initialize_database(
    path: str | Path,
    *,
    target_version: int = CURRENT_SCHEMA_VERSION,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> None:
    """Create or upgrade a node database through explicit numbered migrations."""

    if not 0 <= target_version <= CURRENT_SCHEMA_VERSION:
        raise ValueError(f"unsupported migration target: {target_version}")
    connection = sqlite3.connect(Path(path), isolation_level=None)
    try:
        _configure(connection, busy_timeout_ms)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        current = int(row[0] or 0)
        if current > CURRENT_SCHEMA_VERSION:
            raise IncompatibleSchemaError(
                f"database schema {current} is newer than supported {CURRENT_SCHEMA_VERSION}"
            )
        for version in range(current + 1, target_version + 1):
            connection.execute("BEGIN IMMEDIATE")
            try:
                MIGRATIONS[version](connection)
                connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, _now_text()),
                )
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
    finally:
        connection.close()


class NodeStore:
    """One durable SQLite store for one logical Mission, Field, or Robot node."""

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        initialize_database(self.path, busy_timeout_ms=busy_timeout_ms)
        self._connection = sqlite3.connect(self.path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        _configure(self._connection, busy_timeout_ms)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> NodeStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    @property
    def schema_version(self) -> int:
        row = self._connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    def pragmas(self) -> dict[str, int | str]:
        journal_mode = str(self._connection.execute("PRAGMA journal_mode").fetchone()[0])
        busy_timeout = int(self._connection.execute("PRAGMA busy_timeout").fetchone()[0])
        return {"journal_mode": journal_mode, "busy_timeout_ms": busy_timeout}

    def receive(self, envelope: MessageEnvelope, *, received_at: datetime) -> bool:
        """Persist before handling; return False only for an identical duplicate."""

        encoded = _envelope_json(envelope)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM inbox WHERE message_id = ?", (str(envelope.message_id),)
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != encoded:
                    raise RecordConflictError(f"inbox message_id collision: {envelope.message_id}")
                return False
            connection.execute(
                """
                INSERT INTO inbox (
                    message_id, source_id, source_boot_id, source_sequence, correlation_id,
                    received_at, payload_type, payload_json, processing_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RECEIVED')
                """,
                (
                    str(envelope.message_id),
                    envelope.source_id,
                    str(envelope.source_boot_id),
                    envelope.source_sequence,
                    str(envelope.correlation_id),
                    _utc_text(received_at),
                    envelope.message_type,
                    encoded,
                ),
            )
        return True

    def recover_interrupted_processing(self) -> int:
        """Make messages claimed by a crashed handler available for retry."""

        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE inbox SET processing_state = 'RECEIVED'
                WHERE processing_state = 'PROCESSING'
                """
            )
        return cursor.rowcount

    def claim_next_inbox(self) -> MessageEnvelope | None:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT message_id, payload_json FROM inbox
                WHERE processing_state IN ('RECEIVED', 'FAILED')
                ORDER BY CASE processing_state WHEN 'RECEIVED' THEN 0 ELSE 1 END,
                         received_at, message_id LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            envelope = self._decode_envelope("inbox", row["message_id"], row["payload_json"])
            connection.execute(
                """
                UPDATE inbox SET processing_state = 'PROCESSING', error_json = NULL
                WHERE message_id = ?
                """,
                (row["message_id"],),
            )
            return envelope

    def claim_inbox(self, message_id: UUID) -> MessageEnvelope | None:
        """Claim one known inbox message, or return None if it is no longer claimable."""

        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM inbox
                WHERE message_id = ? AND processing_state IN ('RECEIVED', 'FAILED')
                """,
                (str(message_id),),
            ).fetchone()
            if row is None:
                return None
            envelope = self._decode_envelope("inbox", str(message_id), row["payload_json"])
            connection.execute(
                """
                UPDATE inbox SET processing_state = 'PROCESSING', error_json = NULL
                WHERE message_id = ?
                """,
                (str(message_id),),
            )
            return envelope

    def fail_inbox(self, message_id: UUID, error: Mapping[str, Any]) -> None:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE inbox SET processing_state = 'FAILED', error_json = ?
                WHERE message_id = ? AND processing_state = 'PROCESSING'
                """,
                (_result_json(error), str(message_id)),
            )
            if cursor.rowcount != 1:
                raise InvalidStateTransitionError("only PROCESSING inbox messages can fail")

    def complete_inbox(
        self,
        message_id: UUID,
        *,
        processed_at: datetime,
        handler_result_reference: str,
        outgoing: Sequence[MessageEnvelope] = (),
    ) -> None:
        """Atomically commit handler completion and all outgoing consequences."""

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT processing_state FROM inbox WHERE message_id = ?", (str(message_id),)
            ).fetchone()
            if row is None or row["processing_state"] != "PROCESSING":
                raise InvalidStateTransitionError("only PROCESSING inbox messages can complete")
            for envelope in outgoing:
                self._insert_outbox(connection, envelope)
            connection.execute(
                """
                UPDATE inbox SET processing_state = 'PROCESSED', processed_at = ?,
                    handler_result_reference = ?, error_json = NULL
                WHERE message_id = ?
                """,
                (_utc_text(processed_at), handler_result_reference, str(message_id)),
            )

    def enqueue(self, envelope: MessageEnvelope) -> bool:
        with self._transaction() as connection:
            return self._insert_outbox(connection, envelope)

    def _insert_outbox(
        self, connection: sqlite3.Connection, envelope: MessageEnvelope
    ) -> bool:
        encoded = _envelope_json(envelope)
        existing = connection.execute(
            "SELECT payload_json FROM outbox WHERE message_id = ?", (str(envelope.message_id),)
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != encoded:
                raise RecordConflictError(f"outbox message_id collision: {envelope.message_id}")
            return False
        connection.execute(
            """
            INSERT INTO outbox (
                message_id, destination_id, correlation_id, created_at, not_before,
                expires_at, payload_json, next_attempt_at, ack_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                str(envelope.message_id),
                envelope.destination_id,
                str(envelope.correlation_id),
                _utc_text(envelope.created_at),
                _utc_text(envelope.not_before) if envelope.not_before else None,
                _utc_text(envelope.expires_at) if envelope.expires_at else None,
                encoded,
                _utc_text(envelope.not_before or envelope.created_at),
            ),
        )
        return True

    def pending_outbox(self, *, now: datetime) -> list[MessageEnvelope]:
        rows = self._connection.execute(
            """
            SELECT message_id, payload_json FROM outbox
            WHERE ack_state = 'PENDING'
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at, message_id
            """,
            (_utc_text(now), _utc_text(now)),
        ).fetchall()
        return [
            self._decode_envelope("outbox", row["message_id"], row["payload_json"])
            for row in rows
        ]

    def record_attempt(self, message_id: UUID, *, next_attempt_at: datetime) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE outbox SET attempt_count = attempt_count + 1, next_attempt_at = ?
                WHERE message_id = ? AND ack_state = 'PENDING'
                """,
                (_utc_text(next_attempt_at), str(message_id)),
            )
            if cursor.rowcount != 1:
                raise InvalidStateTransitionError("only pending outbox messages can be attempted")
            row = connection.execute(
                "SELECT attempt_count FROM outbox WHERE message_id = ?", (str(message_id),)
            ).fetchone()
            return int(row["attempt_count"])

    def acknowledge(self, message_id: UUID, *, acked_at: datetime) -> bool:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT ack_state FROM outbox WHERE message_id = ?", (str(message_id),)
            ).fetchone()
            if row is None:
                raise KeyError(str(message_id))
            if row["ack_state"] == "ACKED":
                return False
            connection.execute(
                "UPDATE outbox SET ack_state = 'ACKED', acked_at = ? WHERE message_id = ?",
                (_utc_text(acked_at), str(message_id)),
            )
            return True

    def accept_contract(
        self,
        *,
        contract_id: UUID,
        contract_revision: int,
        operation_id: UUID,
        task_id: UUID,
        effect_key: str,
        accepted_at: datetime,
    ) -> bool:
        immutable = (str(operation_id), str(task_id), effect_key)
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT operation_id, task_id, effect_key FROM execution_journal
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (str(contract_id), contract_revision),
            ).fetchone()
            if row is not None:
                if tuple(row) != immutable:
                    raise RecordConflictError("contract revision collision")
                return False
            try:
                connection.execute(
                    """
                    INSERT INTO execution_journal (
                        contract_id, contract_revision, operation_id, task_id, state,
                        effect_key, accepted_at
                    ) VALUES (?, ?, ?, ?, 'ACCEPTED', ?, ?)
                    """,
                    (
                        str(contract_id),
                        contract_revision,
                        str(operation_id),
                        str(task_id),
                        effect_key,
                        _utc_text(accepted_at),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RecordConflictError(f"effect_key already claimed: {effect_key}") from error
        return True

    def record_dispatch(
        self, contract_id: UUID, contract_revision: int, *, recorded_at: datetime
    ) -> bool:
        with self._transaction() as connection:
            row = self._journal_row(connection, contract_id, contract_revision)
            if row["dispatch_recorded_at"] is not None:
                return False
            if row["state"] != "ACCEPTED":
                raise InvalidStateTransitionError("dispatch requires ACCEPTED state")
            connection.execute(
                """
                UPDATE execution_journal
                SET state = 'DISPATCH_RECORDED', dispatch_recorded_at = ?
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (_utc_text(recorded_at), str(contract_id), contract_revision),
            )
        return True

    def commit_dummy_effect(
        self,
        contract_id: UUID,
        contract_revision: int,
        *,
        terminal_state: ContractState,
        terminal_result: Mapping[str, Any],
        occurred_at: datetime,
        terminal_event: MessageEnvelope,
    ) -> bool:
        """Atomically record one dummy effect, terminal result, and its outbox event."""

        if terminal_state.value not in TERMINAL_STATES:
            raise ValueError("terminal_state must be terminal")
        event = terminal_event.payload
        if not isinstance(event, ExecutionEvent):
            raise ValueError("terminal_event must contain an ExecutionEvent")
        if (
            event.contract_id != contract_id
            or event.contract_revision != contract_revision
            or event.previous_state is not ContractState.RUNNING
            or event.next_state is not terminal_state
        ):
            raise RecordConflictError("terminal event does not match the journal transition")
        with self._transaction() as connection:
            row = self._journal_row(connection, contract_id, contract_revision)
            if row["terminal_at"] is not None:
                return False
            if row["state"] != "DISPATCH_RECORDED" or row["effect_count"] != 0:
                raise InvalidStateTransitionError(
                    "dummy effect requires one unconsumed DISPATCH_RECORDED journal entry"
                )
            encoded_result = _result_json(terminal_result)
            self._insert_outbox(connection, terminal_event)
            connection.execute(
                """
                UPDATE execution_journal SET state = ?, effect_count = 1,
                    effect_started_at = ?, terminal_at = ?, terminal_result_json = ?
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (
                    terminal_state.value,
                    _utc_text(occurred_at),
                    _utc_text(occurred_at),
                    encoded_result,
                    str(contract_id),
                    contract_revision,
                ),
            )
        return True

    def append_execution_audit(
        self,
        contract_id: UUID,
        contract_revision: int,
        *,
        event_type: str,
        metadata: Mapping[str, Any],
        recorded_at: datetime,
    ) -> None:
        if not event_type:
            raise ValueError("event_type must not be empty")
        with self._transaction() as connection:
            self._journal_row(connection, contract_id, contract_revision)
            connection.execute(
                """
                INSERT INTO execution_audit (
                    contract_id, contract_revision, recorded_at, event_type, metadata_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(contract_id),
                    contract_revision,
                    _utc_text(recorded_at),
                    event_type,
                    _result_json(metadata),
                ),
            )

    def inspect_inbox(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM inbox ORDER BY received_at, message_id")

    def inspect_outbox(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM outbox ORDER BY created_at, message_id")

    def inspect_execution_journal(self) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM execution_journal ORDER BY contract_id, contract_revision"
        )

    def inbox_messages(self) -> list[MessageEnvelope]:
        """Return validated inbox envelopes for recovery and read-model reconstruction."""

        rows = self._connection.execute(
            "SELECT message_id, payload_json FROM inbox ORDER BY received_at, message_id"
        ).fetchall()
        return [
            self._decode_envelope("inbox", row["message_id"], row["payload_json"])
            for row in rows
        ]

    def outbox_messages(self) -> list[MessageEnvelope]:
        """Return validated outbox envelopes, including acknowledged records."""

        rows = self._connection.execute(
            "SELECT message_id, payload_json FROM outbox ORDER BY created_at, message_id"
        ).fetchall()
        return [
            self._decode_envelope("outbox", row["message_id"], row["payload_json"])
            for row in rows
        ]

    def causal_history(self, correlation_id: UUID) -> list[dict[str, Any]]:
        rows = self._connection.execute(
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
        return [dict(row) for row in rows]

    def contract_history(
        self, contract_id: UUID, contract_revision: int
    ) -> dict[str, Any]:
        journal = dict(self._journal_row(self._connection, contract_id, contract_revision))
        audit = self._connection.execute(
            """
            SELECT recorded_at, event_type, metadata_json FROM execution_audit
            WHERE contract_id = ? AND contract_revision = ? ORDER BY audit_id
            """,
            (str(contract_id), contract_revision),
        ).fetchall()
        return {"journal": journal, "audit": [dict(row) for row in audit]}

    def _rows(self, query: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connection.execute(query).fetchall()]

    def _decode_envelope(self, table: str, key: str, encoded: str) -> MessageEnvelope:
        try:
            return MessageEnvelope.model_validate_json(encoded)
        except (ValidationError, ValueError) as error:
            raise CorruptRecordError(f"invalid {table} record {key}") from error

    @staticmethod
    def _journal_row(
        connection: sqlite3.Connection, contract_id: UUID, contract_revision: int
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM execution_journal
            WHERE contract_id = ? AND contract_revision = ?
            """,
            (str(contract_id), contract_revision),
        ).fetchone()
        if row is None:
            raise KeyError(f"{contract_id}:{contract_revision}")
        return row
