import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from deferred_teleop.external_effect import (
    ExternalEffectObservation,
    ExternalOutcome,
    InvalidExternalProofError,
    PersistentDummyExternalEffect,
)
from deferred_teleop.protocol import ContractState, ExecutionEvent, ProvenanceKind, RobotState
from deferred_teleop.runtime import (
    DummyRobotService,
    EnvelopeFactory,
    FieldService,
    MissionService,
    dummy_pose,
    evidence,
)
from deferred_teleop.storage import (
    CURRENT_SCHEMA_VERSION,
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


class BooleanObservationAdapter:
    device_id = "dummy-external-button-1"

    def press(self, effect_key: str) -> None:
        del effect_key

    def observe(self, effect_key: str) -> bool:
        del effect_key
        return True


class CountingObservationAdapter:
    def __init__(self, *, device_id: str, clock: VirtualClock) -> None:
        self.device_id = device_id
        self.clock = clock
        self.press_calls = 0
        self.observe_calls = 0

    def press(self, effect_key: str) -> ExternalEffectObservation:
        self.press_calls += 1
        return self.observe(effect_key)

    def observe(self, effect_key: str) -> ExternalEffectObservation:
        self.observe_calls += 1
        return ExternalEffectObservation(
            effect_key=effect_key,
            device_id=self.device_id,
            outcome=ExternalOutcome.APPLIED,
            observed_at=self.clock.now(),
            observation_id=f"observation-{self.device_id}-{self.observe_calls}",
        )


class InvalidDeviceAdapter:
    def __init__(self, device_id: str | None) -> None:
        if device_id is not None:
            self.device_id = device_id
        self.press_calls = 0

    def press(self, effect_key: str) -> None:
        self.press_calls += 1
        del effect_key

    def observe(self, effect_key: str) -> bool:
        del effect_key
        return True


async def _deliver(service, envelope) -> None:
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


def test_persistent_fixture_reopens_and_each_press_is_an_impulse(tmp_path: Path) -> None:
    path = tmp_path / "external-effect.jsonl"
    effect_key = "press:operation:1"
    clock = VirtualClock()
    first = PersistentDummyExternalEffect(path, clock=clock)
    first.press(effect_key)
    first.press(effect_key)
    assert first.press_count == 2

    reopened = PersistentDummyExternalEffect(path, clock=clock)
    observation = reopened.observe(effect_key)
    assert observation.outcome is ExternalOutcome.APPLIED
    assert observation.effect_key == effect_key
    assert observation.device_id == "dummy-external-button-1"
    assert observation.details["press_count_for_effect"] == 2
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_external_success_has_attributable_proof_and_no_robot_effect_counter(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        external_path = tmp_path / "external.jsonl"
        with (
            mission_store,
            field_store,
            NodeStore(tmp_path / "robot.sqlite3") as robot_store,
        ):
            adapter = PersistentDummyExternalEffect(external_path, clock=clock)
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                phase_duration=0.0,
                external_effect_adapter=adapter,
            )
            await _deliver(robot, assignment)
            await _deliver(robot, contract)

            assert adapter.press_count == 1
            assert robot.effect_counter == 0
            journal = robot_store.inspect_execution_journal()[0]
            assert journal["state"] == ContractState.SUCCEEDED.value
            assert journal["effect_count"] == 0
            result = json.loads(journal["terminal_result_json"])
            assert result["effect_key"] == journal["effect_key"]
            assert result["device_id"] == adapter.device_id
            assert result["external_outcome"] == ExternalOutcome.APPLIED.value
            assert result["outcome"] == "APPLIED"
            assert result["observed_at"] == result["terminal_at"]
            assert result["proof"]["observed_at"] == result["observed_at"]
            assert not any(
                isinstance(message.payload, RobotState)
                for message in robot_store.outbox_messages()
            )

            for message in robot_store.outbox_messages():
                await _deliver(field, message)
            snapshots = [
                message
                for message in field_store.outbox_messages()
                if message.message_type == "site.snapshot"
            ]
            # The admission snapshot is valid; terminal success without Robot
            # telemetry must not create a second measured snapshot.
            assert len(snapshots) == 1
            assert not any(
                message.payload.next_state is ContractState.SUCCEEDED
                and message.causation_id is not None
                and snapshot.causation_id == message.message_id
                for snapshot in snapshots
                for message in field_store.inbox_messages()
                if message.message_type == "execution.event"
            )

    asyncio.run(scenario())


def test_external_events_use_durable_dispatch_times_without_clock_advance(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        with mission_store, field_store, NodeStore(tmp_path / "robot.sqlite3") as robot_store:
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                external_effect_adapter=PersistentDummyExternalEffect(
                    tmp_path / "external.jsonl", clock=clock
                ),
            )
            await _deliver(robot, assignment)
            await _deliver(robot, contract)

            event_messages = [
                message
                for message in robot_store.outbox_messages()
                if isinstance(message.payload, ExecutionEvent)
            ]
            assert {message.payload.next_state for message in event_messages} == {
                ContractState.ACCEPTED,
                ContractState.DISPATCH_RECORDED,
                ContractState.RUNNING,
                ContractState.SUCCEEDED,
            }
            assert all(
                message.payload.occurred_at <= clock.now()
                and message.created_at == message.payload.occurred_at
                for message in event_messages
            )
            running = next(
                message
                for message in event_messages
                if message.payload.next_state is ContractState.RUNNING
            )
            terminal = next(
                message
                for message in event_messages
                if message.payload.next_state is ContractState.SUCCEEDED
            )
            assert terminal.causation_id == running.message_id
            journal = robot_store.inspect_execution_journal()[0]
            accepted_at = datetime.fromisoformat(
                journal["accepted_at"].replace("Z", "+00:00")
            )
            dispatch_at = datetime.fromisoformat(
                journal["dispatch_recorded_at"].replace("Z", "+00:00")
            )
            terminal_at = datetime.fromisoformat(
                journal["terminal_at"].replace("Z", "+00:00")
            )
            accepted = next(
                message
                for message in event_messages
                if message.payload.next_state is ContractState.ACCEPTED
            )
            dispatch = next(
                message
                for message in event_messages
                if message.payload.next_state is ContractState.DISPATCH_RECORDED
            )
            assert accepted.payload.occurred_at == accepted_at
            assert dispatch.payload.occurred_at == dispatch_at
            assert running.payload.occurred_at == dispatch_at
            assert terminal.payload.occurred_at == terminal_at

    asyncio.run(scenario())


def test_crash_after_external_press_reopens_and_observes_without_repressing(
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
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=CrashAfterPress(external_path, clock=clock),
                )
                await _deliver(robot, assignment)
                with pytest.raises(RuntimeError, match="after external press"):
                    await _deliver(robot, contract)
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 1
                assert robot_store.inspect_execution_journal()[0]["state"] == (
                    ContractState.DISPATCH_RECORDED.value
                )

            with NodeStore(robot_path) as restarted_store:
                recovered = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=PersistentDummyExternalEffect(
                        external_path, clock=clock
                    ),
                )
                assert await recovered.recover() == 1
                assert recovered.effect_counter == 0
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 1
                assert restarted_store.inspect_execution_journal()[0]["state"] == (
                    ContractState.SUCCEEDED.value
                )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("crash_factory", "expected_press_count"),
    [(CrashBeforePress, 0), (CrashAfterPress, 1)],
    ids=["before-press", "after-press"],
)
def test_external_dispatch_never_falls_back_to_dummy_without_adapter(
    tmp_path: Path, crash_factory, expected_press_count: int
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
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=crash_factory(external_path, clock=clock),
                )
                await _deliver(robot, assignment)
                with pytest.raises(RuntimeError):
                    await _deliver(robot, contract)
                outbox_before_recovery = tuple(
                    message.message_id for message in robot_store.outbox_messages()
                )
                journal = robot_store.inspect_execution_journal()[0]
                assert journal["dispatch_device_id"] == "dummy-external-button-1"
                assert journal["state"] == ContractState.DISPATCH_RECORDED.value
                assert journal["effect_count"] == 0

            with NodeStore(robot_path) as restarted_store:
                recovered = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                )
                with pytest.raises(RecordConflictError, match="original adapter"):
                    await recovered.recover()
                assert tuple(
                    message.message_id for message in restarted_store.outbox_messages()
                ) == outbox_before_recovery
                journal = restarted_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.DISPATCH_RECORDED.value
                assert journal["effect_count"] == 0
                assert not any(
                    isinstance(message.payload, RobotState)
                    for message in restarted_store.outbox_messages()
                )
                assert not any(
                    isinstance(message.payload, ExecutionEvent)
                    and message.payload.next_state is ContractState.SUCCEEDED
                    for message in restarted_store.outbox_messages()
                )
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == (
                    expected_press_count
                )

    asyncio.run(scenario())


def test_clock_regression_blocks_external_recovery_before_observation(
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
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=CrashAfterPress(external_path, clock=clock),
                )
                await _deliver(robot, assignment)
                with pytest.raises(RuntimeError, match="after external press"):
                    await _deliver(robot, contract)
                journal_before = robot_store.inspect_execution_journal()
                outbox_before = [
                    message.model_dump(mode="json")
                    for message in robot_store.outbox_messages()
                ]

            # A restarted process may have a wall clock earlier than the
            # persisted dispatch boundary.  It must wait before touching the
            # adapter or adding a recovery event.
            clock.current = NOW - timedelta(seconds=1)
            with NodeStore(robot_path) as restarted_store:
                adapter = CountingObservationAdapter(
                    device_id="dummy-external-button-1", clock=clock
                )
                recovered = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=adapter,
                )
                with pytest.raises(RecordConflictError, match="clock.*dispatch_recorded_at"):
                    await recovered.recover()
                assert adapter.press_calls == 0
                assert adapter.observe_calls == 0
                assert restarted_store.inspect_execution_journal() == journal_before
                assert [
                    message.model_dump(mode="json")
                    for message in restarted_store.outbox_messages()
                ] == outbox_before

            # Once the clock catches up, recovery observes the durable fixture
            # record and converges without issuing a second impulse.
            clock.current = NOW
            with NodeStore(robot_path) as recovered_store:
                recovered = DummyRobotService(
                    recovered_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=PersistentDummyExternalEffect(
                        external_path, clock=clock
                    ),
                )
                assert await recovered.recover() == 1
                assert recovered_store.inspect_execution_journal()[0]["state"] == (
                    ContractState.SUCCEEDED.value
                )
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 1

    asyncio.run(scenario())


def test_recovery_binds_to_original_device_before_observing_or_pressing(
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
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=CrashAfterPress(external_path, clock=clock),
                )
                await _deliver(robot, assignment)
                with pytest.raises(RuntimeError, match="after external press"):
                    await _deliver(robot, contract)
                journal = robot_store.inspect_execution_journal()[0]
                assert journal["dispatch_device_id"] == "dummy-external-button-1"

            with NodeStore(robot_path) as restarted_store:
                adapter_b = CountingObservationAdapter(
                    device_id="replacement-external-button-2", clock=clock
                )
                recovered = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=adapter_b,
                )
                with pytest.raises(RecordConflictError, match="durable dispatch identity"):
                    await recovered.recover()
                journal = restarted_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.DISPATCH_RECORDED.value
                assert adapter_b.press_calls == 0
                assert adapter_b.observe_calls == 0
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 1

    asyncio.run(scenario())


def test_external_terminal_redelivery_without_adapter_is_rejected(
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
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=PersistentDummyExternalEffect(
                        external_path, clock=clock
                    ),
                )
                await _deliver(robot, assignment)
                await _deliver(robot, contract)
                outbox_before_redelivery = tuple(
                    message.message_id for message in robot_store.outbox_messages()
                )

            with NodeStore(robot_path) as restarted_store:
                duplicate = contract.model_copy(
                    update={
                        "message_id": uuid4(),
                        "source_sequence": contract.source_sequence + 1,
                    }
                )
                recovered = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                )
                with pytest.raises(RecordConflictError, match="original adapter"):
                    await _deliver(recovered, duplicate)
                assert tuple(
                    message.message_id for message in restarted_store.outbox_messages()
                ) == outbox_before_redelivery
                journal = restarted_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.SUCCEEDED.value
                assert journal["effect_count"] == 0
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 1
                assert not any(
                    isinstance(message.payload, RobotState)
                    for message in restarted_store.outbox_messages()
                )

    asyncio.run(scenario())


def test_v2_dispatch_without_device_refuses_adapter_and_keeps_legacy_dummy_path(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        robot_path = tmp_path / "robot.sqlite3"
        initialize_database(robot_path, target_version=2)
        contract_payload = contract.payload
        assignment_payload = assignment.payload
        connection = sqlite3.connect(robot_path)
        connection.execute(
            """
            INSERT INTO execution_journal (
                contract_id, contract_revision, operation_id, task_id, state,
                effect_key, effect_count, accepted_at, dispatch_recorded_at
            ) VALUES (?, ?, ?, ?, 'DISPATCH_RECORDED', ?, 0, ?, ?)
            """,
            (
                str(contract_payload.contract_id),
                contract_payload.contract_revision,
                str(contract_payload.operation_id),
                str(assignment_payload.task_id),
                f"press:{contract_payload.operation_id}:{contract_payload.contract_revision}",
                NOW.isoformat().replace("+00:00", "Z"),
                NOW.isoformat().replace("+00:00", "Z"),
            ),
        )
        connection.commit()
        connection.close()

        with mission_store, field_store:
            with NodeStore(robot_path) as robot_store:
                assert robot_store.schema_version == CURRENT_SCHEMA_VERSION
                adapter = CountingObservationAdapter(
                    device_id="legacy-replacement-device", clock=clock
                )
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=adapter,
                )
                await _deliver(robot, assignment)
                with pytest.raises(RecordConflictError, match="without durable device"):
                    await _deliver(robot, contract)
                assert adapter.press_calls == 0
                assert adapter.observe_calls == 0
                journal = robot_store.inspect_execution_journal()[0]
                assert journal["dispatch_device_id"] is None
                assert journal["state"] == ContractState.DISPATCH_RECORDED.value

            with NodeStore(robot_path) as restarted_store:
                legacy_dummy = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    phase_duration=0.0,
                )
                assert await legacy_dummy.recover() == 1
                journal = restarted_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.SUCCEEDED.value
                assert journal["effect_count"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("device_id", [None, "", "   "])
def test_external_adapter_requires_device_identity_before_press(
    tmp_path: Path, device_id: str | None
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        adapter = InvalidDeviceAdapter(device_id)
        with mission_store, field_store, NodeStore(tmp_path / "robot.sqlite3") as robot_store:
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                external_effect_adapter=adapter,
            )
            await _deliver(robot, assignment)
            with pytest.raises(ValueError, match="non-empty string device_id"):
                await _deliver(robot, contract)
            assert adapter.press_calls == 0
            assert robot_store.inspect_execution_journal() == []

    asyncio.run(scenario())


def test_unknown_after_external_press_is_held_without_repressing(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        external_path = tmp_path / "external.jsonl"
        robot_path = tmp_path / "robot.sqlite3"
        with mission_store, field_store:
            with NodeStore(robot_path) as robot_store:
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=CrashAfterPress(external_path, clock=clock),
                )
                await _deliver(robot, assignment)
                with pytest.raises(RuntimeError):
                    await _deliver(robot, contract)

            with NodeStore(robot_path) as restarted_store:
                recovered = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=PersistentDummyExternalEffect(
                        external_path,
                        observation_outcome=ExternalOutcome.UNKNOWN,
                        clock=clock,
                    ),
                )
                await recovered.recover()
                journal = restarted_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.HELD.value
                assert journal["effect_count"] == 0
                result = json.loads(journal["terminal_result_json"])
                assert result["outcome"] == "OUTCOME_UNKNOWN"
                assert result["external_outcome"] == ExternalOutcome.UNKNOWN.value
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 1

    asyncio.run(scenario())


def test_not_applied_after_crash_before_press_is_held_and_stays_zero(
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
                robot = DummyRobotService(
                    robot_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=CrashBeforePress(external_path, clock=clock),
                )
                await _deliver(robot, assignment)
                with pytest.raises(RuntimeError, match="before external press"):
                    await _deliver(robot, contract)

            with NodeStore(robot_path) as restarted_store:
                recovered = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=PersistentDummyExternalEffect(
                        external_path,
                        observation_outcome=ExternalOutcome.NOT_APPLIED,
                        clock=clock,
                    ),
                )
                await recovered.recover()
                journal = restarted_store.inspect_execution_journal()[0]
                assert journal["state"] == ContractState.HELD.value
                assert journal["effect_count"] == 0
                result = json.loads(journal["terminal_result_json"])
                assert result["outcome"] == "NOT_APPLIED_AFTER_UNCERTAIN_DISPATCH"
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 0

    asyncio.run(scenario())


def test_duplicate_contracts_and_restart_do_not_replay_external_press(tmp_path: Path) -> None:
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
                await _deliver(robot, contract)
                duplicate = contract.model_copy(
                    update={
                        "message_id": uuid4(),
                        "source_sequence": contract.source_sequence + 1,
                    }
                )
                await _deliver(robot, duplicate)
                assert adapter.press_count == 1

            with NodeStore(robot_path) as restarted_store:
                restarted = DummyRobotService(
                    restarted_store,
                    EnvelopeFactory("dummy-robot-1", clock),
                    external_effect_adapter=PersistentDummyExternalEffect(
                        external_path, clock=clock
                    ),
                )
                duplicate_after_restart = contract.model_copy(
                    update={
                        "message_id": uuid4(),
                        "source_sequence": contract.source_sequence + 2,
                    }
                )
                await _deliver(restarted, duplicate_after_restart)
                assert PersistentDummyExternalEffect(external_path, clock=clock).press_count == 1

    asyncio.run(scenario())


def test_unattributed_boolean_observation_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_store, field_store, _field, assignment, contract = await _make_chain(
            tmp_path, clock
        )
        with mission_store, field_store, NodeStore(tmp_path / "robot.sqlite3") as robot_store:
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                external_effect_adapter=BooleanObservationAdapter(),
            )
            await _deliver(robot, assignment)
            with pytest.raises(InvalidExternalProofError, match="attributable"):
                await _deliver(robot, contract)
            assert robot_store.inspect_execution_journal()[0]["state"] == (
                ContractState.DISPATCH_RECORDED.value
            )

    asyncio.run(scenario())


def test_external_legacy_causation_shape_without_pose_does_not_make_snapshot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "field.sqlite3") as store:
            field = FieldService(
                store,
                EnvelopeFactory("field-1", clock),
                dummy_fixture_compatibility=False,
            )
            contract_id = uuid4()
            terminal = field.factory.make(
                "execution.event",
                "field-1",
                uuid4(),
                ExecutionEvent(
                    event_id=uuid4(),
                    contract_id=contract_id,
                    contract_revision=1,
                    previous_state=ContractState.RUNNING,
                    next_state=ContractState.SUCCEEDED,
                    occurred_at=clock.now(),
                ),
                # This was the former external-path discriminator.  It is
                # deliberately insufficient evidence by itself.
                causation_id=contract_id,
            )
            await _deliver(field, terminal)
            assert not any(
                message.message_type == "site.snapshot" for message in store.outbox_messages()
            )

    asyncio.run(scenario())


def test_field_does_not_reconcile_held_or_cross_correlation_terminal(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "field.sqlite3") as store:
            field = FieldService(
                store,
                EnvelopeFactory("field-1", clock),
                dummy_fixture_compatibility=False,
            )
            contract_id = uuid4()
            held = field.factory.make(
                "execution.event",
                "field-1",
                uuid4(),
                ExecutionEvent(
                    event_id=uuid4(),
                    contract_id=contract_id,
                    contract_revision=1,
                    previous_state=ContractState.RUNNING,
                    next_state=ContractState.HELD,
                    occurred_at=clock.now(),
                ),
                causation_id=uuid4(),
            )
            await _deliver(field, held)
            assert not any(
                message.message_type == "site.snapshot" for message in store.outbox_messages()
            )

            correlation_a = uuid4()
            state = field.factory.make(
                "robot.state",
                "field-1",
                correlation_a,
                RobotState(
                    robot_id="dummy-robot-1",
                    pose=dummy_pose(),
                    evidence=evidence(
                        "robot-1", clock.now(), ProvenanceKind.MEASURED, world_revision=1
                    ),
                ),
            )
            await _deliver(field, state)
            correlation_b = uuid4()
            terminal = field.factory.make(
                "execution.event",
                "field-1",
                correlation_b,
                ExecutionEvent(
                    event_id=uuid4(),
                    contract_id=contract_id,
                    contract_revision=1,
                    previous_state=ContractState.RUNNING,
                    next_state=ContractState.SUCCEEDED,
                    occurred_at=clock.now(),
                ),
                causation_id=uuid4(),
            )
            await _deliver(field, terminal)
            assert not any(
                message.causation_id == terminal.message_id
                and message.message_type == "site.snapshot"
                for message in store.outbox_messages()
            )

    asyncio.run(scenario())
