import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from deferred_teleop.protocol import (
    ContractState,
    EntitySelector,
    ExecutionContract,
    ExecutionEvent,
    ProvenanceKind,
    TaskAssignment,
)
from deferred_teleop.runtime import (
    DUMMY_PHASES,
    DummyRobotService,
    EnvelopeFactory,
    FieldService,
    MissionService,
)
from deferred_teleop.storage import NodeStore


@dataclass
class VirtualClock:
    current: datetime = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    sleeps: list[float] = field(default_factory=list)

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class CrashOnSleepClock(VirtualClock):
    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        raise RuntimeError("injected process crash")


async def _deliver(service, envelope) -> bool:
    is_new = service.store.receive(envelope, received_at=service.clock.now())
    if is_new:
        await service.handle(envelope)
    return is_new


def test_complete_domain_slice_survives_mission_disconnect_and_reconstructs(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        events: list[tuple[str, dict]] = []

        def record(event: str, fields) -> None:
            events.append((event, dict(fields)))

        mission_path = tmp_path / "mission.sqlite3"
        with (
            NodeStore(mission_path) as mission_store,
            NodeStore(tmp_path / "field.sqlite3") as field_store,
            NodeStore(tmp_path / "robot.sqlite3") as robot_store,
        ):
            mission = MissionService(
                mission_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=15.0,
                emit=record,
            )
            field_service = FieldService(
                field_store,
                EnvelopeFactory("field-1", clock),
                emit=record,
            )
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                phase_duration=2.0,
                emit=record,
            )

            intent = mission.submit_press_button()
            assert await _deliver(field_service, intent)
            field_to_robot = [
                message
                for message in field_store.pending_outbox(now=clock.now() + timedelta(seconds=1))
                if message.destination_id == "dummy-robot-1"
            ]
            assert [type(message.payload) for message in field_to_robot] == [
                TaskAssignment,
                ExecutionContract,
            ]
            for message in field_to_robot:
                assert await _deliver(robot, message)

            # Mission is deliberately not running while Field and Robot complete the effect.
            for message in robot_store.pending_outbox(now=clock.now() + timedelta(seconds=1)):
                assert await _deliver(field_service, message)
            assert robot.effect_counter == 1
            assert clock.sleeps == [2.0] * (len(DUMMY_PHASES) - 1)

            pending_for_mission = [
                message
                for message in field_store.pending_outbox(now=clock.now() + timedelta(seconds=1))
                if message.destination_id == "mission-1"
            ]
            assert pending_for_mission

        with NodeStore(mission_path) as restarted_store:
            restarted = MissionService(
                restarted_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=15.0,
                emit=record,
            )
            with NodeStore(tmp_path / "field.sqlite3") as field_store:
                for message in field_store.pending_outbox(
                    now=clock.now() + timedelta(seconds=1)
                ):
                    if message.destination_id == "mission-1":
                        await _deliver(restarted, message)

            view = restarted.view()
            assert view["operation_id"] == str(intent.payload.operation_id)
            assert view["terminal_state"] == ContractState.SUCCEEDED.value
            assert view["confirmed_state"]["evidence"]["provenance"] == "MEASURED"
            assert view["arrival_belief"]["evidence"]["provenance"] == "PREDICTED"
            assert view["arrival_belief"]["link_one_way_delay_seconds"] == 15.0
            assert view["arrival_belief"]["estimated_intent_arrival_at"] == view[
                "estimated_arrival_at"
            ]
            assert view["prediction_manifest"]["evidence"]["provenance"] == "PREDICTED"
            assert view["target_branch"]["provenance"] == ProvenanceKind.OPERATOR_ASSERTED
            assert any(event == "field.operation_admitted" for event, _ in events)
            assert any(event == "robot.skill_invoked" for event, _ in events)
            assert any(event == "robot.effect_committed" for event, _ in events)

    asyncio.run(scenario())


def test_semantic_duplicate_intent_and_contract_do_not_duplicate_effect(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with (
            NodeStore(tmp_path / "mission.sqlite3") as mission_store,
            NodeStore(tmp_path / "field.sqlite3") as field_store,
            NodeStore(tmp_path / "robot.sqlite3") as robot_store,
        ):
            mission = MissionService(
                mission_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            field_service = FieldService(
                field_store,
                EnvelopeFactory("field-1", clock),
            )
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                phase_duration=0.0,
            )
            intent = mission.submit_press_button()
            await _deliver(field_service, intent)
            duplicate_intent = intent.model_copy(
                update={"message_id": uuid4(), "source_sequence": intent.source_sequence + 1}
            )
            await _deliver(field_service, duplicate_intent)
            contracts = [
                message
                for message in field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            ]
            assert len(contracts) == 1

            assignment = next(
                message
                for message in field_store.outbox_messages()
                if isinstance(message.payload, TaskAssignment)
            )
            await _deliver(robot, assignment)
            await _deliver(robot, contracts[0])
            duplicate_contract = contracts[0].model_copy(
                update={"message_id": uuid4(), "source_sequence": contracts[0].source_sequence + 1}
            )
            await _deliver(robot, duplicate_contract)

            assert robot.effect_counter == 1
            terminal_events = [
                message
                for message in robot_store.outbox_messages()
                if isinstance(message.payload, ExecutionEvent)
                and message.payload.next_state is ContractState.SUCCEEDED
            ]
            assert len(terminal_events) == 2

    asyncio.run(scenario())


def test_expired_operation_is_explicitly_held_and_never_dispatched(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with (
            NodeStore(tmp_path / "mission.sqlite3") as mission_store,
            NodeStore(tmp_path / "field.sqlite3") as field_store,
        ):
            mission = MissionService(
                mission_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=10.0,
            )
            field_service = FieldService(
                field_store,
                EnvelopeFactory("field-1", clock),
            )
            intent = mission.submit_press_button(expires_in_seconds=1.0)
            clock.current += timedelta(seconds=2)
            await _deliver(field_service, intent)

            outgoing = field_store.outbox_messages()
            held = next(
                message.payload
                for message in outgoing
                if isinstance(message.payload, ExecutionEvent)
            )
            assert held.previous_state is ContractState.RECEIVED
            assert held.next_state is ContractState.HELD
            assert not any(message.destination_id == "dummy-robot-1" for message in outgoing)

    asyncio.run(scenario())


def test_non_whitelisted_target_is_held_before_grounding(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        events: list[tuple[str, dict]] = []
        with (
            NodeStore(tmp_path / "mission.sqlite3") as mission_store,
            NodeStore(tmp_path / "field.sqlite3") as field_store,
        ):
            mission = MissionService(
                mission_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            field_service = FieldService(
                field_store,
                EnvelopeFactory("field-1", clock),
                emit=lambda event, fields: events.append((event, dict(fields))),
            )
            original = mission.submit_press_button()
            unknown = original.model_copy(
                update={
                    "payload": original.payload.model_copy(
                        update={"selector": EntitySelector(entity_id="unknown-button")}
                    )
                }
            )
            await _deliver(field_service, unknown)

            outgoing = field_store.outbox_messages()
            assert not any(message.message_type == "operation.grounded" for message in outgoing)
            assert not any(message.destination_id == "dummy-robot-1" for message in outgoing)
            assert any(
                event == "field.operation_held"
                and fields["hold_reason"] == "selector-not-whitelisted"
                for event, fields in events
            )

    asyncio.run(scenario())


def test_robot_retries_contract_when_assignment_arrives_after_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with (
            NodeStore(tmp_path / "mission.sqlite3") as mission_store,
            NodeStore(tmp_path / "field.sqlite3") as field_store,
            NodeStore(tmp_path / "robot.sqlite3") as robot_store,
        ):
            mission = MissionService(
                mission_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            field_service = FieldService(field_store, EnvelopeFactory("field-1", clock))
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                phase_duration=0.0,
            )
            await _deliver(field_service, mission.submit_press_button())
            assignment = next(
                message
                for message in field_store.outbox_messages()
                if isinstance(message.payload, TaskAssignment)
            )
            contract = next(
                message
                for message in field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )

            await _deliver(robot, contract)
            assert robot.effect_counter == 0
            await _deliver(robot, assignment)
            assert robot.effect_counter == 1
            assert all(
                row["processing_state"] == "PROCESSED"
                for row in robot_store.inspect_inbox()
            )

    asyncio.run(scenario())


def test_robot_holds_unsupported_contract_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with (
            NodeStore(tmp_path / "mission.sqlite3") as mission_store,
            NodeStore(tmp_path / "field.sqlite3") as field_store,
            NodeStore(tmp_path / "robot.sqlite3") as robot_store,
        ):
            mission = MissionService(
                mission_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            field_service = FieldService(field_store, EnvelopeFactory("field-1", clock))
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                phase_duration=0.0,
            )
            await _deliver(field_service, mission.submit_press_button())
            assignment = next(
                message
                for message in field_store.outbox_messages()
                if isinstance(message.payload, TaskAssignment)
            )
            contract = next(
                message
                for message in field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )
            revision_two = contract.model_copy(
                update={
                    "message_id": uuid4(),
                    "source_sequence": contract.source_sequence + 1,
                    "payload": contract.payload.model_copy(update={"contract_revision": 2}),
                }
            )

            await _deliver(robot, assignment)
            await _deliver(robot, revision_two)
            assert robot.effect_counter == 0
            held = next(
                message.payload
                for message in robot_store.outbox_messages()
                if isinstance(message.payload, ExecutionEvent)
            )
            assert held.previous_state is ContractState.RECEIVED
            assert held.next_state is ContractState.HELD

    asyncio.run(scenario())


def test_robot_recovers_after_crash_during_skill_without_duplicate_effect(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = CrashOnSleepClock()
        robot_path = tmp_path / "robot.sqlite3"
        with (
            NodeStore(tmp_path / "mission.sqlite3") as mission_store,
            NodeStore(tmp_path / "field.sqlite3") as field_store,
        ):
            mission = MissionService(
                mission_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            field_service = FieldService(field_store, EnvelopeFactory("field-1", clock))
            await _deliver(field_service, mission.submit_press_button())
            assignment = next(
                message
                for message in field_store.outbox_messages()
                if isinstance(message.payload, TaskAssignment)
            )
            contract = next(
                message
                for message in field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )

        with NodeStore(robot_path) as robot_store:
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                phase_duration=1.0,
            )
            await _deliver(robot, assignment)
            try:
                await _deliver(robot, contract)
            except RuntimeError as error:
                assert str(error) == "injected process crash"
            else:
                raise AssertionError("the injected crash was not reached")
            contract_row = next(
                row
                for row in robot_store.inspect_inbox()
                if row["payload_type"] == "execution.contract"
            )
            assert contract_row["processing_state"] == "PROCESSING"
            assert robot.effect_counter == 0

        recovered_clock = VirtualClock(current=clock.current)
        with NodeStore(robot_path) as recovered_store:
            recovered_robot = DummyRobotService(
                recovered_store,
                EnvelopeFactory("dummy-robot-1", recovered_clock),
                phase_duration=0.0,
            )
            assert await recovered_robot.recover() == 1
            assert recovered_robot.effect_counter == 1
            assert all(
                row["processing_state"] == "PROCESSED"
                for row in recovered_store.inspect_inbox()
            )

    asyncio.run(scenario())
