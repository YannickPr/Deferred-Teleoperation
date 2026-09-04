from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from deferred_teleop.mission_view import ArticulatedMissionViewState
from deferred_teleop.node import JsonLogger, _mission_articulated_view_handler
from deferred_teleop.protocol import (
    ROOT_MESSAGE_TYPES,
    ArticulatedRobotState,
    GroundedOperation,
    MessageEnvelope,
    OperationState,
    ProvenanceKind,
    Quaternion,
    RobotState,
    Vector3,
)
from deferred_teleop.robot_model.articulated import (
    DEFAULT_SO101_DESCRIPTION_PATH,
    description_hash_for_file,
    validate_articulated_state,
)
from deferred_teleop.runtime import EnvelopeFactory, FieldService, MissionService
from deferred_teleop.storage import NodeStore
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "m2" / "articulated-state"
class VirtualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


def _load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _state(name: str = "valid-articulated-state.json") -> ArticulatedRobotState:
    return ArticulatedRobotState.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))


def _envelope(
    payload: ArticulatedRobotState,
    *,
    message_type: str = "robot.articulated_state",
    correlation_id: UUID | None = None,
) -> MessageEnvelope:
    correlation_id = correlation_id or uuid5(NAMESPACE_URL, "dtt-test-correlation")
    return MessageEnvelope(
        message_id=uuid5(NAMESPACE_URL, f"dtt-test-envelope:{payload.evidence.world_revision}"),
        message_type=message_type,
        source_id="so101-follower-1",
        source_boot_id=uuid5(NAMESPACE_URL, "dtt-test-boot"),
        source_sequence=payload.evidence.world_revision,
        destination_id="field-1",
        correlation_id=correlation_id,
        created_at=payload.evidence.produced_at,
        payload=payload,
    )


def test_articulated_topic_is_a_strict_root_payload() -> None:
    state = _state()
    envelope = _envelope(state)
    assert envelope.message_type == "robot.articulated_state"
    assert type(envelope.payload) is ArticulatedRobotState
    assert "robot.articulated_state" in ROOT_MESSAGE_TYPES
    assert envelope.causation_id is None

    with pytest.raises(ValidationError, match="requires payload ArticulatedRobotState"):
        MessageEnvelope.model_validate(
            envelope.model_dump(mode="python")
            | {"payload": RobotState(robot_id="r", pose=state.root_pose, evidence=state.evidence)}
        )


def test_json_schema_binds_message_type_to_payload_shape() -> None:
    schema = json.loads(
        (ROOT / "protocol" / "v0" / "schemas" / "message-envelope.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    articulated = _load("valid-articulated-envelope.json")
    assert validator.is_valid(articulated)

    legacy = json.loads(
        (
            ROOT
            / "protocol"
            / "v0"
            / "fixtures"
            / "valid"
            / "message-envelope.json"
        ).read_text(encoding="utf-8")
    )
    assert validator.is_valid(legacy)

    articulated_with_minimal_type = json.loads(json.dumps(articulated))
    articulated_with_minimal_type["message_type"] = "robot.state"
    assert not validator.is_valid(articulated_with_minimal_type)

    minimal_with_articulated_type = json.loads(json.dumps(legacy))
    minimal_with_articulated_type["message_type"] = "robot.articulated_state"
    assert not validator.is_valid(minimal_with_articulated_type)


def test_vector_and_quaternion_reject_nonfinite_values_without_changing_valid_golden() -> None:
    assert Vector3(x=0.0, y=0.0, z=0.0).model_dump() == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert Quaternion(x=0.0, y=0.0, z=0.0, w=1.0).w == 1.0
    with pytest.raises(ValidationError, match="finite"):
        Vector3(x=math.nan, y=0.0, z=0.0)
    with pytest.raises(ValidationError, match="finite"):
        Quaternion(x=0.0, y=0.0, z=0.0, w=math.inf)
    with pytest.raises(ValidationError, match="unit length"):
        Quaternion(x=0.0, y=0.0, z=0.0, w=0.0)


def test_state_validation_is_order_independent_and_returns_model_order() -> None:
    expected = (0.1, -0.2, 0.3, -0.4, 0.5, 0.6)
    result = validate_articulated_state(_state())
    reordered = validate_articulated_state(_state("reordered-articulated-state.json"))
    assert result.valid and reordered.valid
    assert result.diagnostics == reordered.diagnostics == ()
    assert result.ordered_positions == pytest.approx(expected)
    assert reordered.ordered_positions == pytest.approx(expected)


def test_description_hash_covers_exact_bytes_even_when_identity_fields_match(
    tmp_path: Path,
) -> None:
    altered_description = tmp_path / "so101-altered.json"
    altered_description.write_bytes(DEFAULT_SO101_DESCRIPTION_PATH.read_bytes() + b" \n")
    state = _state()
    assert state.model_reference.description_hash == description_hash_for_file(
        DEFAULT_SO101_DESCRIPTION_PATH
    )

    result = validate_articulated_state(state, description=altered_description)
    assert not result.valid
    assert any("description_hash mismatch" in diagnostic for diagnostic in result.diagnostics)
    assert not any("model_id mismatch" in diagnostic for diagnostic in result.diagnostics)
    assert not any("model_revision mismatch" in diagnostic for diagnostic in result.diagnostics)
    assert result.ordered_positions == ()


@pytest.mark.parametrize(
    ("fixture", "diagnostic"),
    [
        ("invalid-unknown-joint.json", "unknown joint name"),
        ("invalid-fixed-joint.json", "fixed or non-revolute"),
        ("invalid-missing-joint.json", "missing required revolute joint"),
        ("invalid-gripper-100.json", "gripper violates upper limit"),
        ("invalid-model-reference.json", "model_id mismatch"),
    ],
)
def test_description_validation_reports_invalid_state_without_fallback(
    fixture: str, diagnostic: str
) -> None:
    result = validate_articulated_state(
        ArticulatedRobotState.model_validate_json(
            (FIXTURES / fixture).read_text(encoding="utf-8")
        )
    )
    assert not result.valid
    assert any(diagnostic in item for item in result.diagnostics)
    assert result.ordered_positions == ()


def test_nonfinite_state_fixture_is_rejected_before_description_mapping() -> None:
    with pytest.raises(ValidationError, match="finite"):
        ArticulatedRobotState.model_validate_json(
            (FIXTURES / "invalid-nonfinite.json").read_text(encoding="utf-8")
        )


def test_duplicate_joint_names_are_rejected_at_wire_boundary() -> None:
    with pytest.raises(ValidationError, match="duplicate joint names"):
        ArticulatedRobotState.model_validate_json(
            (FIXTURES / "invalid-duplicate-joint.json").read_text(encoding="utf-8")
        )


def test_mission_articulated_view_requires_explicit_layer_provenance() -> None:
    valid = _load("valid-articulated-view.json")
    state = ArticulatedMissionViewState.model_validate_json(
        (FIXTURES / "valid-articulated-view.json").read_text(encoding="utf-8")
    )
    assert state.confirmed_robot_state is not None
    assert state.arrival_robot_state is not None
    assert state.target_robot_state is not None
    assert state.arrival_robot_state.robot_state.evidence.provenance is ProvenanceKind.PREDICTED
    assert state.target_robot_state.evidence.provenance is ProvenanceKind.OPERATOR_ASSERTED

    invalid_arrival = json.loads(json.dumps(valid))
    invalid_arrival["arrival_robot_state"]["predicted_for"] = "2026-09-04T12:00:00Z"
    with pytest.raises(ValidationError, match="predicted_for"):
        ArticulatedMissionViewState.model_validate_json(json.dumps(invalid_arrival))

    invalid_target = json.loads(json.dumps(valid))
    invalid_target["target_robot_state"]["evidence"]["provenance"] = "MEASURED"
    with pytest.raises(ValidationError, match="target articulated state"):
        ArticulatedMissionViewState.model_validate_json(json.dumps(invalid_target))


def test_field_persists_and_relays_articulated_state_to_mission(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as mission_store, NodeStore(
            tmp_path / "field.db"
        ) as field_store:
            mission = MissionService(
                mission_store, EnvelopeFactory("mission-1", clock), configured_one_way_delay=0.25
            )
            field = FieldService(field_store, EnvelopeFactory("field-1", clock))
            intent = mission.submit_press_button(executor_id="so101-follower-1")
            assert field_store.receive(intent, received_at=clock.now())
            await field.handle(intent)

            state = _state()
            articulated = _envelope(state, correlation_id=intent.correlation_id)
            assert field_store.receive(articulated, received_at=clock.now())
            await field.handle(articulated)
            forwarded = next(
                message
                for message in field_store.outbox_messages()
                if isinstance(message.payload, ArticulatedRobotState)
            )
            assert forwarded.destination_id == "mission-1"
            assert mission_store.receive(forwarded, received_at=clock.now())
            await mission.handle(forwarded)

            view = mission.articulated_view_state()
            assert view.confirmed_robot_state == state
            assert view.arrival_robot_state is None
            assert view.target_robot_state is None

    asyncio.run(scenario())


def test_articulated_view_does_not_borrow_state_across_reordered_operations(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            mission = MissionService(
                store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            first = mission.submit_press_button()
            clock.current += timedelta(seconds=1)
            second = mission.submit_press_button()

            first_state = _state().model_copy(
                update={
                    "robot_id": second.payload.preferred_executor,
                    "evidence": _state().evidence.model_copy(update={"world_revision": 99}),
                }
            )
            first_envelope = _envelope(first_state, correlation_id=first.correlation_id)
            assert store.receive(first_envelope, received_at=clock.now())
            await mission.handle(first_envelope)
            # The active operation is the later intent; its missing state cannot borrow the first.
            assert mission.articulated_view_state().confirmed_robot_state is None

            second_state = _state().model_copy(
                update={
                    "robot_id": second.payload.preferred_executor,
                    "evidence": _state().evidence.model_copy(update={"world_revision": 1}),
                }
            )
            second_envelope = _envelope(second_state, correlation_id=second.correlation_id)
            assert store.receive(second_envelope, received_at=clock.now())
            await mission.handle(second_envelope)
            assert mission.articulated_view_state().confirmed_robot_state == second_state

    asyncio.run(scenario())


@pytest.mark.parametrize("incompatible_latest", ["robot", "provenance"])
def test_articulated_view_does_not_fallback_from_latest_incompatible_state(
    incompatible_latest: str,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            mission = MissionService(
                store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            intent = mission.submit_press_button(executor_id="so101-follower-1")
            baseline = _state().model_copy(
                update={"evidence": _state().evidence.model_copy(update={"world_revision": 1})}
            )
            latest_update = {
                "evidence": _state().evidence.model_copy(update={"world_revision": 2})
            }
            if incompatible_latest == "robot":
                latest_update["robot_id"] = "other-robot"
            elif incompatible_latest == "provenance":
                latest_update["evidence"] = _state().evidence.model_copy(
                    update={
                        "world_revision": 2,
                        "provenance": ProvenanceKind.PREDICTED,
                        "model_version": "predictor-fixture-1",
                    }
                )
            latest = baseline.model_copy(update=latest_update)
            for state in (baseline, latest):
                envelope = _envelope(state, correlation_id=intent.correlation_id)
                assert store.receive(envelope, received_at=clock.now())
                await mission.handle(envelope)

            assert mission.articulated_view_state().confirmed_robot_state is None

    asyncio.run(scenario())


def test_articulated_view_does_not_fallback_from_latest_bad_frame(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            mission = MissionService(
                store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            intent = mission.submit_press_button(executor_id="so101-follower-1")
            baseline = _state().model_copy(
                update={"evidence": _state().evidence.model_copy(update={"world_revision": 1})}
            )
            grounded = EnvelopeFactory("field-1", clock).make(
                "operation.grounded",
                "mission-1",
                intent.correlation_id,
                GroundedOperation(
                    operation_id=intent.payload.operation_id,
                    target_entity_id="dummy-button-1",
                    target_pose=baseline.root_pose,
                    state=OperationState.ADMITTED,
                    evidence=baseline.evidence,
                ),
                causation_id=intent.message_id,
            )
            latest = baseline.model_copy(
                update={
                    "evidence": baseline.evidence.model_copy(update={"world_revision": 2}),
                    "root_pose": baseline.root_pose.model_copy(
                        update={
                            "frame": baseline.root_pose.frame.model_copy(
                                update={"calibration_version": "latest-incompatible-calibration"}
                            )
                        }
                    ),
                }
            )
            for envelope in (
                grounded,
                _envelope(baseline, correlation_id=intent.correlation_id),
                _envelope(latest, correlation_id=intent.correlation_id),
            ):
                assert store.receive(envelope, received_at=clock.now())
                await mission.handle(envelope)

            assert mission.articulated_view_state().confirmed_robot_state is None

    asyncio.run(scenario())


def test_grounded_frame_reference_is_scoped_to_operation(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            mission = MissionService(
                store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            intent = mission.submit_press_button(executor_id="so101-follower-1")
            state = _state().model_copy(
                update={"evidence": _state().evidence.model_copy(update={"world_revision": 1})}
            )
            state_envelope = _envelope(state, correlation_id=intent.correlation_id)

            adverse_frame = state.root_pose.frame.model_copy(
                update={"calibration_version": "unrelated-operation-calibration"}
            )
            adverse_grounded = EnvelopeFactory("field-1", clock).make(
                "operation.grounded",
                "mission-1",
                intent.correlation_id,
                GroundedOperation(
                    operation_id=uuid5(NAMESPACE_URL, "unrelated-operation"),
                    target_entity_id="dummy-button-1",
                    target_pose=state.root_pose.model_copy(update={"frame": adverse_frame}),
                    state=OperationState.ADMITTED,
                    evidence=state.evidence,
                ),
                causation_id=intent.message_id,
            )

            for envelope in (state_envelope, adverse_grounded):
                assert store.receive(envelope, received_at=clock.now())
                await mission.handle(envelope)

            # A contradictory grounded payload with the same correlation cannot poison the
            # active operation's frame reference when its operation_id differs.
            assert mission.articulated_view_state().confirmed_robot_state == state

    asyncio.run(scenario())


def test_opt_in_articulated_view_handler_uses_m2_service_method(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            mission = MissionService(
                store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            server = await serve(
                lambda connection: _mission_articulated_view_handler(
                    connection,
                    mission,
                    JsonLogger("mission-test"),
                    0.01,
                ),
                "127.0.0.1",
                0,
            )
            try:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as connection:
                    frame = ArticulatedMissionViewState.model_validate_json(await connection.recv())
                assert frame.message_type == "mission.articulated_view_state"
                assert frame.confirmed_robot_state is None
            finally:
                server.close()
                await server.wait_closed()

    asyncio.run(scenario())
