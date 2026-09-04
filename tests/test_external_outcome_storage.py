import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from deferred_teleop.external_effect import (
    ExternalEffectObservation,
    ExternalOutcome,
    InvalidExternalProofError,
)
from deferred_teleop.protocol import ContractState, ExecutionEvent
from deferred_teleop.runtime import EnvelopeFactory
from deferred_teleop.storage import (
    InvalidStateTransitionError,
    NodeStore,
    RecordConflictError,
    initialize_database,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
CONTRACT_ID = UUID("70000000-0000-4000-8000-000000000001")
OPERATION_ID = UUID("30000000-0000-4000-8000-000000000001")
TASK_ID = UUID("50000000-0000-4000-8000-000000000001")
EFFECT_KEY = "press:30000000-0000-4000-8000-000000000001:1"


class Clock:
    def now(self) -> datetime:
        return NOW


def _dispatch(store: NodeStore) -> None:
    assert store.accept_contract(
        contract_id=CONTRACT_ID,
        contract_revision=1,
        operation_id=OPERATION_ID,
        task_id=TASK_ID,
        effect_key=EFFECT_KEY,
        accepted_at=NOW,
    )
    assert store.record_dispatch(
        CONTRACT_ID,
        1,
        recorded_at=NOW + timedelta(seconds=1),
        device_id="button-sensor-1",
    )


def _terminal_event(
    outcome: ContractState = ContractState.SUCCEEDED,
    *,
    event_occurred_at: datetime = NOW + timedelta(seconds=2),
    envelope_created_at: datetime | None = None,
):
    return EnvelopeFactory("dummy-robot-1", Clock()).make(
        "execution.event",
        "field-1",
        OPERATION_ID,
        ExecutionEvent(
            event_id=UUID("80000000-0000-4000-8000-000000000001"),
            contract_id=CONTRACT_ID,
            contract_revision=1,
            previous_state=ContractState.RUNNING,
            next_state=outcome,
            occurred_at=event_occurred_at,
        ),
        causation_id=UUID("90000000-0000-4000-8000-000000000001"),
        created_at=envelope_created_at or event_occurred_at,
    )


def _proof(
    *,
    outcome: ExternalOutcome = ExternalOutcome.APPLIED,
    device_id: str = "button-sensor-1",
    effect_key: str = EFFECT_KEY,
    observation_id: str = "observation-1",
    observed_at: datetime = NOW + timedelta(seconds=2),
    details: dict[str, object] | None = None,
) -> ExternalEffectObservation:
    return ExternalEffectObservation(
        effect_key=effect_key,
        device_id=device_id,
        outcome=outcome,
        observed_at=observed_at,
        observation_id=observation_id,
        details=details or {},
    )


def test_external_resolution_is_atomic_reopenable_and_keeps_dummy_counter_zero(
    tmp_path: Path,
) -> None:
    path = tmp_path / "robot.sqlite3"
    proof = _proof()
    with NodeStore(path) as store:
        _dispatch(store)
        assert store.resolve_external_outcome(
            CONTRACT_ID,
            1,
            observation=proof,
            expected_device_id=proof.device_id,
            occurred_at=NOW + timedelta(seconds=2),
            terminal_event=_terminal_event(),
        )
        journal = store.inspect_execution_journal()[0]
        assert journal["state"] == ContractState.SUCCEEDED.value
        assert journal["effect_count"] == 0
        result = json.loads(journal["terminal_result_json"])
        assert result["effect_key"] == EFFECT_KEY
        assert result["device_id"] == proof.device_id
        assert result["external_outcome"] == ExternalOutcome.APPLIED.value
        assert store.pending_outbox(now=NOW + timedelta(seconds=3))

    with NodeStore(path) as restarted:
        assert restarted.inspect_execution_journal()[0]["effect_count"] == 0
        assert not restarted.resolve_external_outcome(
            CONTRACT_ID,
            1,
            observation=proof,
            expected_device_id=proof.device_id,
            occurred_at=NOW + timedelta(seconds=2),
            terminal_event=_terminal_event(),
        )


def test_external_dispatch_device_binding_is_immutable(tmp_path: Path) -> None:
    with NodeStore(tmp_path / "robot.sqlite3") as store:
        _dispatch(store)
        assert not store.record_dispatch(
            CONTRACT_ID,
            1,
            recorded_at=NOW + timedelta(seconds=2),
            device_id="button-sensor-1",
        )
        with pytest.raises(RecordConflictError, match="device identity"):
            store.record_dispatch(
                CONTRACT_ID,
                1,
                recorded_at=NOW + timedelta(seconds=2),
                device_id="replacement-sensor-2",
            )
        assert store.inspect_execution_journal()[0]["dispatch_device_id"] == (
            "button-sensor-1"
        )


def test_external_resolution_rejects_unattributed_or_conflicting_proof(tmp_path: Path) -> None:
    with NodeStore(tmp_path / "robot.sqlite3") as store:
        _dispatch(store)
        with pytest.raises(InvalidExternalProofError, match="attributable"):
            store.resolve_external_outcome(
                CONTRACT_ID,
                1,
                observation=True,
                occurred_at=NOW + timedelta(seconds=2),
                terminal_event=_terminal_event(),
            )
        with pytest.raises(InvalidExternalProofError, match="device_id"):
            store.resolve_external_outcome(
                CONTRACT_ID,
                1,
                observation=_proof(device_id="wrong-device"),
                expected_device_id="button-sensor-1",
                occurred_at=NOW + timedelta(seconds=2),
                terminal_event=_terminal_event(),
            )
        assert store.inspect_execution_journal()[0]["state"] == (
            ContractState.DISPATCH_RECORDED.value
        )

        proof = _proof()
        store.resolve_external_outcome(
            CONTRACT_ID,
            1,
            observation=proof,
            expected_device_id=proof.device_id,
            occurred_at=NOW + timedelta(seconds=2),
            terminal_event=_terminal_event(),
        )
        with pytest.raises(RecordConflictError, match="immutable"):
            store.resolve_external_outcome(
                CONTRACT_ID,
                1,
                observation=_proof(outcome=ExternalOutcome.UNKNOWN, observation_id="other"),
                expected_device_id=proof.device_id,
                occurred_at=NOW + timedelta(seconds=2),
                terminal_event=_terminal_event(ContractState.HELD),
            )


def test_external_terminal_proof_identity_timestamp_and_details_are_immutable(
    tmp_path: Path,
) -> None:
    with NodeStore(tmp_path / "robot.sqlite3") as store:
        _dispatch(store)
        proof = _proof()
        store.resolve_external_outcome(
            CONTRACT_ID,
            1,
            observation=proof,
            expected_device_id=proof.device_id,
            occurred_at=NOW + timedelta(seconds=2),
            terminal_event=_terminal_event(),
        )
        variants = (
            _proof(observation_id="observation-other"),
            _proof(observed_at=NOW + timedelta(seconds=1)),
            _proof(details={"sensor": "different"}),
        )
        for conflicting_proof in variants:
            with pytest.raises(RecordConflictError, match="immutable"):
                store.resolve_external_outcome(
                    CONTRACT_ID,
                    1,
                    observation=conflicting_proof,
                    expected_device_id=proof.device_id,
                    occurred_at=NOW + timedelta(seconds=2),
                    terminal_event=_terminal_event(),
                )


def test_external_future_proof_is_rejected_before_journal_or_outbox_mutation(
    tmp_path: Path,
) -> None:
    with NodeStore(tmp_path / "robot.sqlite3") as store:
        _dispatch(store)
        with pytest.raises(RecordConflictError, match="later than"):
            store.resolve_external_outcome(
                CONTRACT_ID,
                1,
                observation=_proof(observed_at=NOW + timedelta(seconds=3)),
                expected_device_id="button-sensor-1",
                occurred_at=NOW + timedelta(seconds=2),
                terminal_event=_terminal_event(),
            )
        journal = store.inspect_execution_journal()[0]
        assert journal["state"] == ContractState.DISPATCH_RECORDED.value
        assert journal["terminal_at"] is None
        assert store.pending_outbox(now=NOW + timedelta(seconds=10)) == []


def test_external_resolution_before_dispatch_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    with NodeStore(tmp_path / "robot.sqlite3") as store:
        _dispatch(store)
        journal_before = store.inspect_execution_journal()
        outbox_before = [
            message.model_dump(mode="json") for message in store.outbox_messages()
        ]
        with pytest.raises(RecordConflictError, match="precede durable dispatch_recorded_at"):
            store.resolve_external_outcome(
                CONTRACT_ID,
                1,
                observation=_proof(observed_at=NOW),
                expected_device_id="button-sensor-1",
                occurred_at=NOW + timedelta(milliseconds=500),
                terminal_event=_terminal_event(
                    event_occurred_at=NOW + timedelta(milliseconds=500)
                ),
            )
        assert store.inspect_execution_journal() == journal_before
        assert [
            message.model_dump(mode="json") for message in store.outbox_messages()
        ] == outbox_before


def test_external_terminal_timestamp_mismatch_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    with NodeStore(tmp_path / "robot.sqlite3") as store:
        _dispatch(store)
        proof = _proof()
        with pytest.raises(RecordConflictError, match="timestamp"):
            store.resolve_external_outcome(
                CONTRACT_ID,
                1,
                observation=proof,
                expected_device_id=proof.device_id,
                occurred_at=NOW + timedelta(seconds=2),
                terminal_event=_terminal_event(
                    envelope_created_at=NOW + timedelta(seconds=3)
                ),
            )
        with pytest.raises(RecordConflictError, match="timestamp"):
            store.resolve_external_outcome(
                CONTRACT_ID,
                1,
                observation=proof,
                expected_device_id=proof.device_id,
                occurred_at=NOW + timedelta(seconds=2),
                terminal_event=_terminal_event(
                    event_occurred_at=NOW + timedelta(seconds=3)
                ),
            )
        journal = store.inspect_execution_journal()[0]
        assert journal["state"] == ContractState.DISPATCH_RECORDED.value
        assert journal["terminal_at"] is None
        assert store.pending_outbox(now=NOW + timedelta(seconds=10)) == []


def test_v2_dispatch_without_device_migrates_and_preserves_legacy_dummy_recovery(
    tmp_path: Path,
) -> None:
    path = tmp_path / "robot.sqlite3"
    initialize_database(path, target_version=2)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        INSERT INTO execution_journal (
            contract_id, contract_revision, operation_id, task_id, state,
            effect_key, effect_count, accepted_at, dispatch_recorded_at
        ) VALUES (?, ?, ?, ?, 'DISPATCH_RECORDED', ?, 0, ?, ?)
        """,
        (
            str(CONTRACT_ID),
            1,
            str(OPERATION_ID),
            str(TASK_ID),
            EFFECT_KEY,
            NOW.isoformat().replace("+00:00", "Z"),
            (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        ),
    )
    connection.commit()
    connection.close()

    with NodeStore(path) as store:
        assert store.schema_version == 3
        journal = store.inspect_execution_journal()[0]
        assert journal["state"] == ContractState.DISPATCH_RECORDED.value
        assert journal["dispatch_device_id"] is None


def test_external_resolution_requires_dispatch_and_matches_terminal_state(
    tmp_path: Path,
) -> None:
    with NodeStore(tmp_path / "robot.sqlite3") as store:
        store.accept_contract(
            contract_id=CONTRACT_ID,
            contract_revision=1,
            operation_id=OPERATION_ID,
            task_id=TASK_ID,
            effect_key=EFFECT_KEY,
            accepted_at=NOW,
        )
        proof = _proof()
        with pytest.raises(InvalidStateTransitionError, match="DISPATCH_RECORDED"):
            store.resolve_external_outcome(
                CONTRACT_ID,
                1,
                observation=proof,
                expected_device_id=proof.device_id,
                occurred_at=NOW + timedelta(seconds=2),
                terminal_event=_terminal_event(),
            )

        assert store.record_dispatch(
            CONTRACT_ID,
            1,
            recorded_at=NOW + timedelta(seconds=1),
            device_id="button-sensor-1",
        )
        with pytest.raises(RecordConflictError, match="terminal state"):
            store.resolve_external_outcome(
                CONTRACT_ID,
                1,
                observation=proof,
                expected_device_id=proof.device_id,
                terminal_state=ContractState.HELD,
                occurred_at=NOW + timedelta(seconds=2),
                terminal_event=_terminal_event(),
            )
