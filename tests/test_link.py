import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from deferred_teleop.link import (
    DeterministicLink,
    FaultProfile,
    InMemoryTransport,
    LinkFrame,
    ScriptedDelivery,
    WebSocketRelay,
)
from deferred_teleop.protocol import MessageEnvelope
from deferred_teleop.storage import NodeStore
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
CHAIN_PATH = ROOT / "protocol" / "v0" / "fixtures" / "valid" / "dummy-operation-chain.json"
NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


def _messages() -> list[MessageEnvelope]:
    raw = json.loads(CHAIN_PATH.read_text(encoding="utf-8"))["messages"]
    return [MessageEnvelope.model_validate_json(json.dumps(item)) for item in raw]


def _delivery_signature(link: DeterministicLink) -> list[tuple[float, str, str]]:
    return [
        (item.delivery_time, item.destination, item.frame.stable_id)
        for item in link.deliver_due(now=10_000.0)
    ]


def test_fixed_seed_and_profile_produce_identical_schedule_and_metrics() -> None:
    profile = FaultProfile(
        one_way_delay_seconds=2.0,
        jitter_distribution="uniform",
        jitter_seconds=0.5,
        duplicate_probability=0.5,
        reorder_window_seconds=1.0,
        seed=20260904,
    )
    links = [DeterministicLink(profile, wall_epoch=NOW) for _ in range(2)]

    for link in links:
        for envelope in _messages()[:4]:
            link.submit("mission", LinkFrame.for_envelope(envelope), now=3.0)

    assert _delivery_signature(links[0]) == _delivery_signature(links[1])
    assert links[0].metrics == links[1].metrics


def test_scripted_schedule_controls_delay_duplicates_and_reordering() -> None:
    first, second = _messages()[-2:]
    profile = FaultProfile(
        seed=1,
        scripted={
            str(first.message_id): ScriptedDelivery(delay_seconds=2.0, duplicates=0),
            str(second.message_id): ScriptedDelivery(delay_seconds=0.5, duplicates=2),
        },
    )
    link = DeterministicLink(profile, wall_epoch=NOW)

    assert link.submit("mission", LinkFrame.for_envelope(first), now=1.0) == 1
    assert link.submit("mission", LinkFrame.for_envelope(second), now=1.0) == 3
    delivered = link.deliver_due(now=10.0)

    assert [item.frame.stable_id for item in delivered] == [
        str(second.message_id),
        str(second.message_id),
        str(second.message_id),
        str(first.message_id),
    ]
    assert [item.delivery_time for item in delivered] == pytest.approx(
        [1.5, 1.500001, 1.500002, 3.0]
    )
    assert link.metrics["duplicates_injected"] == 2


def test_blackout_defers_delivery_without_real_waiting() -> None:
    link = DeterministicLink(
        FaultProfile(one_way_delay_seconds=1.0, blackout_intervals=((0.0, 10.0),)),
        wall_epoch=NOW,
    )
    link.submit("mission", LinkFrame.for_envelope(_messages()[0]), now=0.0)

    assert link.deliver_due(now=9.999) == []
    assert link.status(now=5.0)["blackout"] is True
    assert link.status(now=5.0)["blackout_deferrals"] == 1
    assert len(link.deliver_due(now=10.0)) == 1


def test_duplicate_envelope_reaches_receiver_but_store_deduplicates(tmp_path: Path) -> None:
    envelope = _messages()[0]
    link = DeterministicLink(
        FaultProfile(scripted={str(envelope.message_id): ScriptedDelivery(duplicates=1)}),
        wall_epoch=NOW,
    )
    assert link.submit("mission", LinkFrame.for_envelope(envelope), now=0.0) == 2

    with (
        NodeStore(tmp_path / "field.sqlite3") as field_store,
        NodeStore(tmp_path / "mission.sqlite3") as mission_store,
    ):
        mission_store.enqueue(envelope)
        accepted = [
            field_store.receive(item.frame.envelope, received_at=NOW)
            for item in link.deliver_due(now=1.0)
            if item.frame.envelope is not None
        ]
        assert accepted == [True, False]
        assert len(field_store.inspect_inbox()) == 1

        # The receiver emits its ACK only after the durable inbox write above.
        link.submit("field", LinkFrame.for_ack(envelope.message_id), now=1.0)
        returned_ack = link.deliver_due(now=1.0)[0].frame
        assert returned_ack.acknowledged_message_id == envelope.message_id
        assert mission_store.acknowledge(envelope.message_id, acked_at=NOW)
        assert mission_store.pending_outbox(now=NOW + timedelta(seconds=1)) == []


def test_duplicate_ack_is_idempotent_at_durable_outbox(tmp_path: Path) -> None:
    envelope = _messages()[0]
    ack = LinkFrame.for_ack(envelope.message_id)
    link = DeterministicLink(
        FaultProfile(scripted={ack.stable_id: ScriptedDelivery(duplicates=1)}),
        wall_epoch=NOW,
    )
    link.submit("field", ack, now=0.0)

    with NodeStore(tmp_path / "mission.sqlite3") as store:
        store.enqueue(envelope)
        results = [
            store.acknowledge(item.frame.acknowledged_message_id, acked_at=NOW)
            for item in link.deliver_due(now=1.0)
            if item.frame.acknowledged_message_id is not None
        ]
        assert results == [True, False]
        assert store.pending_outbox(now=NOW + timedelta(seconds=1)) == []


def test_control_lane_is_not_starved_by_low_bandwidth_payload() -> None:
    envelope = _messages()[0].model_copy(update={"source_id": "m" * 4_000})
    data = LinkFrame.for_envelope(envelope)
    ack = LinkFrame.for_ack(uuid4())
    link = DeterministicLink(
        FaultProfile(bandwidth_bytes_per_second=100.0),
        wall_epoch=NOW,
    )

    link.submit("mission", data, now=0.0)
    link.submit("mission", ack, now=0.0)
    delivered_early = link.deliver_due(now=2.0)

    assert [item.frame.kind for item in delivered_early] == ["ack"]
    assert link.status(now=2.0)["queued_deliveries"] == 1
    assert link.metrics["bytes_transmitted"] == len(ack.to_json().encode("utf-8"))
    assert [item.frame.kind for item in link.deliver_due(now=100.0)] == ["envelope"]


def test_expired_messages_and_full_queue_are_audited() -> None:
    envelope = _messages()[0].model_copy(update={"expires_at": NOW + timedelta(seconds=1)})
    expired = DeterministicLink(
        FaultProfile(one_way_delay_seconds=2.0),
        wall_epoch=NOW,
    )
    assert expired.submit("mission", LinkFrame.for_envelope(envelope), now=0.0) == 0
    assert expired.metrics["expired"] == 1

    capacity = DeterministicLink(
        FaultProfile(queue_capacity=1, duplicate_probability=1.0),
        wall_epoch=NOW,
    )
    assert capacity.submit("mission", LinkFrame.for_envelope(_messages()[0]), now=0.0) == 1
    assert capacity.metrics["dropped_capacity"] == 1
    assert capacity.metrics["duplicates_injected"] == 0


def test_in_memory_transport_is_a_substitutable_bidirectional_port() -> None:
    async def scenario() -> None:
        mission, field = InMemoryTransport.pair()
        envelope = _messages()[0]

        await mission.send(envelope)
        received = await field.receive()
        assert received.envelope == envelope
        await field.acknowledge(envelope.message_id)
        acknowledgement = await mission.receive()
        assert acknowledgement.acknowledged_message_id == envelope.message_id
        assert mission.health()["sent"] == 1
        assert field.health()["received"] == 1

    asyncio.run(scenario())


def test_link_crash_cannot_erase_endpoint_pending_outbox(tmp_path: Path) -> None:
    database = tmp_path / "mission.sqlite3"
    envelope = _messages()[0]
    with NodeStore(database) as store:
        store.enqueue(envelope)
        first_link = DeterministicLink(
            FaultProfile(one_way_delay_seconds=100.0),
            wall_epoch=NOW,
        )
        first_link.submit("mission", LinkFrame.for_envelope(envelope), now=0.0)
        assert first_link.status(now=0.0)["queued_deliveries"] == 1

    # The volatile first link is discarded here, modelling a process crash.
    with NodeStore(database) as restarted:
        assert restarted.pending_outbox(now=NOW + timedelta(seconds=1)) == [envelope]
        replacement_link = DeterministicLink(FaultProfile(), wall_epoch=NOW)
        replacement_link.submit("mission", LinkFrame.for_envelope(envelope), now=0.0)
        recovered = replacement_link.deliver_due(now=0.0)[0].frame.envelope
        assert recovered == envelope
        with NodeStore(tmp_path / "field.sqlite3") as field_store:
            assert field_store.receive(recovered, received_at=NOW + timedelta(seconds=1))


def test_websocket_relay_connects_separate_python_processes() -> None:
    async def wait_for_connection(
        relay: WebSocketRelay, side: str, process: asyncio.subprocess.Process
    ) -> None:
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if relay.health()[f"{side}_connections"]:
                return
            if process.returncode is not None:
                stdout, stderr = await process.communicate()
                raise AssertionError((stdout + stderr).decode())
            await asyncio.sleep(0.05)
        raise AssertionError(f"{side} subprocess did not connect")

    async def scenario() -> None:
        relay = WebSocketRelay(FaultProfile(one_way_delay_seconds=0.01))
        await relay.start()
        envelope = _messages()[0]
        encoded = LinkFrame.for_envelope(envelope).to_json()
        receiver_code = (
            "import asyncio,sys\n"
            "from websockets.asyncio.client import connect\n"
            "async def main():\n"
            " async with connect(f'ws://127.0.0.1:{sys.argv[1]}') as ws:\n"
            "  print(await ws.recv(), flush=True)\n"
            "asyncio.run(main())\n"
        )
        sender_code = (
            "import asyncio,sys\n"
            "from websockets.asyncio.client import connect\n"
            "async def main():\n"
            " async with connect(f'ws://127.0.0.1:{sys.argv[1]}') as ws:\n"
            "  await ws.send(sys.argv[2])\n"
            "  await asyncio.sleep(0.05)\n"
            "asyncio.run(main())\n"
        )
        receiver = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            receiver_code,
            str(relay.field_port),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await wait_for_connection(relay, "field", receiver)
            sender = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                sender_code,
                str(relay.mission_port),
                encoded,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            sender_stdout, sender_stderr = await asyncio.wait_for(sender.communicate(), 5.0)
            receiver_stdout, receiver_stderr = await asyncio.wait_for(receiver.communicate(), 5.0)
            assert sender.returncode == 0, (sender_stdout + sender_stderr).decode()
            assert receiver.returncode == 0, (receiver_stdout + receiver_stderr).decode()
            assert LinkFrame.from_json(receiver_stdout.decode().strip()).envelope == envelope
            async with connect(f"ws://127.0.0.1:{relay.mission_port}"):
                pass
            health = relay.health()
            assert health["delivered"] == 1
            assert health["reconnect_count"] == 1
            assert {
                "queued_deliveries",
                "next_delivery_time",
                "duplicates_injected",
                "blackout",
                "blackout_deferrals",
                "bytes_transmitted",
                "reconnect_count",
            } <= health.keys()
        finally:
            if receiver.returncode is None:
                receiver.kill()
                await receiver.wait()
            await relay.close()

    asyncio.run(scenario())


def test_repository_profiles_load_without_real_time_waits() -> None:
    reliable = FaultProfile.from_toml(ROOT / "profiles" / "reliable-short-delay.toml")
    blackout = FaultProfile.from_toml(ROOT / "profiles" / "15min-blackout.toml")

    assert reliable.one_way_delay_seconds == 0.05
    assert blackout.blackout_intervals == ((60.0, 960.0),)
    assert blackout.blackout_intervals[0][1] - blackout.blackout_intervals[0][0] == 900.0
