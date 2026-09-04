import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from deferred_teleop.mission_view import MissionViewState
from deferred_teleop.node import JsonLogger, _mission_view_handler
from deferred_teleop.protocol import (
    ApprovalPolicy,
    ContractState,
    EntitySelector,
    ExecutionEvent,
    MessageEnvelope,
    OperationIntent,
    OperationState,
    OperationType,
    Pose,
    ProvenanceKind,
    RobotForecast,
    RobotState,
    SiteSnapshot,
    SpatialFrame,
    Vector3,
)
from deferred_teleop.runtime import (
    EnvelopeFactory,
    FieldService,
    MissionService,
    MissionViewSelectionError,
    dummy_pose,
    evidence,
)
from deferred_teleop.storage import NodeStore
from pydantic import ValidationError
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve


class VirtualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


async def _deliver(service, envelope) -> None:
    assert service.store.receive(envelope, received_at=service.clock.now())
    await service.handle(envelope)


def _id(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"dtt-mission-view-test:{label}")


def _intent(
    factory: EnvelopeFactory,
    *,
    operation_id: UUID,
    correlation_id: UUID,
    created_at: datetime,
    message_id: UUID,
    entity_id: str,
) -> MessageEnvelope:
    return factory.make(
        "operation.intent",
        "field-1",
        correlation_id,
        OperationIntent(
            operation_id=operation_id,
            operation_type=OperationType.PRESS_BUTTON,
            selector=EntitySelector(entity_id=entity_id),
            preferred_executor="dummy-robot-1",
            approval_policy=ApprovalPolicy.AUTO_IF_WHITELISTED,
            state=OperationState.SUBMITTED,
        ),
        message_id=message_id,
        created_at=created_at,
    )


def _evidence_at(
    source_id: str,
    observed_at: datetime,
    provenance: ProvenanceKind,
    *,
    world_revision: int,
    produced_at: datetime | None = None,
):
    produced = produced_at or observed_at
    return evidence(
        source_id,
        observed_at,
        provenance,
        world_revision=world_revision,
    ).model_copy(
        update={
            "produced_at": produced,
            "fresh_until": produced + timedelta(seconds=30),
        }
    )


def _snapshot(
    factory: EnvelopeFactory,
    *,
    correlation_id: UUID,
    message_id: UUID,
    observed_at: datetime,
    world_revision: int = 1,
    produced_at: datetime | None = None,
    robot_id: str = "dummy-robot-1",
    pose: Pose | None = None,
):
    snapshot_evidence = _evidence_at(
        "field-test",
        observed_at,
        ProvenanceKind.MEASURED,
        world_revision=world_revision,
        produced_at=produced_at,
    )
    robot = RobotState(
        robot_id=robot_id,
        pose=pose or dummy_pose(),
        evidence=snapshot_evidence,
    )
    return factory.make(
        "site.snapshot",
        "mission-1",
        correlation_id,
        SiteSnapshot(
            site_id="dummy-site-1",
            entities=("dummy-button-1",),
            robot_states=(robot,),
            evidence=snapshot_evidence,
        ),
        causation_id=_id(f"snapshot-cause:{message_id}"),
        message_id=message_id,
        created_at=observed_at,
    )


def _forecast(
    factory: EnvelopeFactory,
    *,
    correlation_id: UUID,
    message_id: UUID,
    produced_at: datetime,
    predicted_for: datetime | None = None,
    robot_id: str = "dummy-robot-1",
    pose: Pose | None = None,
):
    forecast_evidence = _evidence_at(
        robot_id,
        produced_at,
        ProvenanceKind.PREDICTED,
        world_revision=1,
    )
    return factory.make(
        "robot.forecast",
        "mission-1",
        correlation_id,
        RobotForecast(
            robot_id=robot_id,
            predicted_pose=pose or dummy_pose(pressed=True),
            predicted_for=predicted_for or produced_at + timedelta(seconds=1),
            evidence=forecast_evidence,
        ),
        causation_id=_id(f"forecast-cause:{message_id}"),
        message_id=message_id,
        created_at=produced_at,
    )


def _terminal(
    factory: EnvelopeFactory,
    *,
    correlation_id: UUID,
    message_id: UUID,
    contract_id: UUID,
    occurred_at: datetime,
    contract_revision: int = 1,
    state: ContractState = ContractState.SUCCEEDED,
):
    return factory.make(
        "execution.event",
        "mission-1",
        correlation_id,
        ExecutionEvent(
            event_id=_id(f"terminal-event:{message_id}"),
            contract_id=contract_id,
            contract_revision=contract_revision,
            previous_state=ContractState.RUNNING,
            next_state=state,
            occurred_at=occurred_at,
        ),
        causation_id=_id(f"terminal-cause:{message_id}"),
        message_id=message_id,
        created_at=occurred_at,
    )


def test_mission_view_keeps_confirmed_arrival_and_target_distinct(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with (
            NodeStore(tmp_path / "mission.db") as mission_store,
            NodeStore(tmp_path / "field.db") as field_store,
        ):
            mission = MissionService(
                mission_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=5.0,
            )
            field = FieldService(field_store, EnvelopeFactory("field-1", clock))
            intent = mission.submit_press_button()
            await _deliver(field, intent)
            for message in field_store.outbox_messages():
                if message.destination_id == "mission-1":
                    await _deliver(mission, message)

            state = mission.view_state()
            assert state.protocol_version == "dtt/0"
            assert state.confirmed_state is not None
            assert state.confirmed_state.evidence.provenance == "MEASURED"
            assert state.target_branch is not None
            assert state.target_branch.evidence.provenance == "OPERATOR_ASSERTED"
            assert state.confirmed_state.pose != state.target_branch.pose
            assert state.arrival_belief is None
            assert [sample.source for sample in state.trajectory_forecasts] == [
                "CONFIRMED_STATE"
            ]

    asyncio.run(scenario())


def test_mission_view_rejects_unsupported_version_and_unknown_fields(tmp_path: Path) -> None:
    clock = VirtualClock()
    with NodeStore(tmp_path / "mission.db") as store:
        service = MissionService(
            store,
            EnvelopeFactory("mission-1", clock),
            configured_one_way_delay=0.0,
        )
        encoded = service.view_state().model_dump(mode="json")

    encoded["protocol_version"] = "dtt/1"
    with pytest.raises(ValidationError):
        MissionViewState.model_validate(encoded)
    encoded["protocol_version"] = "dtt/0"
    encoded["presentation_command"] = "turn-blue"
    with pytest.raises(ValidationError):
        MissionViewState.model_validate(encoded)


def test_mission_view_websocket_publishes_strict_frames(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            service = MissionService(
                store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            logger = JsonLogger("mission-test")
            server = await serve(
                lambda connection: _mission_view_handler(connection, service, logger, 0.01),
                "127.0.0.1",
                0,
            )
            port = server.sockets[0].getsockname()[1]
            async with server, connect(f"ws://127.0.0.1:{port}") as connection:
                first = MissionViewState.model_validate_json(await connection.recv())
                second = MissionViewState.model_validate_json(await connection.recv())
                assert first.source_sequence == 1
                assert second.source_sequence == 2

    asyncio.run(scenario())


def test_mission_view_scopes_late_previous_operation_after_new_submission(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(
                store,
                factory,
                configured_one_way_delay=5.0,
            )
            operation_a = _id("operation-a")
            correlation_a = _id("correlation-a")
            intent_a = _intent(
                factory,
                operation_id=operation_a,
                correlation_id=correlation_a,
                created_at=clock.now(),
                message_id=_id("intent-a"),
                entity_id="button-A",
            )
            store.enqueue(intent_a)
            await _deliver(
                mission,
                _snapshot(
                    factory,
                    correlation_id=correlation_a,
                    message_id=_id("snapshot-a-early"),
                    observed_at=clock.now(),
                    world_revision=1,
                ),
            )

            clock.current += timedelta(seconds=1)
            operation_b = _id("operation-b")
            correlation_b = _id("correlation-b")
            intent_b = _intent(
                factory,
                operation_id=operation_b,
                correlation_id=correlation_b,
                created_at=clock.now(),
                message_id=_id("intent-b"),
                entity_id="button-B",
            )
            store.enqueue(intent_b)

            before_b_proof = mission.view_state()
            assert before_b_proof.status.operation_id == operation_b
            assert before_b_proof.status.correlation_id == correlation_b
            assert before_b_proof.target_branch is not None
            assert before_b_proof.target_branch.entity_id == "button-B"
            assert before_b_proof.confirmed_state is None
            assert before_b_proof.arrival_belief is None
            assert before_b_proof.trajectory_forecasts == ()
            assert before_b_proof.prediction_manifests == ()

            await _deliver(
                mission,
                _snapshot(
                    factory,
                    correlation_id=correlation_b,
                    message_id=_id("snapshot-b"),
                    observed_at=clock.now(),
                    world_revision=2,
                ),
            )
            await _deliver(
                mission,
                _forecast(
                    factory,
                    correlation_id=correlation_b,
                    message_id=_id("forecast-b"),
                    produced_at=clock.now(),
                ),
            )
            terminal_b = _terminal(
                factory,
                correlation_id=correlation_b,
                message_id=_id("terminal-b"),
                contract_id=_id("contract-b"),
                occurred_at=clock.now(),
            )
            await _deliver(mission, terminal_b)

            # These A observations arrive after B has become visible. They must
            # not replace any B projection, even when their evidence is newer.
            await _deliver(
                mission,
                _snapshot(
                    factory,
                    correlation_id=correlation_a,
                    message_id=_id("snapshot-a-late"),
                    observed_at=clock.now() + timedelta(seconds=30),
                    world_revision=99,
                ),
            )
            await _deliver(
                mission,
                _forecast(
                    factory,
                    correlation_id=correlation_a,
                    message_id=_id("forecast-a-late"),
                    produced_at=clock.now() + timedelta(seconds=30),
                ),
            )
            await _deliver(
                mission,
                _terminal(
                    factory,
                    correlation_id=correlation_a,
                    message_id=_id("terminal-a-late"),
                    contract_id=_id("contract-a-late"),
                    occurred_at=clock.now() + timedelta(seconds=30),
                ),
            )

            state = mission.view_state()
            assert state.status.operation_id == operation_b
            assert state.status.correlation_id == correlation_b
            assert state.target_branch is not None
            assert state.target_branch.entity_id == "button-B"
            assert state.confirmed_state is not None
            assert state.arrival_belief is not None
            assert state.status.terminal_state is ContractState.SUCCEEDED
            assert state.status.terminal_contract_id == _id("contract-b")
            assert all(
                sample.pose == state.confirmed_state.pose
                for sample in state.trajectory_forecasts[:1]
            )
            assert state.prediction_manifests[0].forecast_ids == (_id("forecast-b"),)

    asyncio.run(scenario())


def test_mission_view_selection_is_reception_order_independent_and_survives_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_path = tmp_path / "mission.db"
        operation_id = _id("restart-operation")
        correlation_id = _id("restart-correlation")
        with NodeStore(mission_path) as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(
                store,
                factory,
                configured_one_way_delay=0.0,
            )
            store.enqueue(
                _intent(
                    factory,
                    operation_id=operation_id,
                    correlation_id=correlation_id,
                    created_at=clock.now(),
                    message_id=_id("restart-intent"),
                    entity_id="button-restart",
                )
            )
            early_snapshot = _snapshot(
                factory,
                correlation_id=correlation_id,
                message_id=_id("restart-snapshot-rev1"),
                observed_at=clock.now() + timedelta(seconds=10),
                world_revision=1,
            )
            latest_snapshot = _snapshot(
                factory,
                correlation_id=correlation_id,
                message_id=_id("restart-snapshot-rev2"),
                observed_at=clock.now() + timedelta(seconds=1),
                world_revision=2,
                pose=dummy_pose().model_copy(
                    update={"position": Vector3(x=0.8, y=0.1, z=0.18)}
                ),
            )
            early_forecast = _forecast(
                factory,
                correlation_id=correlation_id,
                message_id=_id("restart-forecast-early"),
                produced_at=clock.now() + timedelta(seconds=2),
                pose=dummy_pose(pressed=True).model_copy(
                    update={"position": Vector3(x=0.6, y=0.1, z=0.18)}
                ),
            )
            latest_forecast = _forecast(
                factory,
                correlation_id=correlation_id,
                message_id=_id("restart-forecast-latest"),
                produced_at=clock.now() + timedelta(seconds=3),
                pose=dummy_pose(pressed=True).model_copy(
                    update={"position": Vector3(x=0.7, y=0.1, z=0.18)}
                ),
            )
            terminal = _terminal(
                factory,
                correlation_id=correlation_id,
                message_id=_id("restart-terminal"),
                contract_id=_id("restart-contract"),
                occurred_at=clock.now() + timedelta(seconds=4),
            )
            nonterminal_after_terminal = factory.make(
                "execution.event",
                "mission-1",
                correlation_id,
                ExecutionEvent(
                    event_id=_id("restart-running-event"),
                    contract_id=_id("restart-contract"),
                    contract_revision=1,
                    previous_state=ContractState.DISPATCH_RECORDED,
                    next_state=ContractState.RUNNING,
                    occurred_at=clock.now() + timedelta(seconds=5),
                ),
                causation_id=_id("restart-running-cause"),
                message_id=_id("restart-running"),
                created_at=clock.now() + timedelta(seconds=5),
            )

            # Deliberately invert evidence delivery order.
            for message in (
                nonterminal_after_terminal,
                latest_forecast,
                early_snapshot,
                terminal,
                early_forecast,
                latest_snapshot,
            ):
                await _deliver(mission, message)

            first = mission.view_state()
            assert first.confirmed_state is not None
            assert first.confirmed_state.pose.position.x == 0.8
            assert first.arrival_belief is not None
            assert first.arrival_belief.pose.position.x == 0.7
            assert first.status.terminal_state is ContractState.SUCCEEDED

        with NodeStore(mission_path) as restarted_store:
            restarted = MissionService(
                restarted_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            second = restarted.view_state()
            assert second.model_dump(mode="json") == first.model_dump(mode="json")

    asyncio.run(scenario())


def test_mission_view_uses_message_id_for_same_time_ties(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(store, factory, configured_one_way_delay=0.0)
            operation_a = UUID("00000000-0000-0000-0000-000000000001")
            operation_b = UUID("00000000-0000-0000-0000-000000000002")
            correlation_a = UUID("00000000-0000-0000-0000-000000000011")
            correlation_b = UUID("00000000-0000-0000-0000-000000000012")
            intent_a = _intent(
                factory,
                operation_id=operation_a,
                correlation_id=correlation_a,
                created_at=clock.now(),
                message_id=UUID("00000000-0000-0000-0000-000000000101"),
                entity_id="button-A",
            )
            intent_b = _intent(
                factory,
                operation_id=operation_b,
                correlation_id=correlation_b,
                created_at=clock.now(),
                message_id=UUID("00000000-0000-0000-0000-000000000102"),
                entity_id="button-B",
            )
            store.enqueue(intent_a)
            store.enqueue(intent_b)
            tie_time = clock.now()
            low_snapshot = _snapshot(
                factory,
                correlation_id=correlation_b,
                message_id=UUID("00000000-0000-0000-0000-000000000111"),
                observed_at=tie_time,
                world_revision=2,
                pose=dummy_pose().model_copy(
                    update={"position": Vector3(x=0.1, y=0.1, z=0.18)}
                ),
            )
            high_snapshot = _snapshot(
                factory,
                correlation_id=correlation_b,
                message_id=UUID("00000000-0000-0000-0000-000000000112"),
                observed_at=tie_time,
                world_revision=2,
                pose=dummy_pose().model_copy(
                    update={"position": Vector3(x=0.9, y=0.1, z=0.18)}
                ),
            )
            low_forecast = _forecast(
                factory,
                correlation_id=correlation_b,
                message_id=UUID("00000000-0000-0000-0000-000000000121"),
                produced_at=tie_time,
                pose=dummy_pose(pressed=True).model_copy(
                    update={"position": Vector3(x=0.2, y=0.1, z=0.18)}
                ),
            )
            high_forecast = _forecast(
                factory,
                correlation_id=correlation_b,
                message_id=UUID("00000000-0000-0000-0000-000000000122"),
                produced_at=tie_time,
                pose=dummy_pose(pressed=True).model_copy(
                    update={"position": Vector3(x=0.8, y=0.1, z=0.18)}
                ),
            )
            low_terminal = _terminal(
                factory,
                correlation_id=correlation_b,
                message_id=UUID("00000000-0000-0000-0000-000000000131"),
                contract_id=UUID("00000000-0000-0000-0000-000000000141"),
                occurred_at=tie_time,
            )
            high_terminal = _terminal(
                factory,
                correlation_id=correlation_b,
                message_id=UUID("00000000-0000-0000-0000-000000000132"),
                contract_id=UUID("00000000-0000-0000-0000-000000000142"),
                occurred_at=tie_time,
            )
            for message in (
                high_terminal,
                low_snapshot,
                high_forecast,
                low_terminal,
                high_snapshot,
                low_forecast,
            ):
                await _deliver(mission, message)

            state = mission.view_state()
            assert state.status.operation_id == operation_b
            assert state.confirmed_state is not None
            assert state.confirmed_state.pose.position.x == 0.9
            assert state.arrival_belief is not None
            assert state.arrival_belief.pose.position.x == 0.8
            assert state.status.terminal_contract_id == UUID(
                "00000000-0000-0000-0000-000000000142"
            )

    asyncio.run(scenario())


def test_mission_view_rejects_ambiguous_operation_correlation(tmp_path: Path) -> None:
    clock = VirtualClock()
    with NodeStore(tmp_path / "mission.db") as store:
        factory = EnvelopeFactory("mission-1", clock)
        operation_id = _id("ambiguous-operation")
        store.enqueue(
            _intent(
                factory,
                operation_id=operation_id,
                correlation_id=_id("ambiguous-correlation-a"),
                created_at=clock.now(),
                message_id=_id("ambiguous-intent-a"),
                entity_id="button-A",
            )
        )
        store.enqueue(
            _intent(
                factory,
                operation_id=operation_id,
                correlation_id=_id("ambiguous-correlation-b"),
                created_at=clock.now() + timedelta(seconds=1),
                message_id=_id("ambiguous-intent-b"),
                entity_id="button-B",
            )
        )
        service = MissionService(store, factory, configured_one_way_delay=0.0)
        with pytest.raises(MissionViewSelectionError, match="correlation_id"):
            service.view_state()
        with pytest.raises(MissionViewSelectionError, match="correlation_id"):
            service.view()


@pytest.mark.parametrize("mismatch", ["robot", "frame", "calibration"])
def test_mission_view_drops_incompatible_forecast_artifacts(
    tmp_path: Path, mismatch: str
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(
                store,
                factory,
                configured_one_way_delay=0.0,
            )
            operation_id = _id(f"mismatch-operation:{mismatch}")
            correlation_id = _id(f"mismatch-correlation:{mismatch}")
            store.enqueue(
                _intent(
                    factory,
                    operation_id=operation_id,
                    correlation_id=correlation_id,
                    created_at=clock.now(),
                    message_id=_id(f"mismatch-intent:{mismatch}"),
                    entity_id="button-mismatch",
                )
            )
            confirmed_pose = dummy_pose()
            forecast_pose = dummy_pose(pressed=True)
            forecast_robot_id = "dummy-robot-1"
            if mismatch == "robot":
                forecast_robot_id = "other-robot"
            elif mismatch == "frame":
                forecast_pose = forecast_pose.model_copy(
                    update={
                        "frame": confirmed_pose.frame.model_copy(
                            update={"frame_id": "other-world"}
                        )
                    }
                )
            else:
                forecast_pose = forecast_pose.model_copy(
                    update={
                        "frame": confirmed_pose.frame.model_copy(
                            update={"calibration_version": "other-cal-1"}
                        )
                    }
                )
            await _deliver(
                mission,
                _snapshot(
                    factory,
                    correlation_id=correlation_id,
                    message_id=_id(f"mismatch-snapshot:{mismatch}"),
                    observed_at=clock.now(),
                    pose=confirmed_pose,
                ),
            )
            await _deliver(
                mission,
                _forecast(
                    factory,
                    correlation_id=correlation_id,
                    message_id=_id(f"mismatch-forecast:{mismatch}"),
                    produced_at=clock.now(),
                    robot_id=forecast_robot_id,
                    pose=forecast_pose,
                ),
            )

            state = mission.view_state()
            assert state.confirmed_state is not None
            assert state.target_branch is not None
            assert state.arrival_belief is None
            assert state.prediction_manifests == ()
            assert [sample.source for sample in state.trajectory_forecasts] == [
                "CONFIRMED_STATE"
            ]
            view = mission.view()
            assert view["confirmed_state"] is not None
            assert view["arrival_belief"] is None
            assert view["prediction_manifest"] is None

    asyncio.run(scenario())


def test_mission_view_drops_forecast_for_unexpected_robot_before_or_without_snapshot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "before-snapshot.db") as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(store, factory, configured_one_way_delay=0.0)
            operation_id = _id("unexpected-before-operation")
            correlation_id = _id("unexpected-before-correlation")
            store.enqueue(
                _intent(
                    factory,
                    operation_id=operation_id,
                    correlation_id=correlation_id,
                    created_at=clock.now(),
                    message_id=_id("unexpected-before-intent"),
                    entity_id="button-unexpected-before",
                )
            )
            await _deliver(
                mission,
                _forecast(
                    factory,
                    correlation_id=correlation_id,
                    message_id=_id("unexpected-before-forecast"),
                    produced_at=clock.now(),
                    robot_id="other-robot",
                ),
            )
            state = mission.view_state()
            assert state.confirmed_state is None
            assert state.arrival_belief is None
            assert state.target_branch is not None
            assert state.trajectory_forecasts == ()
            assert state.prediction_manifests == ()

        with NodeStore(tmp_path / "other-robot-snapshot.db") as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(store, factory, configured_one_way_delay=0.0)
            operation_id = _id("unexpected-snapshot-operation")
            correlation_id = _id("unexpected-snapshot-correlation")
            store.enqueue(
                _intent(
                    factory,
                    operation_id=operation_id,
                    correlation_id=correlation_id,
                    created_at=clock.now(),
                    message_id=_id("unexpected-snapshot-intent"),
                    entity_id="button-unexpected-snapshot",
                )
            )
            await _deliver(
                mission,
                _snapshot(
                    factory,
                    correlation_id=correlation_id,
                    message_id=_id("unexpected-snapshot"),
                    observed_at=clock.now(),
                    robot_id="other-robot",
                ),
            )
            await _deliver(
                mission,
                _forecast(
                    factory,
                    correlation_id=correlation_id,
                    message_id=_id("unexpected-snapshot-forecast"),
                    produced_at=clock.now(),
                    robot_id="other-robot",
                ),
            )
            state = mission.view_state()
            assert state.confirmed_state is None
            assert state.arrival_belief is None
            assert state.target_branch is not None
            assert state.trajectory_forecasts == ()
            assert state.prediction_manifests == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("reverse_delivery", [False, True])
def test_mission_view_suppresses_conflicting_terminal_states_across_restart(
    tmp_path: Path, reverse_delivery: bool
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_path = tmp_path / "mission.db"
        operation_id = _id(f"terminal-conflict-operation:{reverse_delivery}")
        correlation_id = _id(f"terminal-conflict-correlation:{reverse_delivery}")
        with NodeStore(mission_path) as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(store, factory, configured_one_way_delay=0.0)
            store.enqueue(
                _intent(
                    factory,
                    operation_id=operation_id,
                    correlation_id=correlation_id,
                    created_at=clock.now(),
                    message_id=_id(f"terminal-conflict-intent:{reverse_delivery}"),
                    entity_id="button-terminal-conflict",
                )
            )
            contract_id = _id(f"terminal-conflict-contract:{reverse_delivery}")
            succeeded = _terminal(
                factory,
                correlation_id=correlation_id,
                message_id=_id(f"terminal-conflict-succeeded:{reverse_delivery}"),
                contract_id=contract_id,
                occurred_at=clock.now() + timedelta(seconds=1),
                state=ContractState.SUCCEEDED,
            )
            failed = _terminal(
                factory,
                correlation_id=correlation_id,
                message_id=_id(f"terminal-conflict-failed:{reverse_delivery}"),
                contract_id=contract_id,
                occurred_at=clock.now() + timedelta(seconds=2),
                state=ContractState.FAILED,
            )
            messages = (failed, succeeded) if reverse_delivery else (succeeded, failed)
            for message in messages:
                await _deliver(mission, message)
            first = mission.view_state()
            assert first.status.terminal_state is None
            assert first.status.terminal_contract_id is None

        with NodeStore(mission_path) as restarted_store:
            restarted = MissionService(
                restarted_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            second = restarted.view_state()
            assert second.status.terminal_state is None
            assert second.status.terminal_contract_id is None
            assert second.model_dump(mode="json") == first.model_dump(mode="json")

    asyncio.run(scenario())


def test_mission_view_allows_same_state_terminal_replay_with_newer_timestamp(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(store, factory, configured_one_way_delay=0.0)
            operation_id = _id("terminal-replay-operation")
            correlation_id = _id("terminal-replay-correlation")
            store.enqueue(
                _intent(
                    factory,
                    operation_id=operation_id,
                    correlation_id=correlation_id,
                    created_at=clock.now(),
                    message_id=_id("terminal-replay-intent"),
                    entity_id="button-terminal-replay",
                )
            )
            contract_id = _id("terminal-replay-contract")
            first_terminal = _terminal(
                factory,
                correlation_id=correlation_id,
                message_id=_id("terminal-replay-first"),
                contract_id=contract_id,
                occurred_at=clock.now() + timedelta(seconds=1),
            )
            replay = _terminal(
                factory,
                correlation_id=correlation_id,
                message_id=_id("terminal-replay-later"),
                contract_id=contract_id,
                occurred_at=clock.now() + timedelta(seconds=2),
            )
            await _deliver(mission, replay)
            await _deliver(mission, first_terminal)
            state = mission.view_state()
            assert state.status.terminal_state is ContractState.SUCCEEDED
            assert state.status.terminal_contract_id == contract_id
            assert state.status.operation_id == operation_id

    asyncio.run(scenario())


def test_mission_view_hides_target_when_snapshot_frame_is_not_comparable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(store, factory, configured_one_way_delay=0.0)
            operation_id = _id("target-frame-operation")
            correlation_id = _id("target-frame-correlation")
            store.enqueue(
                _intent(
                    factory,
                    operation_id=operation_id,
                    correlation_id=correlation_id,
                    created_at=clock.now(),
                    message_id=_id("target-frame-intent"),
                    entity_id="button-target-frame",
                )
            )
            incompatible_pose = dummy_pose().model_copy(
                update={
                    "frame": SpatialFrame(
                        frame_id="other-world",
                        calibration_version="other-cal-1",
                    )
                }
            )
            await _deliver(
                mission,
                _snapshot(
                    factory,
                    correlation_id=correlation_id,
                    message_id=_id("target-frame-snapshot"),
                    observed_at=clock.now(),
                    pose=incompatible_pose,
                ),
            )
            state = mission.view_state()
            assert state.confirmed_state is not None
            assert state.target_branch is None
            assert state.trajectory_forecasts[0].source == "CONFIRMED_STATE"

    asyncio.run(scenario())


def test_mission_view_and_view_state_share_projection_semantics(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            factory = EnvelopeFactory("mission-1", clock)
            mission = MissionService(store, factory, configured_one_way_delay=2.0)
            operation_id = _id("coherence-operation")
            correlation_id = _id("coherence-correlation")
            intent = _intent(
                factory,
                operation_id=operation_id,
                correlation_id=correlation_id,
                created_at=clock.now(),
                message_id=_id("coherence-intent"),
                entity_id="button-coherence",
            )
            store.enqueue(intent)
            snapshot = _snapshot(
                factory,
                correlation_id=correlation_id,
                message_id=_id("coherence-snapshot"),
                observed_at=clock.now(),
                world_revision=2,
            )
            forecast = _forecast(
                factory,
                correlation_id=correlation_id,
                message_id=_id("coherence-forecast"),
                produced_at=clock.now(),
            )
            terminal = _terminal(
                factory,
                correlation_id=correlation_id,
                message_id=_id("coherence-terminal"),
                contract_id=_id("coherence-contract"),
                occurred_at=clock.now(),
            )
            for message in (forecast, terminal, snapshot):
                await _deliver(mission, message)

            view = mission.view()
            state = mission.view_state()
            assert view["operation_id"] == str(state.status.operation_id)
            assert view["correlation_id"] == str(state.status.correlation_id)
            assert view["estimated_arrival_at"] == (
                intent.created_at + timedelta(seconds=2.0)
            ).isoformat()
            assert view["confirmed_state"]["site_id"] == state.confirmed_state.site_id
            assert view["confirmed_state"]["robot_states"][-1]["pose"] == (
                state.confirmed_state.pose.model_dump(mode="json")
            )
            assert view["arrival_belief"]["robot_id"] == state.arrival_belief.robot_id
            assert view["arrival_belief"]["predicted_pose"] == (
                state.arrival_belief.pose.model_dump(mode="json")
            )
            assert view["prediction_manifest"] == state.prediction_manifests[0].model_dump(
                mode="json"
            )
            assert view["target_branch"]["entity_id"] == state.target_branch.entity_id
            assert view["terminal_state"] == state.status.terminal_state.value
            assert view["terminal_contract_id"] == str(state.status.terminal_contract_id)
            assert [sample.source.value for sample in state.trajectory_forecasts] == [
                "CONFIRMED_STATE",
                "ARRIVAL_BELIEF",
            ]

    asyncio.run(scenario())
