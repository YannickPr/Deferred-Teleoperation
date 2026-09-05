"""Durable SQLite endpoint state for the M1 delayed-dummy runtime.

The store provides at-least-once message processing and an effect-once database
boundary for the dummy effect. It deliberately does not claim that SQLite can
make a future external physical action exactly-once.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
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
from deferred_teleop.protocol import (
    ContractState,
    ExecutionEvent,
    LocalTwoButtonDecision,
    M3aSpatialExecutionContext,
    MessageEnvelope,
    SpatialPressCommand,
    TwoButtonEffectEvidence,
    TwoButtonLevelEvidence,
)

CURRENT_SCHEMA_VERSION = 9
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


class BusyError(StorageError):
    """The local Robot database is currently owned by another service."""


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


def _m3a_payload_json(payload: object) -> str:
    model_dump = getattr(payload, "model_dump", None)
    if not callable(model_dump):
        raise ValueError("M3a payload must expose model_dump")
    return json.dumps(
        model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _m3a_payload_digest(payload_json: str) -> str:
    return "sha256:" + hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


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


def _validate_command_digest(command_digest: str | None) -> str | None:
    if command_digest is None:
        return None
    if (
        not isinstance(command_digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", command_digest) is None
    ):
        raise ValueError("command_digest must be sha256:<64 lowercase hexadecimal digits>")
    return command_digest


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


def _migration_5(connection: sqlite3.Connection) -> None:
    """Bind an M3a canonical spatial command to its external budget."""

    connection.execute(
        """
        ALTER TABLE autonomy_budget ADD COLUMN command_digest TEXT
            CHECK (command_digest IS NULL OR length(trim(command_digest)) > 0)
        """
    )
    connection.execute(
        """
        CREATE TABLE m3a_intent_binding (
            operation_id TEXT NOT NULL,
            intent_revision INTEGER NOT NULL CHECK (intent_revision = 1),
            canonical_intent_digest TEXT NOT NULL,
            source_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            semantic_json TEXT NOT NULL,
            bound_at TEXT NOT NULL,
            PRIMARY KEY (operation_id, intent_revision)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE m3a_intent_conflict (
            conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT NOT NULL,
            intent_revision INTEGER NOT NULL,
            canonical_intent_digest TEXT NOT NULL,
            source_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            semantic_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE m3a_decision (
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL CHECK (contract_revision = 1),
            operation_id TEXT NOT NULL,
            decision_envelope_json TEXT NOT NULL,
            level_envelope_json TEXT NOT NULL,
            held_event_json TEXT,
            business_result TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (contract_id, contract_revision)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX m3a_intent_conflict_operation_idx
            ON m3a_intent_conflict (operation_id, intent_revision, recorded_at)
        """
    )


def _migration_6(connection: sqlite3.Connection) -> None:
    """Retain the canonical execute command beside its immutable decision."""

    connection.execute(
        """
        ALTER TABLE m3a_decision ADD COLUMN command_envelope_json TEXT
        """
    )


def _migration_7(connection: sqlite3.Connection) -> None:
    """Bind one canonical execution context before Robot policy admission."""

    connection.execute(
        """
        CREATE TABLE m3a_context_binding (
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL CHECK (contract_revision = 1),
            operation_id TEXT NOT NULL,
            intent_revision INTEGER NOT NULL CHECK (intent_revision = 1),
            task_id TEXT NOT NULL,
            context_digest TEXT NOT NULL,
            context_json TEXT NOT NULL,
            source_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            bound_at TEXT NOT NULL,
            PRIMARY KEY (contract_id, contract_revision)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE m3a_context_conflict (
            conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL,
            operation_id TEXT NOT NULL,
            context_digest TEXT NOT NULL,
            source_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            context_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX m3a_context_conflict_contract_idx
            ON m3a_context_conflict (contract_id, contract_revision, recorded_at)
        """
    )


def _migration_8(connection: sqlite3.Connection) -> None:
    """Bind the first attributable M3a effect proof at each service boundary."""

    connection.execute(
        """
        CREATE TABLE m3a_effect_binding (
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL CHECK (contract_revision = 1),
            operation_id TEXT NOT NULL,
            intent_revision INTEGER NOT NULL CHECK (intent_revision = 1),
            effect_digest TEXT NOT NULL,
            effect_json TEXT NOT NULL,
            effect_envelope_json TEXT NOT NULL,
            source_id TEXT NOT NULL,
            destination_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            bound_at TEXT NOT NULL,
            PRIMARY KEY (contract_id, contract_revision)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE m3a_effect_conflict (
            conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL,
            operation_id TEXT NOT NULL,
            intent_revision INTEGER NOT NULL,
            effect_digest TEXT NOT NULL,
            effect_json TEXT NOT NULL,
            effect_envelope_json TEXT NOT NULL,
            source_id TEXT NOT NULL,
            destination_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX m3a_effect_conflict_contract_idx
            ON m3a_effect_conflict (contract_id, contract_revision, recorded_at)
        """
    )


def _migration_9(connection: sqlite3.Connection) -> None:
    """Retain one unverified UNKNOWN diagnostic without making it attributable."""

    connection.execute(
        """
        CREATE TABLE m3a_effect_diagnostic (
            contract_id TEXT NOT NULL,
            contract_revision INTEGER NOT NULL CHECK (contract_revision = 1),
            operation_id TEXT NOT NULL,
            intent_revision INTEGER NOT NULL CHECK (intent_revision = 1),
            effect_digest TEXT NOT NULL,
            effect_json TEXT NOT NULL,
            effect_envelope_json TEXT NOT NULL,
            source_id TEXT NOT NULL,
            destination_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (contract_id, contract_revision)
        )
        """
    )


MIGRATIONS = {
    1: _migration_1,
    2: _migration_2,
    3: _migration_3,
    4: _migration_4,
    5: _migration_5,
    6: _migration_6,
    7: _migration_7,
    8: _migration_8,
    9: _migration_9,
}


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
        # Resolve before opening SQLite or deriving the sidecar path.  Two
        # relative/symlinked spellings of one database must use one lock file.
        self.path = Path(path).resolve(strict=False)
        self._robot_owner_lock_path = Path(f"{self.path}.robot-owner.lock")
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
    def exclusive_robot_owner(self) -> Iterator[None]:
        """Hold the process-lifetime Robot owner lock for one service action.

        The lock is deliberately a sidecar rather than SQLite state: it spans
        the complete ``handle``/``recover`` call, including external adapter
        I/O and the terminal commit.  The sidecar is retained after release so
        that all path aliases continue to address the same inode.
        """

        lock_path = self._robot_owner_lock_path
        with lock_path.open("a+b") as lock_file:
            acquired = False
            try:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    try:
                        # ``msvcrt.locking`` may lock beyond EOF; no marker byte
                        # is written because the sidecar is only a lock inode.
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as error:
                        if error.errno in {
                            errno.EACCES,
                            errno.EAGAIN,
                            errno.EDEADLK,
                        }:
                            raise BusyError(
                                f"Robot database ownership is busy: {lock_path}"
                            ) from error
                        raise
                else:
                    import fcntl

                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as error:
                        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                            raise BusyError(
                                f"Robot database ownership is busy: {lock_path}"
                            ) from error
                        raise
                acquired = True
                yield
            finally:
                if acquired:
                    if os.name == "nt":
                        import msvcrt

                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

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

    def _insert_outbox(self, connection: sqlite3.Connection, envelope: MessageEnvelope) -> bool:
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
            self._decode_envelope("outbox", row["message_id"], row["payload_json"]) for row in rows
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

    def budget_legacy_classification(self, contract_id: UUID, contract_revision: int) -> str | None:
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

    def find_m3a_intent_binding(
        self, operation_id: UUID, intent_revision: int = 1
    ) -> dict[str, Any] | None:
        """Return the immutable M3a root binding, if Field has created one."""

        row = self._connection.execute(
            """
            SELECT * FROM m3a_intent_binding
            WHERE operation_id = ? AND intent_revision = ?
            """,
            (str(operation_id), intent_revision),
        ).fetchone()
        return dict(row) if row is not None else None

    def bind_m3a_intent(
        self,
        *,
        operation_id: UUID,
        intent_revision: int,
        canonical_intent_digest: str,
        source_id: str,
        correlation_id: UUID,
        semantic_fields: Mapping[str, Any],
        bound_at: datetime,
    ) -> bool:
        """Atomically bind an operation/revision before Field creates a bundle.

        ``False`` denotes an exact fresh duplicate.  Any changed claim, root
        source, or correlation is durable as ``M3A_INTENT_CONFLICT`` and raises
        before an operation plan, assignment, or contract can be emitted.
        """

        if intent_revision != 1:
            raise ValueError("M3a intent binding supports revision 1 only")
        canonical_intent_digest = _validate_command_digest(canonical_intent_digest)
        assert canonical_intent_digest is not None
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id must not be empty")
        semantic_json = _result_json(semantic_fields)
        operation_text = str(operation_id)
        correlation_text = str(correlation_id)
        conflict = False
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM m3a_intent_binding
                WHERE operation_id = ? AND intent_revision = ?
                """,
                (operation_text, intent_revision),
            ).fetchone()
            if existing is not None:
                exact = (
                    existing["canonical_intent_digest"] == canonical_intent_digest
                    and existing["source_id"] == source_id
                    and existing["correlation_id"] == correlation_text
                    and existing["semantic_json"] == semantic_json
                )
                if exact:
                    return False
                connection.execute(
                    """
                    INSERT INTO m3a_intent_conflict (
                        operation_id, intent_revision, canonical_intent_digest,
                        source_id, correlation_id, reason, semantic_json, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation_text,
                        intent_revision,
                        canonical_intent_digest,
                        source_id,
                        correlation_text,
                        "M3A_INTENT_CONFLICT",
                        semantic_json,
                        _utc_text(bound_at),
                    ),
                )
                # Do not raise inside ``_transaction``: its rollback is
                # correct for ordinary handler failures, but would erase the
                # durable conflict evidence required by the M3a oracle.
                conflict = True
            if not conflict:
                connection.execute(
                    """
                    INSERT INTO m3a_intent_binding (
                        operation_id, intent_revision, canonical_intent_digest,
                        source_id, correlation_id, semantic_json, bound_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation_text,
                        intent_revision,
                        canonical_intent_digest,
                        source_id,
                        correlation_text,
                        semantic_json,
                        _utc_text(bound_at),
                    ),
                )
        if conflict:
            raise RecordConflictError("M3A_INTENT_CONFLICT")
        return True

    def record_m3a_decision(
        self,
        *,
        contract_id: UUID,
        contract_revision: int,
        operation_id: UUID,
        decision_envelope: MessageEnvelope,
        level_envelope: MessageEnvelope,
        held_event: MessageEnvelope | None,
        business_result: str,
        recorded_at: datetime,
        command_envelope: MessageEnvelope | None = None,
        outgoing: Sequence[MessageEnvelope] = (),
    ) -> bool:
        """Persist one immutable Robot decision and its exact consequences.

        Execute decisions pass the canonical command envelope and all three
        M3a evidence envelopes in ``outgoing``.  They are committed in the
        same SQLite transaction as the decision, so a crash cannot leave a
        durable dispatch without the command needed for exact replay.
        """

        if contract_revision != 1:
            raise ValueError("M3a decision storage supports contract revision 1 only")
        if not isinstance(decision_envelope, MessageEnvelope):
            raise ValueError("decision_envelope is required")
        if not isinstance(level_envelope, MessageEnvelope):
            raise ValueError("level_envelope is required")
        if held_event is not None and not isinstance(held_event.payload, ExecutionEvent):
            raise ValueError("held_event must contain ExecutionEvent")
        if command_envelope is not None and not isinstance(
            command_envelope.payload, SpatialPressCommand
        ):
            raise ValueError("command_envelope must contain SpatialPressCommand")
        if any(not isinstance(value, MessageEnvelope) for value in outgoing):
            raise ValueError("outgoing must contain MessageEnvelope values")
        if not isinstance(business_result, str) or not business_result.strip():
            raise ValueError("business_result must not be empty")
        decision_json = _envelope_json(decision_envelope)
        level_json = _envelope_json(level_envelope)
        held_json = _envelope_json(held_event) if held_event is not None else None
        command_json = _envelope_json(command_envelope) if command_envelope is not None else None
        recorded_text = _utc_text(recorded_at)
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM m3a_decision
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (str(contract_id), contract_revision),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation_id"] == str(operation_id)
                    and existing["decision_envelope_json"] == decision_json
                    and existing["level_envelope_json"] == level_json
                    and existing["held_event_json"] == held_json
                    and existing["command_envelope_json"] == command_json
                    and existing["business_result"] == business_result
                ):
                    for consequence in outgoing:
                        self._insert_outbox(connection, consequence)
                    return False
                raise RecordConflictError("M3a decision is immutable")
            connection.execute(
                """
                INSERT INTO m3a_decision (
                    contract_id, contract_revision, operation_id,
                    decision_envelope_json, level_envelope_json, held_event_json,
                    command_envelope_json, business_result, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(contract_id),
                    contract_revision,
                    str(operation_id),
                    decision_json,
                    level_json,
                    held_json,
                    command_json,
                    business_result,
                    recorded_text,
                ),
            )
            for consequence in outgoing:
                self._insert_outbox(connection, consequence)
        return True

    def find_m3a_decision(
        self, contract_id: UUID, contract_revision: int = 1
    ) -> dict[str, Any] | None:
        """Return one persisted M3a decision with validated envelope objects."""

        row = self._connection.execute(
            """
            SELECT * FROM m3a_decision
            WHERE contract_id = ? AND contract_revision = ?
            """,
            (str(contract_id), contract_revision),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["decision_envelope"] = self._decode_envelope(
            "m3a_decision",
            f"{contract_id}:{contract_revision}:decision",
            row["decision_envelope_json"],
        )
        result["level_envelope"] = self._decode_envelope(
            "m3a_decision", f"{contract_id}:{contract_revision}:level", row["level_envelope_json"]
        )
        result["held_event"] = (
            self._decode_envelope(
                "m3a_decision", f"{contract_id}:{contract_revision}:held", row["held_event_json"]
            )
            if row["held_event_json"] is not None
            else None
        )
        result["command_envelope"] = (
            self._decode_envelope(
                "m3a_decision",
                f"{contract_id}:{contract_revision}:command",
                row["command_envelope_json"],
            )
            if row["command_envelope_json"] is not None
            else None
        )
        return result

    def find_m3a_context_binding(
        self, contract_id: UUID, contract_revision: int = 1
    ) -> dict[str, Any] | None:
        """Return the first immutable context bound to a contract."""

        row = self._connection.execute(
            """
            SELECT * FROM m3a_context_binding
            WHERE contract_id = ? AND contract_revision = ?
            """,
            (str(contract_id), contract_revision),
        ).fetchone()
        return dict(row) if row is not None else None

    def find_m3a_effect_binding(
        self, contract_id: UUID, contract_revision: int = 1
    ) -> dict[str, Any] | None:
        """Return the first attributable effect proof bound to a contract."""

        row = self._connection.execute(
            """
            SELECT * FROM m3a_effect_binding
            WHERE contract_id = ? AND contract_revision = ?
            """,
            (str(contract_id), contract_revision),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["effect_envelope"] = self._decode_envelope(
            "m3a_effect_binding",
            f"{contract_id}:{contract_revision}",
            row["effect_envelope_json"],
        )
        result["effect"] = result["effect_envelope"].payload
        return result

    def find_m3a_effect_diagnostic(
        self, contract_id: UUID, contract_revision: int = 1
    ) -> dict[str, Any] | None:
        """Return the first unverified UNKNOWN diagnostic for a contract."""

        row = self._connection.execute(
            """
            SELECT * FROM m3a_effect_diagnostic
            WHERE contract_id = ? AND contract_revision = ?
            """,
            (str(contract_id), contract_revision),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["effect_envelope"] = self._decode_envelope(
            "m3a_effect_diagnostic",
            f"{contract_id}:{contract_revision}",
            row["effect_envelope_json"],
        )
        result["effect"] = result["effect_envelope"].payload
        return result

    def bind_m3a_effect_diagnostic(
        self, envelope: MessageEnvelope, *, recorded_at: datetime
    ) -> bool:
        """Retain one digest-unverified UNKNOWN without attributing its facts."""

        if not isinstance(envelope.payload, TwoButtonEffectEvidence):
            raise ValueError("M3a effect diagnostic requires m3a.spatial.effect")
        effect = envelope.payload
        if effect.command_digest_verified or effect.outcome != "UNKNOWN":
            raise ValueError("only digest-unverified UNKNOWN effects are diagnostics")
        effect_json = _m3a_payload_json(effect)
        effect_digest = _m3a_payload_digest(effect_json)
        envelope_json = _envelope_json(envelope)
        contract_text = str(effect.contract_id)
        operation_text = str(effect.operation_id)
        conflict = False
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM m3a_effect_diagnostic
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (contract_text, effect.contract_revision),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation_id"] == operation_text
                    and existing["intent_revision"] == effect.intent_revision
                    and existing["effect_digest"] == effect_digest
                    and existing["effect_json"] == effect_json
                    and existing["source_id"] == envelope.source_id
                    and existing["destination_id"] == envelope.destination_id
                    and existing["correlation_id"] == str(envelope.correlation_id)
                ):
                    return False
                connection.execute(
                    """
                    INSERT INTO m3a_effect_conflict (
                        contract_id, contract_revision, operation_id,
                        intent_revision, effect_digest, effect_json,
                        effect_envelope_json, source_id, destination_id,
                        correlation_id, reason, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract_text,
                        effect.contract_revision,
                        operation_text,
                        effect.intent_revision,
                        effect_digest,
                        effect_json,
                        envelope_json,
                        envelope.source_id,
                        envelope.destination_id,
                        str(envelope.correlation_id),
                        "M3A_EFFECT_DIAGNOSTIC_CONFLICT",
                        _utc_text(recorded_at),
                    ),
                )
                conflict = True
            else:
                connection.execute(
                    """
                    INSERT INTO m3a_effect_diagnostic (
                        contract_id, contract_revision, operation_id,
                        intent_revision, effect_digest, effect_json,
                        effect_envelope_json, source_id, destination_id,
                        correlation_id, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract_text,
                        effect.contract_revision,
                        operation_text,
                        effect.intent_revision,
                        effect_digest,
                        effect_json,
                        envelope_json,
                        envelope.source_id,
                        envelope.destination_id,
                        str(envelope.correlation_id),
                        _utc_text(recorded_at),
                    ),
                )
        if conflict:
            raise RecordConflictError("M3A_EFFECT_DIAGNOSTIC_CONFLICT")
        return True

    def bind_m3a_effect(self, envelope: MessageEnvelope, *, bound_at: datetime) -> bool:
        """Persist the first verified effect and reject changed semantic duplicates.

        Message IDs, source boots, sequence numbers, and timestamps describe a
        transport attempt and may change on a retry.  The persisted binding
        compares the complete canonical effect payload and the semantic
        source/destination/correlation tuple, so a replay of the same proof is
        harmless while a changed proof is durably recorded as a conflict.
        Digest-unverified UNKNOWN observations are diagnostics rather than an
        attributable first proof and therefore do not create the binding.
        """

        if not isinstance(envelope.payload, TwoButtonEffectEvidence):
            raise ValueError("M3a effect binding requires m3a.spatial.effect")
        effect = envelope.payload
        if effect.intent_revision != 1 or effect.contract_revision != 1:
            raise ValueError("M3a effect binding supports revision 1 only")
        effect_json = _m3a_payload_json(effect)
        effect_digest = _m3a_payload_digest(effect_json)
        envelope_json = _envelope_json(envelope)
        contract_text = str(effect.contract_id)
        operation_text = str(effect.operation_id)
        conflict = False
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM m3a_effect_binding
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (contract_text, effect.contract_revision),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation_id"] == operation_text
                    and existing["intent_revision"] == effect.intent_revision
                    and existing["effect_digest"] == effect_digest
                    and existing["effect_json"] == effect_json
                    and existing["source_id"] == envelope.source_id
                    and existing["destination_id"] == envelope.destination_id
                    and existing["correlation_id"] == str(envelope.correlation_id)
                ):
                    return False
                connection.execute(
                    """
                    INSERT INTO m3a_effect_conflict (
                        contract_id, contract_revision, operation_id,
                        intent_revision, effect_digest, effect_json,
                        effect_envelope_json, source_id, destination_id,
                        correlation_id, reason, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract_text,
                        effect.contract_revision,
                        operation_text,
                        effect.intent_revision,
                        effect_digest,
                        effect_json,
                        envelope_json,
                        envelope.source_id,
                        envelope.destination_id,
                        str(envelope.correlation_id),
                        "M3A_EFFECT_CONFLICT",
                        _utc_text(bound_at),
                    ),
                )
                conflict = True
            elif effect.command_digest_verified:
                connection.execute(
                    """
                    INSERT INTO m3a_effect_binding (
                        contract_id, contract_revision, operation_id,
                        intent_revision, effect_digest, effect_json,
                        effect_envelope_json, source_id, destination_id,
                        correlation_id, bound_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract_text,
                        effect.contract_revision,
                        operation_text,
                        effect.intent_revision,
                        effect_digest,
                        effect_json,
                        envelope_json,
                        envelope.source_id,
                        envelope.destination_id,
                        str(envelope.correlation_id),
                        _utc_text(bound_at),
                    ),
                )
            else:
                # An UNKNOWN result whose command digest was not reported is
                # retained in the normal inbox/outbox audit trail, but cannot
                # become the immutable first attributable proof.
                return False
        if conflict:
            raise RecordConflictError("M3A_EFFECT_CONFLICT")
        return True

    def record_m3a_effect_conflict(
        self,
        envelope: MessageEnvelope,
        *,
        reason: str,
        recorded_at: datetime,
    ) -> None:
        """Record a validated effect divergence without changing its binding."""

        if not isinstance(envelope.payload, TwoButtonEffectEvidence):
            raise ValueError("M3a effect conflict requires m3a.spatial.effect")
        if not reason or not reason.strip():
            raise ValueError("effect conflict reason must not be empty")
        effect = envelope.payload
        effect_json = _m3a_payload_json(effect)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO m3a_effect_conflict (
                    contract_id, contract_revision, operation_id,
                    intent_revision, effect_digest, effect_json,
                    effect_envelope_json, source_id, destination_id,
                    correlation_id, reason, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(effect.contract_id),
                    effect.contract_revision,
                    str(effect.operation_id),
                    effect.intent_revision,
                    _m3a_payload_digest(effect_json),
                    effect_json,
                    _envelope_json(envelope),
                    envelope.source_id,
                    envelope.destination_id,
                    str(envelope.correlation_id),
                    reason,
                    _utc_text(recorded_at),
                ),
            )

    def bind_m3a_context(self, envelope: MessageEnvelope, *, bound_at: datetime) -> bool:
        """Bind one canonical context and durably reject changed duplicates."""

        if not isinstance(envelope.payload, M3aSpatialExecutionContext):
            raise ValueError("M3a context binding requires m3a.spatial.context")
        context = envelope.payload
        if context.intent_revision != 1 or context.contract_revision != 1:
            raise ValueError("M3a context binding supports revision 1 only")
        context_json = _m3a_payload_json(context)
        context_digest = _m3a_payload_digest(context_json)
        contract_text = str(context.contract_id)
        operation_text = str(context.operation_id)
        task_text = str(context.task_id)
        conflict = False
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM m3a_context_binding
                WHERE contract_id = ? AND contract_revision = ?
                """,
                (contract_text, context.contract_revision),
            ).fetchone()
            if existing is not None:
                if (
                    existing["context_digest"] == context_digest
                    and existing["context_json"] == context_json
                ):
                    return False
                connection.execute(
                    """
                    INSERT INTO m3a_context_conflict (
                        contract_id, contract_revision, operation_id,
                        context_digest, source_id, correlation_id, reason,
                        context_json, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract_text,
                        context.contract_revision,
                        operation_text,
                        context_digest,
                        envelope.source_id,
                        str(envelope.correlation_id),
                        "M3A_CONTEXT_CONFLICT",
                        context_json,
                        _utc_text(bound_at),
                    ),
                )
                conflict = True
            if not conflict:
                connection.execute(
                    """
                    INSERT INTO m3a_context_binding (
                        contract_id, contract_revision, operation_id,
                        intent_revision, task_id, context_digest, context_json,
                        source_id, correlation_id, bound_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract_text,
                        context.contract_revision,
                        operation_text,
                        context.intent_revision,
                        task_text,
                        context_digest,
                        context_json,
                        envelope.source_id,
                        str(envelope.correlation_id),
                        _utc_text(bound_at),
                    ),
                )
        if conflict:
            raise RecordConflictError("M3A_CONTEXT_CONFLICT")
        return True

    def inspect_m3a_context_conflicts(
        self,
        contract_id: UUID | None = None,
        contract_revision: int = 1,
    ) -> list[dict[str, Any]]:
        if contract_id is None:
            return self._rows(
                """
                SELECT * FROM m3a_context_conflict
                ORDER BY recorded_at, conflict_id
                """
            )
        return [
            dict(row)
            for row in self._connection.execute(
                """
                SELECT * FROM m3a_context_conflict
                WHERE contract_id = ? AND contract_revision = ?
                ORDER BY recorded_at, conflict_id
                """,
                (str(contract_id), contract_revision),
            ).fetchall()
        ]

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
        command_digest: str | None = None,
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
        command_digest = _validate_command_digest(command_digest)
        accepted_text = _utc_text(accepted_at)
        try:
            deadline_text = _utc_text(accepted_at + timedelta(seconds=policy_seconds))
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
                if command_digest is not None and budget["command_digest"] != command_digest:
                    raise RecordConflictError(
                        "external command digest differs from durable budget binding"
                    )
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
                    command_digest,
                    attempt_limit, action_limit, max_elapsed_seconds,
                    window_started_at, deadline_at, clock_high_water_at,
                    attempts_reserved, actions_reserved, dispatch_reserved_at, resolution
                ) VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, 0, 0, NULL, NULL)
                """,
                (
                    operation_text,
                    contract_text,
                    contract_revision,
                    effect_key,
                    command_digest,
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
        command_digest: str | None = None,
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
        command_digest = _validate_command_digest(command_digest)
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
                raise RecordConflictError("external contract has no durable autonomy budget")
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
            if command_digest is not None and budget["command_digest"] != command_digest:
                raise RecordConflictError(
                    "external command digest differs from durable budget binding"
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
            deadline_at = _parse_utc_text(budget["deadline_at"], field_name="deadline_at")
            if recorded_at < accepted_at or recorded_at < high_water_at:
                raise BudgetClockRollbackError(
                    BUDGET_CLOCK_ROLLBACK
                    + ": trusted service clock is earlier than durable budget time"
                )
            if recorded_at >= deadline_at:
                raise BudgetDeadlineError(
                    BUDGET_DEADLINE_EXPIRED + ": service-clock budget window has elapsed"
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
        m3a_decision_envelope: MessageEnvelope | None = None,
        m3a_level_envelope: MessageEnvelope | None = None,
        m3a_business_result: str | None = None,
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
        if (m3a_decision_envelope is None) != (m3a_level_envelope is None):
            raise ValueError("M3a denial evidence requires both decision and level envelopes")
        if m3a_decision_envelope is not None:
            if m3a_decision_envelope.message_type != "m3a.spatial.decision":
                raise ValueError("M3a denial decision envelope has the wrong message type")
            if m3a_level_envelope is None or m3a_level_envelope.message_type != "m3a.spatial.level":
                raise ValueError("M3a denial level envelope has the wrong message type")
            if not isinstance(m3a_decision_envelope.payload, LocalTwoButtonDecision):
                raise ValueError("M3a denial decision envelope has the wrong payload")
            if not isinstance(m3a_level_envelope.payload, TwoButtonLevelEvidence):
                raise ValueError("M3a denial level envelope has the wrong payload")
            if not isinstance(m3a_business_result, str) or not m3a_business_result.strip():
                raise ValueError("M3a denial business result must not be empty")
        operation_text = str(operation_id)
        contract_text = str(contract_id)
        first_encoded = _envelope_json(first_envelope)
        candidate_encoded = _envelope_json(held_event)
        m3a_decision_encoded = (
            _envelope_json(m3a_decision_envelope)
            if m3a_decision_envelope is not None
            else None
        )
        m3a_level_encoded = (
            _envelope_json(m3a_level_envelope)
            if m3a_level_envelope is not None
            else None
        )
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
                accepted_at = _parse_utc_text(journal["accepted_at"], field_name="accepted_at")
                if event.occurred_at < accepted_at:
                    raise BudgetClockRollbackError(
                        BUDGET_CLOCK_ROLLBACK
                        + ": denial timestamp is earlier than durable acceptance"
                    )
            if processed_at < event.occurred_at:
                raise BudgetClockRollbackError(
                    BUDGET_CLOCK_ROLLBACK + ": inbox completion precedes denial timestamp"
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
                    raise RecordConflictError("budget denial first envelope payload is immutable")
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

            if m3a_decision_encoded is not None and m3a_level_encoded is not None:
                decision_row = connection.execute(
                    """
                    SELECT * FROM m3a_decision
                    WHERE contract_id = ? AND contract_revision = ?
                    """,
                    (contract_text, contract_revision),
                ).fetchone()
                if decision_row is None:
                    connection.execute(
                        """
                        INSERT INTO m3a_decision (
                            contract_id, contract_revision, operation_id,
                            decision_envelope_json, level_envelope_json, held_event_json,
                            command_envelope_json, business_result, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                        """,
                        (
                            contract_text,
                            contract_revision,
                            operation_text,
                            m3a_decision_encoded,
                            m3a_level_encoded,
                            candidate_encoded,
                            m3a_business_result,
                            processed_text,
                        ),
                    )
                elif (
                    str(decision_row["operation_id"]) != operation_text
                    or decision_row["decision_envelope_json"] != m3a_decision_encoded
                    or decision_row["level_envelope_json"] != m3a_level_encoded
                    or decision_row["held_event_json"] != candidate_encoded
                    or decision_row["command_envelope_json"] is not None
                    or decision_row["business_result"] != m3a_business_result
                ):
                    raise RecordConflictError("M3a denial decision is immutable")
                self._insert_outbox(connection, m3a_level_envelope)
                self._insert_outbox(connection, m3a_decision_envelope)

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
        if device_id is not None and (not isinstance(device_id, str) or not device_id.strip()):
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
    outgoing: Sequence[MessageEnvelope] = (),
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
        if any(not isinstance(value, MessageEnvelope) for value in outgoing):
            raise ValueError("outgoing must contain MessageEnvelope values")

        row = self._journal_row(self._connection, contract_id, contract_revision)
        expected_effect_key = str(row["effect_key"])
        occurred_text = _utc_text(occurred_at)
        dispatch_recorded_at = row["dispatch_recorded_at"]
        if dispatch_recorded_at is not None:
            dispatch_at = _parse_utc_text(dispatch_recorded_at, field_name="dispatch_recorded_at")
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
            for consequence in outgoing:
                self._insert_outbox(connection, consequence)
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
        return self._rows("SELECT * FROM execution_journal ORDER BY contract_id, contract_revision")

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

    def inspect_m3a_intent_bindings(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT * FROM m3a_intent_binding
            ORDER BY operation_id, intent_revision
            """
        )

    def inspect_m3a_intent_conflicts(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT * FROM m3a_intent_conflict
            ORDER BY recorded_at, conflict_id
            """
        )

    def inspect_m3a_decisions(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT * FROM m3a_decision
            ORDER BY contract_id, contract_revision
            """
        )

    def inspect_m3a_context_bindings(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT * FROM m3a_context_binding
            ORDER BY contract_id, contract_revision
            """
        )

    def inspect_m3a_effect_bindings(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT * FROM m3a_effect_binding
            ORDER BY contract_id, contract_revision
            """
        )

    def inspect_m3a_effect_diagnostics(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT * FROM m3a_effect_diagnostic
            ORDER BY contract_id, contract_revision
            """
        )

    def inspect_m3a_effect_conflicts(
        self,
        contract_id: UUID | None = None,
        contract_revision: int = 1,
    ) -> list[dict[str, Any]]:
        if contract_id is None:
            return self._rows(
                """
                SELECT * FROM m3a_effect_conflict
                ORDER BY recorded_at, conflict_id
                """
            )
        return [
            dict(row)
            for row in self._connection.execute(
                """
                SELECT * FROM m3a_effect_conflict
                WHERE contract_id = ? AND contract_revision = ?
                ORDER BY recorded_at, conflict_id
                """,
                (str(contract_id), contract_revision),
            ).fetchall()
        ]

    def inbox_messages(self) -> list[MessageEnvelope]:
        """Return validated inbox envelopes for recovery and read-model reconstruction."""

        rows = self._connection.execute(
            "SELECT message_id, payload_json FROM inbox ORDER BY received_at, message_id"
        ).fetchall()
        return [
            self._decode_envelope("inbox", row["message_id"], row["payload_json"]) for row in rows
        ]

    def outbox_messages(self) -> list[MessageEnvelope]:
        """Return validated outbox envelopes, including acknowledged records."""

        rows = self._connection.execute(
            "SELECT message_id, payload_json FROM outbox ORDER BY created_at, message_id"
        ).fetchall()
        return [
            self._decode_envelope("outbox", row["message_id"], row["payload_json"]) for row in rows
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

    def contract_history(self, contract_id: UUID, contract_revision: int) -> dict[str, Any]:
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
