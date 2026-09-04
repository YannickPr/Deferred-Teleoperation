import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from deferred_teleop.mission_view import MissionViewState
from deferred_teleop.node import JsonLogger, _mission_view_handler
from deferred_teleop.runtime import EnvelopeFactory, FieldService, MissionService
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
