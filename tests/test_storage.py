import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from deferred_teleop.protocol import ContractState, MessageEnvelope
from deferred_teleop.storage import (
    CURRENT_SCHEMA_VERSION,
    CorruptRecordError,
    IncompatibleSchemaError,
    NodeStore,
    RecordConflictError,
    initialize_database,
)

ROOT = Path(__file__).resolve().parents[1]
CHAIN_PATH = ROOT / "protocol" / "v0" / "fixtures" / "valid" / "dummy-operation-chain.json"
NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)
CONTRACT_ID = UUID("70000000-0000-4000-8000-000000000001")
OPERATION_ID = UUID("30000000-0000-4000-8000-000000000001")
TASK_ID = UUID("50000000-0000-4000-8000-000000000001")


def _raw_messages() -> list[dict[str, object]]:
    return json.loads(CHAIN_PATH.read_text(encoding="utf-8"))["messages"]


def _messages() -> list[MessageEnvelope]:
    return [MessageEnvelope.model_validate_json(json.dumps(item)) for item in _raw_messages()]


def _terminal_event() -> MessageEnvelope:
    raw = deepcopy(_raw_messages()[-1])
    raw["message_id"] = "00000000-0000-4000-8000-000000000099"
    raw["source_sequence"] = 99
    raw["payload"]["event_id"] = "80000000-0000-4000-8000-000000000099"
    raw["payload"]["previous_state"] = "RUNNING"
    raw["payload"]["next_state"] = "SUCCEEDED"
    return MessageEnvelope.model_validate_json(json.dumps(raw))


def _accept(store: NodeStore, *, effect_key: str = "press:dummy-button-1") -> None:
    assert store.accept_contract(
        contract_id=CONTRACT_ID,
        contract_revision=1,
        operation_id=OPERATION_ID,
        task_id=TASK_ID,
        effect_key=effect_key,
        accepted_at=NOW,
    )


def _dispatch(store: NodeStore) -> None:
    _accept(store)
    assert store.record_dispatch(CONTRACT_ID, 1, recorded_at=NOW + timedelta(seconds=1))


def _effect(store: NodeStore) -> None:
    assert store.commit_dummy_effect(
        CONTRACT_ID,
        1,
        terminal_state=ContractState.SUCCEEDED,
        terminal_result={"effect_counter": 1, "button": "dummy-button-1"},
        occurred_at=NOW + timedelta(seconds=2),
        terminal_event=_terminal_event(),
    )


def test_migrations_initialize_v1_and_upgrade_to_current(tmp_path: Path) -> None:
    path = tmp_path / "node.sqlite3"
    initialize_database(path, target_version=1)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'execution_audit'"
        ).fetchone()[0] == 0

    with NodeStore(path) as store:
        assert store.schema_version == CURRENT_SCHEMA_VERSION
        assert store.pragmas() == {"journal_mode": "wal", "busy_timeout_ms": 5_000}

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'execution_audit'"
        ).fetchone()[0] == 1


def test_newer_schema_fails_loudly_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (999, ?)",
            ("2099-01-01T00:00:00Z",),
        )
    with pytest.raises(IncompatibleSchemaError):
        NodeStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 999


def test_duplicate_inbox_is_deduplicated_and_collision_fails(tmp_path: Path) -> None:
    path = tmp_path / "field.sqlite3"
    intent = _messages()[0]
    with NodeStore(path) as store:
        assert store.receive(intent, received_at=NOW)
        assert not store.receive(intent, received_at=NOW + timedelta(seconds=1))
        assert len(store.inspect_inbox()) == 1

        conflicting = intent.model_copy(update={"source_id": "forged-source"})
        with pytest.raises(RecordConflictError):
            store.receive(conflicting, received_at=NOW)


def test_crash_after_outbox_commit_preserves_pending_delivery(tmp_path: Path) -> None:
    path = tmp_path / "mission.sqlite3"
    intent = _messages()[0]
    with NodeStore(path) as store:
        assert store.enqueue(intent)

    with NodeStore(path) as restarted:
        assert restarted.pending_outbox(now=NOW + timedelta(seconds=1)) == [intent]


def test_crash_after_inbox_commit_retries_processing_atomically(tmp_path: Path) -> None:
    path = tmp_path / "field.sqlite3"
    intent, grounded = _messages()[:2]
    with NodeStore(path) as store:
        store.receive(intent, received_at=NOW)
        assert store.claim_next_inbox() == intent

    with NodeStore(path) as restarted:
        assert restarted.recover_interrupted_processing() == 1
        assert restarted.claim_next_inbox() == intent
        restarted.complete_inbox(
            intent.message_id,
            processed_at=NOW + timedelta(seconds=1),
            handler_result_reference="grounding:1",
            outgoing=[grounded],
        )
        assert restarted.inspect_inbox()[0]["processing_state"] == "PROCESSED"
        assert restarted.pending_outbox(now=NOW + timedelta(seconds=2)) == [grounded]


def test_handler_completion_and_outbox_consequence_roll_back_together(tmp_path: Path) -> None:
    intent, grounded = _messages()[:2]
    conflicting = grounded.model_copy(update={"source_id": "forged-field"})
    with NodeStore(tmp_path / "field.sqlite3") as store:
        store.receive(intent, received_at=NOW)
        assert store.claim_next_inbox() == intent
        store.enqueue(grounded)
        with pytest.raises(RecordConflictError):
            store.complete_inbox(
                intent.message_id,
                processed_at=NOW + timedelta(seconds=1),
                handler_result_reference="must-rollback",
                outgoing=[conflicting],
            )
        assert store.inspect_inbox()[0]["processing_state"] == "PROCESSING"


def test_crash_after_acceptance_resumes_before_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "robot.sqlite3"
    with NodeStore(path) as store:
        _accept(store)

    with NodeStore(path) as restarted:
        assert restarted.inspect_execution_journal()[0]["state"] == "ACCEPTED"
        assert restarted.record_dispatch(CONTRACT_ID, 1, recorded_at=NOW + timedelta(seconds=1))


def test_crash_after_dispatch_resumes_before_dummy_effect(tmp_path: Path) -> None:
    path = tmp_path / "robot.sqlite3"
    with NodeStore(path) as store:
        _dispatch(store)

    with NodeStore(path) as restarted:
        _effect(restarted)
        journal = restarted.inspect_execution_journal()[0]
        assert journal["effect_count"] == 1
        assert journal["state"] == "SUCCEEDED"


def test_crash_after_effect_preserves_terminal_result_and_event(tmp_path: Path) -> None:
    path = tmp_path / "robot.sqlite3"
    terminal_event = _terminal_event()
    with NodeStore(path) as store:
        _dispatch(store)
        _effect(store)

    with NodeStore(path) as restarted:
        journal = restarted.inspect_execution_journal()[0]
        assert json.loads(journal["terminal_result_json"])["effect_counter"] == 1
        assert restarted.pending_outbox(now=NOW + timedelta(seconds=6)) == [terminal_event]
        assert not restarted.commit_dummy_effect(
            CONTRACT_ID,
            1,
            terminal_state=ContractState.SUCCEEDED,
            terminal_result={"effect_counter": 2},
            occurred_at=NOW + timedelta(seconds=4),
            terminal_event=terminal_event,
        )
        assert restarted.inspect_execution_journal()[0]["effect_count"] == 1


def test_crash_before_ack_preserves_attempt_and_acknowledges_once(tmp_path: Path) -> None:
    path = tmp_path / "robot.sqlite3"
    terminal_event = _terminal_event()
    with NodeStore(path) as store:
        _dispatch(store)
        _effect(store)
        assert store.record_attempt(
            terminal_event.message_id, next_attempt_at=NOW + timedelta(seconds=4)
        ) == 1

    with NodeStore(path) as restarted:
        record = restarted.inspect_outbox()[0]
        assert record["attempt_count"] == 1
        assert record["ack_state"] == "PENDING"
        assert restarted.acknowledge(
            terminal_event.message_id, acked_at=NOW + timedelta(seconds=5)
        )
        assert not restarted.acknowledge(
            terminal_event.message_id, acked_at=NOW + timedelta(seconds=6)
        )
        assert restarted.pending_outbox(now=NOW + timedelta(seconds=7)) == []


def test_duplicate_contract_and_effect_key_never_duplicate_effect(tmp_path: Path) -> None:
    with NodeStore(tmp_path / "robot.sqlite3") as store:
        _accept(store)
        assert not store.accept_contract(
            contract_id=CONTRACT_ID,
            contract_revision=1,
            operation_id=OPERATION_ID,
            task_id=TASK_ID,
            effect_key="press:dummy-button-1",
            accepted_at=NOW + timedelta(seconds=1),
        )
        with pytest.raises(RecordConflictError):
            store.accept_contract(
                contract_id=uuid4(),
                contract_revision=1,
                operation_id=OPERATION_ID,
                task_id=TASK_ID,
                effect_key="press:dummy-button-1",
                accepted_at=NOW,
            )


def test_contract_history_keeps_terminal_result_immutable_and_audit_append_only(
    tmp_path: Path,
) -> None:
    with NodeStore(tmp_path / "robot.sqlite3") as store:
        _dispatch(store)
        _effect(store)
        store.append_execution_audit(
            CONTRACT_ID,
            1,
            event_type="delivery-observed",
            metadata={"relay": "field-1"},
            recorded_at=NOW + timedelta(seconds=6),
        )
        history = store.contract_history(CONTRACT_ID, 1)
        assert history["journal"]["state"] == "SUCCEEDED"
        assert history["journal"]["effect_count"] == 1
        assert [item["event_type"] for item in history["audit"]] == ["delivery-observed"]


def test_corrupt_outbox_fails_loudly_without_rewriting_evidence(tmp_path: Path) -> None:
    path = tmp_path / "mission.sqlite3"
    intent = _messages()[0]
    with NodeStore(path) as store:
        store.enqueue(intent)

    corrupt = "{not-json"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE outbox SET payload_json = ? WHERE message_id = ?",
            (corrupt, str(intent.message_id)),
        )

    with NodeStore(path) as restarted:
        with pytest.raises(CorruptRecordError):
            restarted.pending_outbox(now=NOW + timedelta(seconds=1))
        assert restarted.inspect_outbox()[0]["payload_json"] == corrupt
