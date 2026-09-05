"""Durable SQLite endpoint state for the M1 delayed-dummy runtime.

The store provides at-least-once message processing and an effect-once database
boundary for the dummy effect. It deliberately does not claim that SQLite can
make a future external physical action exactly-once.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from deferred_teleop.external_effect import (
    ExternalEffectObservation,
    ExternalOutcome,
    coerce_observation,
)
from deferred_teleop.protocol import ContractState, ExecutionEvent, MessageEnvelope

CURRENT_SCHEMA_VERSION = 4
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


class BudgetError(StorageError):
    """Base class for a durable external-action budget decision."""


class BudgetPolicyConflictError(BudgetError, RecordConflictError):
    """The restart policy differs from the immutable persisted snapshot."""


class BudgetScopeConflictError(BudgetError, RecordConflictError):
    """Another contract already owns the operation budget scope."""

    def __init__(
        self,
        message: str,
        *,
        operation_id: str | None = None,
        bound_contract_id: str | None = None,
        bound_contract_revision: int | None = None,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.bound_contract_id = bound_contract_id
        self.bound_contract_revision = bound_contract_revision


class BudgetDeadlineError(BudgetError):
    """The local service-clock budget window has elapsed before dispatch."""


class BudgetClockRollbackError(BudgetError):
    """The trusted service clock is behind a durable budget timestamp."""


class BudgetLimitError(BudgetError):
    """The durable one-attempt, one-action reservation is already consumed."""


LEGACY_OBSERVE_ONLY = "LEGACY_OBSERVE_ONLY"
LEGACY_UNBUDGETED_HOLD = "LEGACY_UNBUDGETED_HOLD"
BUDGET_SCOPE_CONFLICT = "BUDGET_SCOPE_CONFLICT"
BUDGET_POLICY_CONFLICT = "BUDGET_POLICY_CONFLICT"
BUDGET_DEADLINE_EXPIRED = "BUDGET_DEADLINE_EXPIRED"
BUDGET_CLOCK_ROLLBACK = "BUDGET_CLOCK_ROLLBACK"
BUDGET_LIMIT_EXHAUSTED = "BUDGET_LIMIT_EXHAUSTED"


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc_text(value: object, *, field_name: str) -> datetime:
    """Decode a persisted timestamp before making an external decision."""

    if not isinstance(value, str):
        raise CorruptRecordError(f"journal {field_name} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CorruptRecordError(f"journal {field_name} is not a timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CorruptRecordError(f"journal {field_name} must be timezone-aware")
    return parsed


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


def _validate_budget_policy(
    *,
    attempt_limit: int,
    action_limit: int,
    max_elapsed_seconds: float,
) -> float:
    """Validate the deliberately narrow local external-action policy."""

    if type(attempt_limit) is not int or attempt_limit != 1:
        raise ValueError("external autonomy attempt_limit must be the literal 1")
    if type(action_limit) is not int or action_limit != 1:
        raise ValueError("external autonomy action_limit must be the literal 1")
    if isinstance(max_elapsed_seconds, bool):
        raise ValueError("max_elapsed_seconds must be a finite positive float")
    try:
        value = float(max_elapsed_seconds)
    except (TypeError, ValueError) as error:
        raise ValueError("max_elapsed_seconds must be a finite positive float") from error
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("max_elapsed_seconds must be a finite positive float")
    return value


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


def _migration_3(connection: sqlite3.Connection) -> None:
    """Bind an optional external device to the durable dispatch boundary."""

    connection.execute(
        """
        ALTER TABLE execution_journal ADD COLUMN dispatch_device_id TEXT
            CHECK (dispatch_device_id IS NULL OR length(trim(dispatch_device_id)) > 0)
        """
    )


def _migration_4(connection: sqlite3.Connection) -> None:
    """Add the Robot-local budget and classify pre-budget journal records.

    Migration deliberately records only facts already present in the v3
    journal.  It never creates an authorization or a retrospective budget
    reservation for an old record.
    """

    connection.execute(
        """
        CREATE TABLE autonomy_budget (
            operation_id TEXT PRIMARY KEY,
            bound_contract_id TEXT NOT NULL,
            bound_contract_revision INTEGER NOT NULL CHECK (bound_contract_revision = 1),
            effect_key TEXT NOT NULL,
            attempt_limit INTEGER NOT NULL CHECK (attempt_limit = 1),
            action_limit INTEGER NOT NULL CHECK (action_limit = 1),
            max_elapsed_seconds REAL NOT NULL CHECK (
                max_elapsed_seconds > 0.0
                AND max_elapsed_seconds < 1.7976931348623157e308
            ),
            window_started_at TEXT NOT NULL,
            deadline_at TEXT NOT NULL,
            clock_high_water_at TEXT NOT NULL,
            attempts_reserved INTEGER NOT NULL CHECK (attempts_reserved IN (0, 1)),
            actions_reserved INTEGER NOT NULL CHECK (actions_reserved IN (0, 1)),
            dispatch_reserved_at TEXT,
            resolution TEXT,
            FOREIGN KEY (bound_contract_id, bound_contract_revision)
                REFERENCES execution_journal (contract_id, contract_revision)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE autonomy_budget_legacy (
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL,
            operation_id TEXT NOT NULL,
            classification TEXT NOT NULL CHECK (
                classification IN ('LEGACY_OBSERVE_ONLY', 'LEGACY_UNBUDGETED_HOLD')
            ),
            marked_at TEXT NOT NULL,
            PRIMARY KEY (contract_id, contract_revision),
            FOREIGN KEY (contract_id, contract_revision)
                REFERENCES execution_journal (contract_id, contract_revision)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE autonomy_budget_denial (
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL,
            operation_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            first_envelope_json TEXT NOT NULL,
            held_event_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (contract_id, contract_revision)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX autonomy_budget_operation_idx
            ON autonomy_budget (operation_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX autonomy_budget_legacy_operation_idx
            ON autonomy_budget_legacy (operation_id)
        """
    )

    marked_at = _now_text()
    connection.execute(
        """
        INSERT INTO autonomy_budget_legacy (
            contract_id, contract_revision, operation_id, classification, marked_at
        )
        SELECT contract_id, contract_revision, operation_id, ?, ?
        FROM execution_journal
        WHERE dispatch_recorded_at IS NOT NULL
          AND dispatch_device_id IS NOT NULL
        """,
        (LEGACY_OBSERVE_ONLY, marked_at),
    )
    connection.execute(
        """
        INSERT INTO autonomy_budget_legacy (
            contract_id, contract_revision, operation_id, classification, marked_at
        )
        SELECT journal.contract_id, journal.contract_revision, journal.operation_id, ?, ?
        FROM execution_journal AS journal
        WHERE journal.state = 'ACCEPTED'
          AND NOT EXISTS (
              SELECT 1 FROM autonomy_budget_legacy AS legacy
              WHERE legacy.contract_id = journal.contract_id
                AND legacy.contract_revision = journal.contract_revision
          )
        """,
        (LEGACY_UNBUDGETED_HOLD, marked_at),
    )


MIGRATIONS = {1: _migration_1, 2: _migration_2, 3: _migration_3, 4: _migration_4}


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

    def find_execution_journal(
        self, contract_id: UUID, contract_revision: int
    ) -> dict[str, Any] | None:
        """Return one journal row without changing durable state."""

        row = self._connection.execute(
            """
            SELECT * FROM execution_journal
            WHERE contract_id = ? AND contract_revision = ?
            """,
            (str(contract_id), contract_revision),
        ).fetchone()
        return dict(row) if row is not None else None

    def budget_legacy_classification(
        self, contract_id: UUID, contract_revision: int
    ) -> str | None:
        row = self._connection.execute(
            """
            SELECT classification FROM autonomy_budget_legacy
            WHERE contract_id = ? AND contract_revision = ?
            """,
            (str(contract_id), contract_revision),
        ).fetchone()
        return str(row["classification"]) if row is not None else None

    def find_autonomy_budget(
        self, contract_id: UUID, contract_revision: int
    ) -> dict[str, Any] | None:
        """Return the budget bound to one contract, without changing it."""

        row = self._connection.execute(
            """
            SELECT * FROM autonomy_budget
            WHERE bound_contract_id = ? AND bound_contract_revision = ?
            """,
            (str(contract_id), contract_revision),
        ).fetchone()
        return dict(row) if row is not None else None

    def admit_external_budget_contract(
        self,
        *,
        contract_id: UUID,
        contract_revision: int,
        operation_id: UUID,
        task_id: UUID,
        effect_key: str,
        accepted_at: datetime,
        max_elapsed_seconds: float,
        attempt_limit: int = 1,
        action_limit: int = 1,
    ) -> bool:
        """Admit one rev-1 external contract and snapshot its local policy.

        The operation scope check runs before the unique ``effect_key`` insert.
        A repeated immutable contract returns ``False``; a different contract
        for the operation raises :class:`BudgetScopeConflictError` without
        creating a second journal row.
        """

        policy_seconds = _validate_budget_policy(
            attempt_limit=attempt_limit,
            action_limit=action_limit,
            max_elapsed_seconds=max_elapsed_seconds,
        )
        if contract_revision != 1:
            raise ValueError("external autonomy budget admits contract revision 1 only")
        if not isinstance(effect_key, str) or not effect_key.strip():
            raise ValueError("effect_key must be a non-empty string")
        accepted_text = _utc_text(accepted_at)
        try:
            deadline_text = _utc_text(
                accepted_at + timedelta(seconds=policy_seconds)
            )
        except (OverflowError, ValueError) as error:
            raise ValueError("max_elapsed_seconds produces an invalid deadline") from error
        operation_text = str(operation_id)
        contract_text = str(contract_id)
        task_text = str(task_id)

        with self._transaction() as connection:
            budget = connection.execute(
                "SELECT * FROM autonomy_budget WHERE operation_id = ?",
                (operation_text,),
            ).fetchone()
            if budget is not None:
                if (
                    budget["bound_contract_id"] != contract_text
                    or int(budget["bound_contract_revision"]) != contract_revision
                ):
                    raise BudgetScopeConflictError(
                        f"operation_id already bound to contract {budget['bound_contract_id']}"
                        f":{budget['bound_contract_revision']}",
                        operation_id=operation_text,
                        bound_contract_id=str(budget["bound_contract_id"]),
                        bound_contract_revision=int(budget["bound_contract_revision"]),
                    )
                journal = self._journal_row(connection, contract_id, contract_revision)
                immutable = (operation_text, task_text, effect_key)
                if (
                    str(journal["operation_id"]),
                    str(journal["task_id"]),
                    str(journal["effect_key"]),
                ) != immutable:
                    raise RecordConflictError("contract revision collision")
                return False

            prior_denial = connection.execute(
                """
                SELECT operation_id FROM autonomy_budget_denial
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (contract_text, contract_revision),
            ).fetchone()
            if prior_denial is not None:
                if str(prior_denial["operation_id"]) != operation_text:
                    raise RecordConflictError("budget denial contract collision")
                raise BudgetScopeConflictError(
                    "contract was already denied for its operation scope",
                    operation_id=operation_text,
                )

            # The budget table is not the only possible historical owner.  A
            # v3 journal (or a non-external journal) can already claim this
            # operation; inspect it before touching the effect-key UNIQUE
            # constraint so the result remains an explicit scope conflict.
            operation_journal = connection.execute(
                """
                SELECT contract_id, contract_revision, operation_id, task_id, effect_key
                FROM execution_journal WHERE operation_id = ?
                ORDER BY contract_revision, contract_id LIMIT 1
                """,
                (operation_text,),
            ).fetchone()
            if operation_journal is not None:
                if (
                    operation_journal["contract_id"] != contract_text
                    or int(operation_journal["contract_revision"]) != contract_revision
                ):
                    raise BudgetScopeConflictError(
                        "operation_id is already owned by another contract",
                        operation_id=operation_text,
                        bound_contract_id=str(operation_journal["contract_id"]),
                        bound_contract_revision=int(operation_journal["contract_revision"]),
                    )
                immutable = (operation_text, task_text, effect_key)
                if (
                    str(operation_journal["operation_id"]),
                    str(operation_journal["task_id"]),
                    str(operation_journal["effect_key"]),
                ) != immutable:
                    raise RecordConflictError("contract revision collision")
                # A journal without a budget is a historical/corrupt state.
                # Migration marks all legitimate v3 cases, so never invent a
                # fresh authorization here.
                raise RecordConflictError(
                    "external contract journal has no durable autonomy budget"
                )

            # This final check handles a same contract identifier whose
            # operation differs, while still preserving the operation-scope
            # ordering above.
            existing_contract = connection.execute(
                """
                SELECT operation_id, task_id, effect_key FROM execution_journal
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (contract_text, contract_revision),
            ).fetchone()
            if existing_contract is not None:
                if (
                    str(existing_contract["operation_id"]),
                    str(existing_contract["task_id"]),
                    str(existing_contract["effect_key"]),
                ) != (operation_text, task_text, effect_key):
                    raise RecordConflictError("contract revision collision")
                raise RecordConflictError(
                    "external contract journal has no durable autonomy budget"
                )

            try:
                connection.execute(
                    """
                    INSERT INTO execution_journal (
                        contract_id, contract_revision, operation_id, task_id, state,
                        effect_key, accepted_at
                    ) VALUES (?, ?, ?, ?, 'ACCEPTED', ?, ?)
                    """,
                    (
                        contract_text,
                        contract_revision,
                        operation_text,
                        task_text,
                        effect_key,
                        accepted_text,
                    ),
                )
            except sqlite3.IntegrityError as error:
                # A concurrent/previous effect-key claim is still surfaced as
                # a record conflict.  Operation scope was checked first.
                raise RecordConflictError(f"effect_key already claimed: {effect_key}") from error
            connection.execute(
                """
                INSERT INTO autonomy_budget (
                    operation_id, bound_contract_id, bound_contract_revision, effect_key,
                    attempt_limit, action_limit, max_elapsed_seconds,
                    window_started_at, deadline_at, clock_high_water_at,
                    attempts_reserved, actions_reserved, dispatch_reserved_at, resolution
                ) VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?, ?, 0, 0, NULL, NULL)
                """,
                (
                    operation_text,
                    contract_text,
                    contract_revision,
                    effect_key,
                    policy_seconds,
                    accepted_text,
                    deadline_text,
                    accepted_text,
                ),
            )
        return True

    def reserve_external_dispatch_with_budget(
        self,
        contract_id: UUID,
        contract_revision: int,
        *,
        recorded_at: datetime,
        device_id: str,
        max_elapsed_seconds: float,
        attempt_limit: int = 1,
        action_limit: int = 1,
    ) -> bool:
        """Reserve the sole external action and record dispatch atomically.

        The SQLite transaction commits the reservation, device identity, and
        ``DISPATCH_RECORDED`` boundary together.  The caller may invoke the
        physical adapter only after this method returns ``True``.
        """

        policy_seconds = _validate_budget_policy(
            attempt_limit=attempt_limit,
            action_limit=action_limit,
            max_elapsed_seconds=max_elapsed_seconds,
        )
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id must be a non-empty string")
        recorded_text = _utc_text(recorded_at)

        with self._transaction() as connection:
            journal = self._journal_row(connection, contract_id, contract_revision)
            if journal["dispatch_recorded_at"] is not None:
                stored_device_id = journal["dispatch_device_id"]
                if stored_device_id != device_id:
                    raise RecordConflictError(
                        "dispatch device identity is immutable and does not match"
                    )
                return False
            if journal["state"] != ContractState.ACCEPTED.value:
                if journal["state"] in TERMINAL_STATES:
                    return False
                raise InvalidStateTransitionError(
                    "external budget reservation requires ACCEPTED state"
                )
            budget = connection.execute(
                "SELECT * FROM autonomy_budget WHERE operation_id = ?",
                (str(journal["operation_id"]),),
            ).fetchone()
            if budget is None:
                raise RecordConflictError(
                    "external contract has no durable autonomy budget"
                )
            if (
                budget["bound_contract_id"] != str(contract_id)
                or int(budget["bound_contract_revision"]) != contract_revision
                or budget["effect_key"] != journal["effect_key"]
            ):
                raise BudgetScopeConflictError(
                    "autonomy budget binding does not match contract",
                    operation_id=str(journal["operation_id"]),
                    bound_contract_id=str(budget["bound_contract_id"]),
                    bound_contract_revision=int(budget["bound_contract_revision"]),
                )
            if (
                int(budget["attempt_limit"]) != attempt_limit
                or int(budget["action_limit"]) != action_limit
                or float(budget["max_elapsed_seconds"]) != policy_seconds
            ):
                raise BudgetPolicyConflictError(
                    BUDGET_POLICY_CONFLICT
                    + ": configured external autonomy policy differs from durable snapshot"
                )
            if int(budget["attempts_reserved"]) != 0 or int(budget["actions_reserved"]) != 0:
                raise BudgetLimitError(BUDGET_LIMIT_EXHAUSTED)

            accepted_at = _parse_utc_text(journal["accepted_at"], field_name="accepted_at")
            high_water_at = _parse_utc_text(
                budget["clock_high_water_at"], field_name="clock_high_water_at"
            )
            deadline_at = _parse_utc_text(
                budget["deadline_at"], field_name="deadline_at"
            )
            if recorded_at < accepted_at or recorded_at < high_water_at:
                raise BudgetClockRollbackError(
                    BUDGET_CLOCK_ROLLBACK
                    + ": trusted service clock is earlier than durable budget time"
                )
            if recorded_at >= deadline_at:
                raise BudgetDeadlineError(
                    BUDGET_DEADLINE_EXPIRED
                    + ": service-clock budget window has elapsed"
                )

            connection.execute(
                """
                UPDATE autonomy_budget
                SET attempts_reserved = 1, actions_reserved = 1,
                    dispatch_reserved_at = ?, clock_high_water_at = ?
                WHERE operation_id = ?
                """,
                (recorded_text, recorded_text, str(journal["operation_id"])),
            )
            connection.execute(
                """
                UPDATE execution_journal
                SET state = 'DISPATCH_RECORDED', dispatch_recorded_at = ?,
                    dispatch_device_id = ?
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (
                    recorded_text,
                    device_id,
                    str(contract_id),
                    contract_revision,
                ),
            )
        return True

    def budget_denial_event(
        self, contract_id: UUID, contract_revision: int
    ) -> MessageEnvelope | None:
        """Return the exact durable HELD event for a budget denial, if any."""

        row = self._connection.execute(
            """
            SELECT held_event_json FROM autonomy_budget_denial
            WHERE contract_id = ? AND contract_revision = ?
            """,
            (str(contract_id), contract_revision),
        ).fetchone()
        if row is None:
            return None
        return self._decode_envelope(
            "autonomy_budget_denial", f"{contract_id}:{contract_revision}", row["held_event_json"]
        )

    def complete_budget_scope_denial(
        self,
        contract_id: UUID,
        contract_revision: int,
        *,
        operation_id: UUID,
        reason: str,
        first_envelope: MessageEnvelope,
        held_event: MessageEnvelope,
        inbox_message_id: UUID,
        processed_at: datetime,
    ) -> bool:
        """Persist a pre-dispatch denial, its stable HELD event, and inbox completion.

        The method also serves the legacy ``ACCEPTED`` migration hold.  It is
        intentionally named for the scope-denial call site because the scope
        conflict must be handled before a second journal insert.  Existing
        denial rows always reuse their original canonical event bytes.
        """

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("budget denial reason must not be empty")
        if not isinstance(first_envelope, MessageEnvelope):
            raise ValueError("first_envelope must be a MessageEnvelope")
        if first_envelope.message_id != inbox_message_id:
            raise RecordConflictError(
                "budget denial first envelope must be the claimed inbox message"
            )
        first_payload = first_envelope.payload
        if (
            not hasattr(first_payload, "contract_id")
            or first_payload.contract_id != contract_id
            or getattr(first_payload, "contract_revision", None) != contract_revision
            or getattr(first_payload, "operation_id", None) != operation_id
        ):
            raise RecordConflictError(
                "budget denial first envelope does not match contract or operation"
            )
        if not isinstance(held_event, MessageEnvelope):
            raise ValueError("held_event must be a MessageEnvelope")
        event = held_event.payload
        if not isinstance(event, ExecutionEvent):
            raise ValueError("held_event must contain an ExecutionEvent")
        if (
            event.contract_id != contract_id
            or event.contract_revision != contract_revision
            or event.next_state is not ContractState.HELD
            or event.previous_state not in {ContractState.RECEIVED, ContractState.ACCEPTED}
        ):
            raise RecordConflictError("budget denial event does not match pre-dispatch hold")
        operation_text = str(operation_id)
        contract_text = str(contract_id)
        first_encoded = _envelope_json(first_envelope)
        candidate_encoded = _envelope_json(held_event)
        event_occurred_text = _utc_text(event.occurred_at)
        processed_text = _utc_text(processed_at)

        with self._transaction() as connection:
            inbox = connection.execute(
                "SELECT processing_state FROM inbox WHERE message_id = ?",
                (str(inbox_message_id),),
            ).fetchone()
            if inbox is None or inbox["processing_state"] != "PROCESSING":
                raise InvalidStateTransitionError("only PROCESSING inbox messages can complete")

            journal = connection.execute(
                """
                SELECT * FROM execution_journal
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (contract_text, contract_revision),
            ).fetchone()
            if journal is not None:
                if str(journal["operation_id"]) != operation_text:
                    raise RecordConflictError("budget denial operation does not match journal")
                if journal["dispatch_recorded_at"] is not None:
                    raise InvalidStateTransitionError(
                        "budget denial cannot follow durable dispatch"
                    )
                if journal["state"] not in {
                    ContractState.ACCEPTED.value,
                    ContractState.HELD.value,
                }:
                    raise InvalidStateTransitionError(
                        "budget denial requires ACCEPTED or HELD journal state"
                    )
                if (
                    journal["state"] == ContractState.ACCEPTED.value
                    and event.previous_state is not ContractState.ACCEPTED
                ):
                    raise InvalidStateTransitionError(
                        "an ACCEPTED journal requires an ACCEPTED -> HELD denial"
                    )
                accepted_at = _parse_utc_text(
                    journal["accepted_at"], field_name="accepted_at"
                )
                if event.occurred_at < accepted_at:
                    raise BudgetClockRollbackError(
                        BUDGET_CLOCK_ROLLBACK
                        + ": denial timestamp is earlier than durable acceptance"
                    )
            if processed_at < event.occurred_at:
                raise BudgetClockRollbackError(
                    BUDGET_CLOCK_ROLLBACK
                    + ": inbox completion precedes denial timestamp"
                )

            existing = connection.execute(
                """
                SELECT operation_id, reason, first_envelope_json, held_event_json
                FROM autonomy_budget_denial
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (contract_text, contract_revision),
            ).fetchone()
            if existing is None:
                denial_first_encoded = first_encoded
                denial_event_encoded = candidate_encoded
                connection.execute(
                    """
                    INSERT INTO autonomy_budget_denial (
                        contract_id, contract_revision, operation_id, reason,
                        first_envelope_json, held_event_json, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract_text,
                        contract_revision,
                        operation_text,
                        reason,
                        denial_first_encoded,
                        denial_event_encoded,
                        processed_text,
                    ),
                )
            else:
                if str(existing["operation_id"]) != operation_text:
                    raise RecordConflictError("budget denial operation collision")
                if str(existing["reason"]) != reason:
                    raise RecordConflictError("budget denial reason is immutable")
                denial_first_encoded = str(existing["first_envelope_json"])
                denial_event_encoded = str(existing["held_event_json"])
                # Decode immutable evidence before writing any consequence.
                durable_first = self._decode_envelope(
                    "autonomy_budget_denial",
                    f"{contract_text}:{contract_revision}:first",
                    denial_first_encoded,
                )
                if (
                    durable_first.message_type != first_envelope.message_type
                    or durable_first.payload.model_dump(mode="json")
                    != first_envelope.payload.model_dump(mode="json")
                ):
                    raise RecordConflictError(
                        "budget denial first envelope payload is immutable"
                    )
                durable_event = self._decode_envelope(
                    "autonomy_budget_denial",
                    f"{contract_text}:{contract_revision}:held",
                    denial_event_encoded,
                )
                durable_payload = durable_event.payload
                if not isinstance(durable_payload, ExecutionEvent):
                    raise CorruptRecordError(
                        f"invalid autonomy budget denial event {contract_text}:{contract_revision}"
                    )

            if journal is not None and journal["state"] == ContractState.ACCEPTED.value:
                connection.execute(
                    """
                    UPDATE execution_journal
                    SET state = 'HELD', terminal_at = ?, terminal_result_json = ?
                    WHERE contract_id = ? AND contract_revision = ?
                    """,
                    (
                        event_occurred_text,
                        _result_json({"budget_denial": reason}),
                        contract_text,
                        contract_revision,
                    ),
                )
            budget = connection.execute(
                """
                SELECT operation_id, clock_high_water_at FROM autonomy_budget
                WHERE operation_id = ? AND bound_contract_id = ?
                  AND bound_contract_revision = ?
                """,
                (operation_text, contract_text, contract_revision),
            ).fetchone()
            if budget is not None:
                current_high_water = _parse_utc_text(
                    budget["clock_high_water_at"], field_name="clock_high_water_at"
                )
                high_water_text = (
                    event_occurred_text
                    if event.occurred_at >= current_high_water
                    else _utc_text(current_high_water)
                )
                connection.execute(
                    """
                    UPDATE autonomy_budget SET resolution = ?, clock_high_water_at = ?
                    WHERE operation_id = ? AND bound_contract_id = ?
                      AND bound_contract_revision = ?
                    """,
                    (
                        reason,
                        high_water_text,
                        operation_text,
                        contract_text,
                        contract_revision,
                    ),
                )

            durable_event = self._decode_envelope(
                "autonomy_budget_denial",
                f"{contract_text}:{contract_revision}:held",
                denial_event_encoded,
            )
            self._insert_outbox(connection, durable_event)
            connection.execute(
                """
                UPDATE inbox SET processing_state = 'PROCESSED', processed_at = ?,
                    handler_result_reference = ?, error_json = NULL
                WHERE message_id = ?
                """,
                (
                    processed_text,
                    f"held:{reason}:{contract_text}",
                    str(inbox_message_id),
                ),
            )
        return existing is None

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
        self,
        contract_id: UUID,
        contract_revision: int,
        *,
        recorded_at: datetime,
        device_id: str | None = None,
    ) -> bool:
        if device_id is not None and (
            not isinstance(device_id, str) or not device_id.strip()
        ):
            raise ValueError("device_id must be a non-empty string when provided")
        with self._transaction() as connection:
            row = self._journal_row(connection, contract_id, contract_revision)
            if row["dispatch_recorded_at"] is not None:
                stored_device_id = row["dispatch_device_id"]
                if device_id is not None and stored_device_id != device_id:
                    raise RecordConflictError(
                        "dispatch device identity is immutable and does not match"
                    )
                return False
            if row["state"] != "ACCEPTED":
                raise InvalidStateTransitionError("dispatch requires ACCEPTED state")
            connection.execute(
                """
                UPDATE execution_journal
                SET state = 'DISPATCH_RECORDED', dispatch_recorded_at = ?,
                    dispatch_device_id = ?
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (
                    _utc_text(recorded_at),
                    device_id,
                    str(contract_id),
                    contract_revision,
                ),
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

    def resolve_external_outcome(
        self,
        contract_id: UUID,
        contract_revision: int,
        *,
        observation: ExternalEffectObservation | Mapping[str, Any] | object | None = None,
        expected_device_id: str | None = None,
        terminal_state: ContractState | None = None,
        terminal_result: Mapping[str, Any] | None = None,
        occurred_at: datetime,
        terminal_event: MessageEnvelope,
    ) -> bool:
        """Atomically resolve an effect performed outside the Robot journal.

        The external path deliberately leaves ``effect_count`` at zero.  The
        adapter owns the physical/test effect record, while this transaction
        owns only the immutable contract resolution and its terminal outbox
        event.  A proof must identify both the semantic ``effect_key`` and the
        concrete ``device_id``; a bare status or boolean is rejected.

        When ``terminal_state`` is omitted it is derived from the proof:
        APPLIED resolves to SUCCEEDED and either other observation resolves to
        HELD.
        """

        if observation is None:
            raise ValueError("external observation proof is required")

        row = self._journal_row(self._connection, contract_id, contract_revision)
        expected_effect_key = str(row["effect_key"])
        occurred_text = _utc_text(occurred_at)
        dispatch_recorded_at = row["dispatch_recorded_at"]
        if dispatch_recorded_at is not None:
            dispatch_at = _parse_utc_text(
                dispatch_recorded_at, field_name="dispatch_recorded_at"
            )
            if occurred_at < dispatch_at:
                raise RecordConflictError(
                    "external terminal resolution cannot precede durable dispatch_recorded_at"
                )
        proof = coerce_observation(
            observation,
            expected_effect_key=expected_effect_key,
            expected_device_id=expected_device_id,
        )
        if proof.observed_at > occurred_at:
            raise RecordConflictError(
                "external observation cannot be later than its terminal resolution"
            )
        resolved_state = (
            ContractState.SUCCEEDED
            if proof.outcome is ExternalOutcome.APPLIED
            else ContractState.HELD
        )
        if terminal_state is not None:
            try:
                terminal_state = ContractState(terminal_state)
            except (TypeError, ValueError) as error:
                raise ValueError("terminal_state must be a ContractState") from error
            if terminal_state is not resolved_state:
                raise RecordConflictError("terminal state does not match external observation")
        terminal_state = resolved_state

        event = terminal_event.payload
        if not isinstance(event, ExecutionEvent):
            raise ValueError("terminal_event must contain an ExecutionEvent")
        if (
            event.contract_id != contract_id
            or event.contract_revision != contract_revision
            or event.previous_state is not ContractState.RUNNING
            or event.next_state is not terminal_state
            or event.occurred_at != occurred_at
            or terminal_event.created_at != occurred_at
        ):
            raise RecordConflictError(
                "terminal event or timestamp does not match external resolution"
            )

        resolution = {
            ExternalOutcome.APPLIED: "APPLIED",
            ExternalOutcome.UNKNOWN: "OUTCOME_UNKNOWN",
            ExternalOutcome.NOT_APPLIED: "NOT_APPLIED_AFTER_UNCERTAIN_DISPATCH",
        }[proof.outcome]
        result: dict[str, Any] = dict(terminal_result or {})
        required_result = {
            "effect_key": proof.effect_key,
            "device_id": proof.device_id,
            "external_outcome": proof.outcome.value,
            "outcome": resolution,
            "resolution": resolution,
            "observation_id": proof.observation_id,
            "observed_at": proof.observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "terminal_at": occurred_text,
            "proof": proof.model_dump(),
        }
        for key, value in required_result.items():
            if key in result and result[key] != value:
                raise RecordConflictError(f"external terminal result field collision: {key}")
            result[key] = value
        encoded_result = _result_json(result)

        with self._transaction() as connection:
            journal = self._journal_row(connection, contract_id, contract_revision)
            dispatch_device_id = journal["dispatch_device_id"]
            if journal["terminal_at"] is not None:
                if dispatch_device_id is None:
                    raise RecordConflictError(
                        "external resolution has no durable dispatch device identity"
                    )
                if dispatch_device_id != proof.device_id:
                    raise RecordConflictError(
                        "external observation device differs from durable dispatch identity"
                    )
                existing_raw = journal["terminal_result_json"]
                try:
                    existing = json.loads(existing_raw) if existing_raw is not None else {}
                except (TypeError, json.JSONDecodeError) as error:
                    raise CorruptRecordError(
                        f"invalid external terminal result {contract_id}:{contract_revision}"
                    ) from error
                # Compare the complete canonical record.  Python's mapping
                # equality would treat values such as 1 and 1.0 as equal and
                # could silently accept a changed immutable proof detail.
                if _canonical_json(existing) == encoded_result:
                    return False
                raise RecordConflictError("external terminal result is immutable")
            if journal["state"] != "DISPATCH_RECORDED" or journal["effect_count"] != 0:
                raise InvalidStateTransitionError(
                    "external outcome requires an unconsumed DISPATCH_RECORDED journal entry"
                )
            if dispatch_device_id is None:
                raise RecordConflictError(
                    "external resolution has no durable dispatch device identity"
                )
            if dispatch_device_id != proof.device_id:
                raise RecordConflictError(
                    "external observation device differs from durable dispatch identity"
                )
            self._insert_outbox(connection, terminal_event)
            connection.execute(
                """
                UPDATE execution_journal SET state = ?, terminal_at = ?,
                    terminal_result_json = ?
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (
                    terminal_state.value,
                    occurred_text,
                    encoded_result,
                    str(contract_id),
                    contract_revision,
                ),
            )
            budget = connection.execute(
                """
                SELECT clock_high_water_at FROM autonomy_budget
                WHERE operation_id = ? AND bound_contract_id = ?
                  AND bound_contract_revision = ?
                """,
                (str(journal["operation_id"]), str(contract_id), contract_revision),
            ).fetchone()
            if budget is not None:
                current_high_water = _parse_utc_text(
                    budget["clock_high_water_at"], field_name="clock_high_water_at"
                )
                high_water_text = (
                    occurred_text
                    if occurred_at >= current_high_water
                    else _utc_text(current_high_water)
                )
                connection.execute(
                    """
                    UPDATE autonomy_budget SET resolution = ?, clock_high_water_at = ?
                    WHERE operation_id = ? AND bound_contract_id = ?
                      AND bound_contract_revision = ?
                    """,
                    (
                        resolution,
                        high_water_text,
                        str(journal["operation_id"]),
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

    def inspect_autonomy_budget(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM autonomy_budget ORDER BY operation_id")

    def inspect_autonomy_budget_legacy(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT * FROM autonomy_budget_legacy
            ORDER BY operation_id, contract_id, contract_revision
            """
        )

    def inspect_autonomy_budget_denials(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT * FROM autonomy_budget_denial
            ORDER BY recorded_at, contract_id, contract_revision
            """
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
