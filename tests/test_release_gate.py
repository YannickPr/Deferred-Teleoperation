from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from deferred_teleop.link import LinkFrame
from deferred_teleop.mission_view import MissionViewState
from deferred_teleop.node import JsonLogger, _mission_view_handler
from deferred_teleop.protocol import ExecutionContract, MessageEnvelope, TaskAssignment
from deferred_teleop.release_gate import (
    DEFAULT_CHECKLIST_PATH,
    DEFAULT_GOLDEN_ROOT,
    evaluate_release_checklist,
    validate_scenario_matrix,
    verify_golden_session,
)
from deferred_teleop.runtime import DummyRobotService, EnvelopeFactory, FieldService, MissionService
from deferred_teleop.storage import NodeStore
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

ROOT = Path(__file__).resolve().parents[1]
CHAIN_PATH = ROOT / "protocol" / "v0" / "fixtures" / "valid" / "dummy-operation-chain.json"


class VirtualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


async def _deliver(service, envelope) -> bool:
    is_new = service.store.receive(envelope, received_at=service.clock.now())
    if is_new:
        await service.handle(envelope)
    return is_new


def test_golden_session_strictly_replays_to_expected_results() -> None:
    result = verify_golden_session(DEFAULT_GOLDEN_ROOT)

    assert result["terminal_state"] == "SUCCEEDED"
    assert result["effect_counter"] == 1
    assert result["mission_view"] == "strict-dtt/0"
    assert result["deliveries"] > result["unique_envelopes"]


def test_scenario_matrix_is_complete_and_references_executable_evidence() -> None:
    result = validate_scenario_matrix()

    assert result["scenarios"] == 14
    assert result["test_references"] >= 14


def test_release_gate_is_ready_with_explicit_unreal_platform_scope() -> None:
    result = evaluate_release_checklist(DEFAULT_CHECKLIST_PATH)

    assert result["release_ready"]
    assert result["blockers"] == []


def test_field_restart_after_admission_preserves_robot_dispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        mission_path = tmp_path / "mission.db"
        field_path = tmp_path / "field.db"
        robot_path = tmp_path / "robot.db"
        with (
            NodeStore(mission_path) as mission_store,
            NodeStore(field_path) as field_store,
        ):
            mission = MissionService(
                mission_store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.0,
            )
            field = FieldService(field_store, EnvelopeFactory("field-1", clock))
            assert await _deliver(field, mission.submit_press_button())
            assert any(
                isinstance(message.payload, ExecutionContract)
                for message in field_store.outbox_messages()
            )

        # Field is restarted after its durable admission transaction and before Robot dispatch.
        with (
            NodeStore(field_path) as restarted_field_store,
            NodeStore(robot_path) as robot_store,
        ):
            pending = restarted_field_store.pending_outbox(
                now=clock.now() + timedelta(seconds=1)
            )
            assignment = next(
                message for message in pending if isinstance(message.payload, TaskAssignment)
            )
            contract = next(
                message for message in pending if isinstance(message.payload, ExecutionContract)
            )
            robot = DummyRobotService(
                robot_store,
                EnvelopeFactory("dummy-robot-1", clock),
                phase_duration=0.0,
            )
            assert await _deliver(robot, assignment)
            assert await _deliver(robot, contract)
            assert robot.effect_counter == 1

    asyncio.run(scenario())


def test_malformed_or_unsupported_frame_isolated_before_valid_delivery(tmp_path: Path) -> None:
    raw = json.loads(CHAIN_PATH.read_text(encoding="utf-8"))["messages"][0]
    envelope = MessageEnvelope.model_validate_json(json.dumps(raw))
    unsupported = LinkFrame.for_envelope(envelope).to_json().replace('"dtt/0"', '"dtt/1"')
    malformed_frames = ("{", '{"kind":"command","payload":"run"}', unsupported)

    for encoded in malformed_frames:
        with pytest.raises((ValueError, json.JSONDecodeError)):
            LinkFrame.from_json(encoded)

    recovered = LinkFrame.from_json(LinkFrame.for_envelope(envelope).to_json())
    assert recovered.envelope == envelope
    with NodeStore(tmp_path / "field.db") as store:
        assert store.receive(envelope, received_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC))
        assert len(store.inspect_inbox()) == 1


def test_mission_view_consumer_reconnects_after_server_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = VirtualClock()
        with NodeStore(tmp_path / "mission.db") as store:
            service = MissionService(
                store,
                EnvelopeFactory("mission-1", clock),
                configured_one_way_delay=0.25,
            )
            logger = JsonLogger("mission-release-gate")
            first_server = await serve(
                lambda connection: _mission_view_handler(connection, service, logger, 0.01),
                "127.0.0.1",
                0,
            )
            port = first_server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as connection:
                first = MissionViewState.model_validate_json(await connection.recv())
            first_server.close()
            await first_server.wait_closed()

            service.submit_press_button()
            clock.current += timedelta(seconds=1)
            second_server = await serve(
                lambda connection: _mission_view_handler(connection, service, logger, 0.01),
                "127.0.0.1",
                port,
            )
            try:
                async with connect(f"ws://127.0.0.1:{port}") as connection:
                    second = MissionViewState.model_validate_json(await connection.recv())
            finally:
                second_server.close()
                await second_server.wait_closed()

            assert first.status.operation_id is None
            assert second.status.operation_id is not None
            assert second.source_sequence > first.source_sequence

    asyncio.run(scenario())
