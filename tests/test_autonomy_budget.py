import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from deferred_teleop.external_effect import (
    ExternalEffectObservation,
    ExternalOutcome,
    PersistentDummyExternalEffect,
)
from deferred_teleop.protocol import (
    ContractState,
    ExecutionEvent,
    MessageEnvelope,
)
from deferred_teleop.runtime import (
    DummyRobotService,
    EnvelopeFactory,
    FieldService,
    MissionService,
)
from deferred_teleop.storage import (
    BUDGET_CLOCK_ROLLBACK,
    BUDGET_DEADLINE_EXPIRED,
    BUDGET_POLICY_CONFLICT,
    BudgetClockRollbackError,
    BudgetDeadlineError,
    NodeStore,
    RecordConflictError,
    initialize_database,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class VirtualClock:
    def __init__(self, current: datetime = NOW) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        del seconds


class CrashAfterPress(PersistentDummyExternalEffect):
    def __init__(self, path: str | Path, **kwargs: object) -> None:
        super().__init__(path, **kwargs)
        self._crash = True

    def press(self, effect_key: str) -> ExternalEffectObservation:
        result = super().press(effect_key)
        if self._crash:
            self._crash = False
            raise RuntimeError("injected crash after external press")
        return result


class CrashBeforePress(PersistentDummyExternalEffect):
    def press(self, effect_key: str) -> ExternalEffectObservation:
        del effect_key
        raise RuntimeError("injected crash before external press")


async def _deliver(service, envelope: MessageEnvelope) -> None:
    if service.store.receive(envelope, received_at=service.clock.now()):
        await service.handle(envelope)


async def _make_chain(tmp_path: Path, clock: VirtualClock):
    mission_store = NodeStore(tmp_path / "mission.sqlite3")
    field_store = NodeStore(tmp_path / "field.sqlite3")
    mission = MissionService(
        mission_store,
        EnvelopeFactory("mission-1", clock),
        configured_one_way_delay=0.0,
    )
    field = FieldService(
        field_store,
        EnvelopeFactory("field-1", clock),
        dummy_fixture_compatibility=False,
    )
    intent = mission.submit_press_button()
    await _deliver(field, intent)
    assignment = next(
        message
        for message in field_store.outbox_messages()
        if message.message_type == "task.assignment"
    )
    contract = next(
        message
        for message in field_store.outbox_messages()
        if message.message_type == "execution.contract"
    )
    return mission_store, field_store, field, assignment, contract


def test_dtt_accepts_only_the_new_pre_dispatch_hold_transition() -> None:
    event = ExecutionEvent(
        event_id=uuid4(),
        contract_id=uuid4(),
        contract_revision=1,
        previous_state=ContractState.ACCEPTED,
        next_state=ContractState.HELD,
        occurred_at=NOW,
    )
    assert event.next_state is ContractState.HELD
    with pytest.raises(ValueError, match="illegal contract transition"):
        ExecutionEvent(
            event_id=uuid4(),
            contract_id=event.contract_id,
            contract_revision=1,
            previous_state=ContractState.ACCEPTED,
            next_state=ContractState.SUCCEEDED,
            occurred_at=NOW,
        )


def test_external_budget_is_one_reservation_and_replay_ignores_changed_policy(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        external_path = tmp_path / "external.jsonl"
        robot_path = tmp_path / "robot.sqlite3"
        with mission_store, field_store:
            with NodeStore(robot_path) as robot_store:
                adapter = PersistentDummyExternalEffect(external_path, clock=clock)
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=adapter,
                    max_elapsed_seconds=60.0,
                )
                await _deliver(robot, assignment)
                await _deliver(robot, contract)
                assert adapter.press_count == 1
                budget = robot_store.inspect_autonomy_budget()[0]
                assert budget["attempts_reserved"] == 1
                assert budget["actions_reserved"] == 1
                assert budget["attempt_limit"] == 1
                assert budget["action_limit"] == 1
                assert budget["resolution"] == "APPLIED"

            with NodeStore(robot_path) as restarted_store:
                restarted = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=PersistentDummyExternalEffect(
                        external_path, clock=clock
                    ),
                    max_elapsed_seconds=1.0,
                )
                duplicate = contract.model_copy(
                    update={"message_id": uuid4(), "source_sequence": contract.source_sequence + 1}
                )
                await _deliver(restarted, duplicate)
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 1
                assert restarted_store.inspect_autonomy_budget()[0]["actions_reserved"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("crash_type", "outcome", "expected_presses", "expected_state"),
    [
        (CrashBeforePress, ExternalOutcome.NOT_APPLIED, 0, ContractState.HELD),
        (CrashAfterPress, ExternalOutcome.UNKNOWN, 1, ContractState.HELD),
    ],
    ids=["crash-before-press-after-deadline", "crash-after-press-unknown"],
)
def test_reserved_budget_recovers_by_observation_after_restart_and_deadline(
    tmp_path: Path, crash_type, outcome: ExternalOutcome, expected_presses: int, expected_state
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        external_path = tmp_path / "external.jsonl"
        robot_path = tmp_path / "robot.sqlite3"
        with mission_store, field_store:
            with NodeStore(robot_path) as robot_store:
                crashing = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=crash_type(external_path, clock=clock),
                    max_elapsed_seconds=1.0,
                )
                await _deliver(crashing, assignment)
                with pytest.raises(RuntimeError):
                    await _deliver(crashing, contract)
                assert robot_store.inspect_autonomy_budget()[0]["actions_reserved"] == 1

            clock.current += timedelta(seconds=2)
            with NodeStore(robot_path) as restarted_store:
                adapter = PersistentDummyExternalEffect(
                    external_path,
                    observation_outcome=outcome,
                    clock=clock,
                )
                restarted = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=adapter,
                    max_elapsed_seconds=60.0,
                )
                await restarted.recover()
                assert adapter.press_count == expected_presses
                assert restarted_store.inspect_execution_journal()[0]["state"] == (
                    expected_state.value
                )
                assert restarted_store.inspect_autonomy_budget()[0]["actions_reserved"] == 1

    asyncio.run(scenario())


def test_deadline_and_clock_rollback_never_reserve_a_new_dispatch(tmp_path: Path) -> None:
    contract_id = UUID("70000000-0000-4000-8000-000000000001")
    operation_id = UUID("30000000-0000-4000-8000-000000000001")
    task_id = UUID("50000000-0000-4000-8000-000000000001")
    effect_key = f"press:{operation_id}:1"
    for attempted_at, error_type, code in (
        (NOW + timedelta(seconds=1), BudgetDeadlineError, BUDGET_DEADLINE_EXPIRED),
        (NOW - timedelta(seconds=1), BudgetClockRollbackError, BUDGET_CLOCK_ROLLBACK),
    ):
        path = tmp_path / f"{error_type.__name__}.sqlite3"
        with NodeStore(path) as store:
            assert store.admit_external_budget_contract(
                contract_id=contract_id,
                contract_revision=1,
                operation_id=operation_id,
                task_id=task_id,
                effect_key=effect_key,
                accepted_at=NOW,
                max_elapsed_seconds=1.0,
            )
            with pytest.raises(error_type, match=code):
                store.reserve_external_dispatch_with_budget(
                    contract_id,
                    1,
                    recorded_at=attempted_at,
                    device_id="dummy-external-button-1",
                    max_elapsed_seconds=1.0,
                )
            journal = store.inspect_execution_journal()[0]
            assert journal["state"] == ContractState.ACCEPTED.value
            assert store.inspect_autonomy_budget()[0]["actions_reserved"] == 0


def test_clock_rollback_waits_before_accepted_then_resumes_one_reservation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        external_path = tmp_path / "external.jsonl"
        robot_path = tmp_path / "robot.sqlite3"
        with mission_store, field_store:
            with NodeStore(robot_path) as robot_store:
                adapter = PersistentDummyExternalEffect(external_path, clock=clock)
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=adapter,
                )
                await _deliver(robot, assignment)
                assert robot_store.admit_external_budget_contract(
                    contract_id=contract.payload.contract_id,
                    contract_revision=1,
                    operation_id=contract.payload.operation_id,
                    task_id=assignment.payload.task_id,
                    effect_key=f"press:{contract.payload.operation_id}:1",
                    accepted_at=NOW,
                    max_elapsed_seconds=60.0,
                )

                clock.current = NOW - timedelta(seconds=10)
                with pytest.raises(BudgetClockRollbackError):
                    await _deliver(robot, contract)
                journal = robot_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.ACCEPTED.value
                assert journal["dispatch_recorded_at"] is None
                assert journal["terminal_at"] is None
                budget = robot_store.inspect_autonomy_budget()[0]
                assert budget["attempts_reserved"] == 0
                assert budget["actions_reserved"] == 0
                assert adapter.press_count == 0

                clock.current = NOW + timedelta(seconds=1)
                await robot.recover()
                assert adapter.press_count == 1
                journal = robot_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.SUCCEEDED.value
                budget = robot_store.inspect_autonomy_budget()[0]
                assert budget["attempts_reserved"] == 1
                assert budget["actions_reserved"] == 1

    asyncio.run(scenario())


def test_deadline_denial_is_held_without_reservation_or_external_proof(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        external_path = tmp_path / "external.jsonl"
        robot_path = tmp_path / "robot.sqlite3"
        with mission_store, field_store:
            with NodeStore(robot_path) as robot_store:
                adapter = PersistentDummyExternalEffect(external_path, clock=clock)
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=adapter,
                    max_elapsed_seconds=60.0,
                )
                await _deliver(robot, assignment)
                assert robot_store.admit_external_budget_contract(
                    contract_id=contract.payload.contract_id,
                    contract_revision=1,
                    operation_id=contract.payload.operation_id,
                    task_id=assignment.payload.task_id,
                    effect_key=f"press:{contract.payload.operation_id}:1",
                    accepted_at=NOW,
                    max_elapsed_seconds=60.0,
                )
                clock.current = NOW + timedelta(seconds=60)
                await _deliver(robot, contract)
                journal = robot_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.HELD.value
                assert journal["effect_count"] == 0
                assert journal["dispatch_recorded_at"] is None
                assert adapter.press_count == 0
                budget = robot_store.inspect_autonomy_budget()[0]
                assert budget["attempts_reserved"] == 0
                assert budget["actions_reserved"] == 0
                assert budget["resolution"] == BUDGET_DEADLINE_EXPIRED
                events = [
                    message.payload
                    for message in robot_store.outbox_messages()
                    if isinstance(message.payload, ExecutionEvent)
                ]
                assert len(events) == 1
                assert events[0].previous_state is ContractState.ACCEPTED
                assert events[0].next_state is ContractState.HELD
                assert "proof" not in journal["terminal_result_json"]

    asyncio.run(scenario())


def test_policy_change_before_dispatch_holds_without_rewriting_snapshot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        external_path = tmp_path / "external.jsonl"
        robot_path = tmp_path / "robot.sqlite3"
        with mission_store, field_store:
            with NodeStore(robot_path) as robot_store:
                adapter = PersistentDummyExternalEffect(external_path, clock=clock)
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=adapter,
                    max_elapsed_seconds=1.0,
                )
                await _deliver(robot, assignment)
                assert robot_store.admit_external_budget_contract(
                    contract_id=contract.payload.contract_id,
                    contract_revision=1,
                    operation_id=contract.payload.operation_id,
                    task_id=assignment.payload.task_id,
                    effect_key=f"press:{contract.payload.operation_id}:1",
                    accepted_at=NOW,
                    max_elapsed_seconds=60.0,
                )
                clock.current = NOW + timedelta(seconds=1)
                await _deliver(robot, contract)
                journal = robot_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.HELD.value
                assert journal["dispatch_recorded_at"] is None
                assert journal["effect_count"] == 0
                assert adapter.press_count == 0
                budget = robot_store.inspect_autonomy_budget()[0]
                assert budget["max_elapsed_seconds"] == 60.0
                assert budget["attempts_reserved"] == 0
                assert budget["actions_reserved"] == 0
                assert budget["resolution"] == BUDGET_POLICY_CONFLICT

    asyncio.run(scenario())


def test_external_budget_never_falls_back_to_dummy_without_adapter(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        robot_path = tmp_path / "robot.sqlite3"
        with mission_store, field_store:
            with NodeStore(robot_path) as robot_store:
                await _deliver(
                    DummyRobotService(
                        robot_store,
                        EnvelopeFactory("dummy-robot-1", clock),
                        external_effect_adapter=PersistentDummyExternalEffect(
                            tmp_path / "external.jsonl", clock=clock
                        ),
                    ),
                    assignment,
                )
                assert robot_store.admit_external_budget_contract(
                    contract_id=contract.payload.contract_id,
                    contract_revision=1,
                    operation_id=contract.payload.operation_id,
                    task_id=assignment.payload.task_id,
                    effect_key=f"press:{contract.payload.operation_id}:1",
                    accepted_at=NOW,
                    max_elapsed_seconds=60.0,
                )

            with NodeStore(robot_path) as restarted_store:
                restarted = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                )
                with pytest.raises(RecordConflictError, match="requires its original adapter"):
                    await _deliver(restarted, contract)
                journal = restarted_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.ACCEPTED.value
                assert journal["dispatch_recorded_at"] is None
                assert journal["effect_count"] == 0
                budget = restarted_store.inspect_autonomy_budget()[0]
                assert budget["attempts_reserved"] == 0
                assert budget["actions_reserved"] == 0

    asyncio.run(scenario())


def test_scope_conflict_is_durable_and_reuses_exact_held_event_bytes(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        external_path = tmp_path / "external.jsonl"
        robot_path = tmp_path / "robot.sqlite3"
        with mission_store, field_store:
            with NodeStore(robot_path) as robot_store:
                adapter = PersistentDummyExternalEffect(external_path, clock=clock)
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=adapter,
                )
                await _deliver(robot, assignment)
                await _deliver(robot, contract)
                conflicting = contract.model_copy(
                    update={
                        "message_id": uuid4(),
                        "source_sequence": contract.source_sequence + 1,
                        "payload": contract.payload.model_copy(update={"contract_id": uuid4()}),
                    }
                )
                await _deliver(robot, conflicting)
                first_denial = robot_store.inspect_autonomy_budget_denials()[0]
                first_event_json = first_denial["held_event_json"]
                held = robot_store.budget_denial_event(
                    conflicting.payload.contract_id, conflicting.payload.contract_revision
                )
                assert held is not None
                assert held.payload.previous_state is ContractState.RECEIVED
                repeat = conflicting.model_copy(
                    update={
                        "message_id": uuid4(),
                        "source_sequence": conflicting.source_sequence + 1,
                    }
                )
                await _deliver(robot, repeat)
                assert robot_store.inspect_autonomy_budget_denials()[0]["held_event_json"] == (
                    first_event_json
                )
                assert len(robot_store.inspect_execution_journal()) == 1
                assert adapter.press_count == 1

            with NodeStore(robot_path) as restarted_store:
                restarted = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=PersistentDummyExternalEffect(
                        external_path, clock=clock
                    ),
                )
                repeat_after_restart = repeat.model_copy(
                    update={"message_id": uuid4(), "source_sequence": repeat.source_sequence + 1}
                )
                await _deliver(restarted, repeat_after_restart)
                assert restarted_store.inspect_autonomy_budget_denials()[0]["held_event_json"] == (
                    first_event_json
                )
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 1

    asyncio.run(scenario())


def test_v3_migration_marks_dispatch_observe_only_and_acceptance_unbudgeted_hold(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        legacy_paths = (
            (tmp_path / "legacy-dispatch.sqlite3", "DISPATCH_RECORDED", "dummy-external-button-1"),
            (tmp_path / "legacy-succeeded.sqlite3", "SUCCEEDED", "dummy-external-button-1"),
            (tmp_path / "legacy-held.sqlite3", "HELD", "dummy-external-button-1"),
            (tmp_path / "legacy-accepted.sqlite3", "ACCEPTED", None),
        )
        effect_key = f"press:{contract.payload.operation_id}:1"
        for path, state, device_id in legacy_paths:
            initialize_database(path, target_version=3)
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    INSERT INTO execution_journal (
                        contract_id, contract_revision, operation_id, task_id, state,
                        effect_key, effect_count, accepted_at, dispatch_recorded_at,
                        dispatch_device_id, terminal_at, terminal_result_json
                    ) VALUES (?, 1, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(contract.payload.contract_id),
                        str(contract.payload.operation_id),
                        str(assignment.payload.task_id),
                        state,
                        effect_key,
                        NOW.isoformat().replace("+00:00", "Z"),
                        (NOW if device_id is not None else None),
                        device_id,
                        (
                            NOW.isoformat().replace("+00:00", "Z")
                            if state in {"SUCCEEDED", "HELD"}
                            else None
                        ),
                        ("{}" if state in {"SUCCEEDED", "HELD"} else None),
                    ),
                )
        with mission_store, field_store:
            with NodeStore(legacy_paths[0][0]) as store:
                assert store.budget_legacy_classification(
                    contract.payload.contract_id, 1
                ) == "LEGACY_OBSERVE_ONLY"
                assert store.inspect_autonomy_budget() == []
                robot = DummyRobotService(
                    store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=PersistentDummyExternalEffect(
                        tmp_path / "legacy-dispatch.jsonl",
                        observation_outcome=ExternalOutcome.NOT_APPLIED,
                        clock=clock,
                    ),
                )
                await _deliver(robot, assignment)
                await _deliver(robot, contract)
                assert store.inspect_execution_journal()[0]["state"] == ContractState.HELD.value
                assert store.inspect_autonomy_budget() == []

            for path, expected_state in (
                (legacy_paths[1][0], ContractState.SUCCEEDED),
                (legacy_paths[2][0], ContractState.HELD),
            ):
                with NodeStore(path) as store:
                    assert store.budget_legacy_classification(
                        contract.payload.contract_id, 1
                    ) == "LEGACY_OBSERVE_ONLY"
                    adapter = PersistentDummyExternalEffect(
                        tmp_path / f"{expected_state.value.lower()}-legacy.jsonl", clock=clock
                    )
                    robot = DummyRobotService(
                        store,
                        EnvelopeFactory("dummy-robot-1", clock),
                        external_effect_adapter=adapter,
                        max_elapsed_seconds=0.001,
                    )
                    await _deliver(robot, assignment)
                    await _deliver(robot, contract)
                    assert store.inspect_execution_journal()[0]["state"] == expected_state.value
                    assert store.inspect_autonomy_budget() == []
                    assert adapter.press_count == 0
                    assert adapter.records == ()
                    replay_events = [
                        message.payload
                        for message in store.outbox_messages()
                        if isinstance(message.payload, ExecutionEvent)
                    ]
                    assert len(replay_events) == 1
                    assert replay_events[0].previous_state is ContractState.RUNNING
                    assert replay_events[0].next_state is expected_state

            with NodeStore(legacy_paths[3][0]) as store:
                assert store.budget_legacy_classification(
                    contract.payload.contract_id, 1
                ) == "LEGACY_UNBUDGETED_HOLD"
                robot = DummyRobotService(
                    store,
                    EnvelopeFactory("dummy-robot-1", clock),
                )
                await _deliver(robot, assignment)
                await _deliver(robot, contract)
                row = store.inspect_execution_journal()[0]
                assert row["state"] == ContractState.HELD.value
                assert row["effect_count"] == 0
                assert json.loads(row["terminal_result_json"]) == {
                    "budget_denial": "LEGACY_UNBUDGETED_HOLD"
                }
                assert store.inspect_autonomy_budget() == []
                events = [
                    message.payload
                    for message in store.outbox_messages()
                    if isinstance(message.payload, ExecutionEvent)
                ]
                assert len(events) == 1
                assert events[0].previous_state is ContractState.ACCEPTED
                assert events[0].next_state is ContractState.HELD
                assert "proof" not in row["terminal_result_json"]

    asyncio.run(scenario())
