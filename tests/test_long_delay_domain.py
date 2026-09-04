"""Virtual-time domain proofs for delayed M1 operation delivery.

These tests exercise the real Mission, Field and dummy Robot services through durable
``NodeStore`` instances.  The Mission/Field boundary uses the same deterministic link
implementation as the development relay; Field/Robot is deliberately delivered directly
because the M1 domain slice has no second link port.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from deferred_teleop.link import DeterministicLink, FaultProfile, LinkFrame
from deferred_teleop.protocol import ContractState, ExecutionEvent, MessageEnvelope
from deferred_teleop.runtime import (
    DummyRobotService,
    EnvelopeFactory,
    FieldService,
    MissionService,
)
from deferred_teleop.storage import NodeStore

EPOCH = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
RETRY_HORIZON_SECONDS = 10_000_000.0


@dataclass
class VirtualClock:
    """A clock advanced only to a scheduled virtual delivery."""

    current: datetime = EPOCH

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("virtual sleep cannot be negative")
        self.current += timedelta(seconds=seconds)

    @property
    def seconds(self) -> float:
        return (self.current - EPOCH).total_seconds()

    def advance_to(self, seconds: float) -> None:
        if seconds < self.seconds:
            raise ValueError("virtual time cannot move backwards")
        candidate = EPOCH + timedelta(seconds=seconds)
        # ``DeterministicLink`` uses sub-microsecond tie breakers for duplicate frames,
        # while ``datetime`` stores microseconds. Round such a tie to the next representable
        # instant instead of spinning while the float deadline remains just ahead.
        if seconds > self.seconds and candidate <= self.current:
            candidate = self.current + timedelta(microseconds=1)
        self.current = candidate


@dataclass
class StableUuidFactory:
    node_id: str
    sequence: int = 0

    def __call__(self) -> UUID:
        self.sequence += 1
        return uuid5(NAMESPACE_URL, f"dtt-long-delay:{self.node_id}:{self.sequence}")


def _factory(node_id: str, clock: VirtualClock) -> EnvelopeFactory:
    return EnvelopeFactory(
        node_id,
        clock,
        boot_id=uuid5(NAMESPACE_URL, f"dtt-long-delay-boot:{node_id}"),
        uuid_factory=StableUuidFactory(node_id),
    )


@dataclass
class DomainHarness:
    """Small virtual relay around the production M1 service implementations."""

    data_dir: Path
    profile: FaultProfile
    configured_one_way_delay: float
    clock: VirtualClock = dataclass_field(default_factory=VirtualClock)
    link: DeterministicLink = dataclass_field(init=False)
    mission_store: NodeStore = dataclass_field(init=False)
    field_store: NodeStore = dataclass_field(init=False)
    robot_store: NodeStore = dataclass_field(init=False)
    mission: MissionService = dataclass_field(init=False)
    field: FieldService = dataclass_field(init=False)
    robot: DummyRobotService = dataclass_field(init=False)
    _factories: dict[str, EnvelopeFactory] = dataclass_field(init=False)
    delivery_log: list[dict[str, object]] = dataclass_field(default_factory=list)
    send_log: list[dict[str, object]] = dataclass_field(default_factory=list)

    def __post_init__(self) -> None:
        self.link = DeterministicLink(self.profile, wall_epoch=EPOCH)
        self._factories = {
            node_id: _factory(node_id, self.clock)
            for node_id in ("mission-1", "field-1", "dummy-robot-1")
        }
        self._open_mission()
        self._open_field()
        self._open_robot()

    def _open_mission(self) -> None:
        self.mission_store = NodeStore(self.data_dir / "mission.sqlite3")
        self.mission = MissionService(
            self.mission_store,
            self._factories["mission-1"],
            configured_one_way_delay=self.configured_one_way_delay,
        )

    def _open_field(self) -> None:
        self.field_store = NodeStore(self.data_dir / "field.sqlite3")
        self.field = FieldService(self.field_store, self._factories["field-1"])

    def _open_robot(self) -> None:
        self.robot_store = NodeStore(self.data_dir / "robot.sqlite3")
        self.robot = DummyRobotService(
            self.robot_store,
            self._factories["dummy-robot-1"],
            phase_duration=0.0,
        )

    def close(self) -> None:
        self.robot_store.close()
        self.field_store.close()
        self.mission_store.close()

    async def restart(self, node: Literal["mission", "field"]) -> None:
        """Reopen one real SQLite endpoint, then run its ordinary recovery pass."""

        if node == "mission":
            self.mission_store.close()
            self._open_mission()
            await self.mission.recover()
            return
        self.field_store.close()
        self._open_field()
        await self.field.recover()

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _next_outbox_attempt(self) -> float | None:
        """Return the next real outbox eligibility instant, without jumping past expiry."""

        candidates: list[float] = []
        now = self.clock.now()
        for store in (self.mission_store, self.field_store, self.robot_store):
            envelopes = {envelope.message_id: envelope for envelope in store.outbox_messages()}
            for row in store.inspect_outbox():
                if row["ack_state"] != "PENDING":
                    continue
                envelope = envelopes[UUID(row["message_id"])]
                next_attempt = self._parse_time(row["next_attempt_at"])
                if next_attempt is None:
                    next_attempt = envelope.created_at
                eligible_at = max(next_attempt, envelope.created_at)
                if envelope.not_before is not None:
                    eligible_at = max(eligible_at, envelope.not_before)
                expires_at = self._parse_time(row["expires_at"])
                if expires_at is not None and eligible_at >= expires_at:
                    # A retry horizon after expiry must never make the virtual clock leap
                    # past the expiry just to attempt an already-dead envelope.
                    continue
                candidates.append(max(now, eligible_at).timestamp() - EPOCH.timestamp())
        return min(candidates) if candidates else None

    def _pending_due(self, store: NodeStore) -> list[MessageEnvelope]:
        """Read and compare timestamps in Python before handing a message to an adapter."""

        now = self.clock.now()
        envelopes = {envelope.message_id: envelope for envelope in store.outbox_messages()}
        pending: list[MessageEnvelope] = []
        for row in store.inspect_outbox():
            if row["ack_state"] != "PENDING":
                continue
            envelope = envelopes[UUID(row["message_id"])]
            next_attempt = self._parse_time(row["next_attempt_at"]) or envelope.created_at
            eligible_at = max(next_attempt, envelope.created_at)
            if envelope.not_before is not None:
                eligible_at = max(eligible_at, envelope.not_before)
            expires_at = self._parse_time(row["expires_at"])
            if eligible_at <= now and (expires_at is None or now < expires_at):
                pending.append(envelope)
        return sorted(pending, key=lambda item: (item.created_at, item.message_id))

    def advance_to_next_work(self) -> bool:
        """Advance only to the next link delivery or genuinely eligible outbox send."""

        next_delivery = self.link.status(now=self.clock.seconds)["next_delivery_time"]
        next_outbox = self._next_outbox_attempt()
        future_work = [
            instant
            for instant in (next_delivery, next_outbox)
            if instant is not None and instant > self.clock.seconds
        ]
        if not future_work:
            return False
        self.clock.advance_to(min(future_work))
        return True

    def _record_send(self, source: str, envelope: MessageEnvelope) -> None:
        now = self.clock.now()
        assert now >= envelope.created_at
        if envelope.not_before is not None:
            assert now >= envelope.not_before
        self.send_log.append(
            {
                "source": source,
                "send_time": self.clock.seconds,
                "envelope": envelope,
            }
        )

    async def submit_pending_link(self, source: Literal["mission", "field"]) -> bool:
        if source == "mission":
            service = self.mission
            destination = "field-1"
        else:
            service = self.field
            destination = "mission-1"
        submitted = False
        for envelope in self._pending_due(service.store):
            if envelope.destination_id != destination:
                continue
            self._record_send(source, envelope)
            service.store.record_attempt(
                envelope.message_id,
                next_attempt_at=self.clock.now() + timedelta(seconds=RETRY_HORIZON_SECONDS),
            )
            self.link.submit(
                source,
                LinkFrame.for_envelope(envelope),
                now=self.clock.seconds,
            )
            submitted = True
        return submitted

    async def deliver_link_due(self) -> bool:
        scheduled = self.link.deliver_due(now=self.clock.seconds)
        for item in scheduled:
            # Normally the scheduled time is the current time after the runner advances
            # to it.  Keeping a later caller-supplied time models a durable frame that was
            # delivered to Field after its transport schedule but before handling.
            self.clock.advance_to(max(self.clock.seconds, item.delivery_time))
            self.delivery_log.append(
                {
                    "delivery_time": item.delivery_time,
                    "received_at": self.clock.seconds,
                    "destination": item.destination,
                    "frame": item.frame,
                }
            )
            if item.frame.kind == "ack":
                message_id = item.frame.acknowledged_message_id
                assert message_id is not None
                target_store = (
                    self.mission_store if item.destination == "mission" else self.field_store
                )
                target_store.acknowledge(message_id, acked_at=self.clock.now())
                continue

            envelope = item.frame.envelope
            assert envelope is not None
            received_at = self.clock.now()
            assert received_at >= envelope.created_at
            if envelope.not_before is not None:
                assert received_at >= envelope.not_before
            if item.destination == "field":
                target = self.field
                target_store = self.field_store
                ack_source: Literal["mission", "field"] = "field"
            else:
                target = self.mission
                target_store = self.mission_store
                ack_source = "mission"
            is_new = target_store.receive(envelope, received_at=self.clock.now())
            # ACK follows the durable inbox write, even for a duplicate envelope.
            self.link.submit(
                ack_source,
                LinkFrame.for_ack(envelope.message_id),
                now=self.clock.seconds,
            )
            if is_new:
                await target.handle(envelope)
        return bool(scheduled)

    async def deliver_field_to_robot(self) -> bool:
        delivered = False
        for envelope in self._pending_due(self.field_store):
            if envelope.destination_id != "dummy-robot-1":
                continue
            self._record_send("field->robot", envelope)
            self.field_store.record_attempt(
                envelope.message_id,
                next_attempt_at=self.clock.now() + timedelta(seconds=RETRY_HORIZON_SECONDS),
            )
            is_new = self.robot_store.receive(envelope, received_at=self.clock.now())
            # The direct adapter has the same durable-receive-before-ACK ordering as the
            # executable node path.  Robot processing may then be resumed independently.
            self.field_store.acknowledge(envelope.message_id, acked_at=self.clock.now())
            if is_new:
                await self.robot.handle(envelope)
            delivered = True
        return delivered

    async def deliver_robot_to_field(self) -> bool:
        delivered = False
        for envelope in self._pending_due(self.robot_store):
            if envelope.destination_id != "field-1":
                continue
            self._record_send("robot->field", envelope)
            self.robot_store.record_attempt(
                envelope.message_id,
                next_attempt_at=self.clock.now() + timedelta(seconds=RETRY_HORIZON_SECONDS),
            )
            is_new = self.field_store.receive(envelope, received_at=self.clock.now())
            self.robot_store.acknowledge(envelope.message_id, acked_at=self.clock.now())
            if is_new:
                await self.field.handle(envelope)
            delivered = True
        return delivered

    async def drain_field_robot(self) -> bool:
        progressed = False
        for _ in range(16):
            pass_progress = False
            if await self.deliver_field_to_robot():
                pass_progress = True
            if await self.deliver_robot_to_field():
                pass_progress = True
            progressed |= pass_progress
            if not pass_progress:
                break
        return progressed

    async def settle(self, *, before_return: object | None = None) -> None:
        """Run until all due domain work and link frames have converged."""

        checkpoint_called = False
        for _ in range(256):
            progressed = await self.submit_pending_link("mission")
            if await self.deliver_link_due():
                progressed = True
            progressed |= await self.drain_field_robot()
            if before_return is not None and not checkpoint_called and self.robot.effect_counter:
                assert callable(before_return)
                before_return()
                checkpoint_called = True
            progressed |= await self.submit_pending_link("field")
            if await self.deliver_link_due():
                progressed = True

            next_delivery = self.link.status(now=self.clock.seconds)["next_delivery_time"]
            if self.advance_to_next_work():
                progressed = True
            if not progressed and next_delivery is None:
                return
        raise AssertionError("virtual domain did not converge within 256 scheduler turns")

    def pending_field_to_mission(self) -> list[MessageEnvelope]:
        return [
            envelope
            for envelope in self._pending_due(self.field_store)
            if envelope.destination_id == "mission-1"
        ]


def _run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize("one_way_delay", [0.0, 30.0, 900.0, 1200.0])
def test_nominal_long_delay_domain_reconciles_after_return(
    tmp_path: Path, one_way_delay: float
) -> None:
    async def scenario() -> None:
        profile = FaultProfile(one_way_delay_seconds=one_way_delay)
        ttl_seconds = 2 * one_way_delay + 120.0
        harness = DomainHarness(tmp_path, profile, one_way_delay)
        try:
            intent = harness.mission.submit_press_button(expires_in_seconds=ttl_seconds)
            checkpoints: list[float] = []

            def assert_effect_precedes_mission_return() -> None:
                checkpoints.append(harness.clock.seconds)
                assert harness.robot.effect_counter == 1
                assert harness.mission.view()["terminal_state"] is None

            await harness.settle(before_return=assert_effect_precedes_mission_return)

            view = harness.mission.view()
            assert len(checkpoints) == 1
            assert checkpoints[0] >= one_way_delay
            assert checkpoints[0] < one_way_delay + 0.01
            assert view["operation_id"] == str(intent.payload.operation_id)
            assert view["terminal_state"] == ContractState.SUCCEEDED.value
            assert harness.robot.effect_counter == 1
            journal = harness.robot_store.inspect_execution_journal()
            assert len(journal) == 1
            assert journal[0]["state"] == ContractState.SUCCEEDED.value
            assert len(harness.field_store.inspect_execution_journal()) == 0

            first_delivery = next(
                record
                for record in harness.delivery_log
                if record["frame"].kind == "envelope"
                and record["frame"].envelope.message_id == intent.message_id
            )
            first_send = next(
                record
                for record in harness.send_log
                if record["envelope"].message_id == intent.message_id
            )
            assert first_delivery["delivery_time"] >= (
                first_send["send_time"] + profile.one_way_delay_seconds
            )
            assert first_delivery["received_at"] >= first_delivery["delivery_time"]
            terminal_delivery = next(
                record
                for record in harness.delivery_log
                if (
                    record["destination"] == "mission"
                    and record["frame"].kind == "envelope"
                    and isinstance(record["frame"].envelope.payload, ExecutionEvent)
                    and record["frame"].envelope.payload.next_state is ContractState.SUCCEEDED
                )
            )
            terminal_send = next(
                record
                for record in harness.send_log
                if (
                    isinstance(record["envelope"].payload, ExecutionEvent)
                    and record["envelope"].payload.next_state is ContractState.SUCCEEDED
                )
            )
            assert terminal_delivery["delivery_time"] >= (
                terminal_send["send_time"] + profile.one_way_delay_seconds
            )
            assert terminal_delivery["delivery_time"] >= 2 * one_way_delay
            for record in harness.send_log:
                sent_at = EPOCH + timedelta(seconds=record["send_time"])
                assert sent_at >= record["envelope"].created_at
                if record["envelope"].not_before is not None:
                    assert sent_at >= record["envelope"].not_before
            send_times = {
                record["envelope"].message_id: record["send_time"]
                for record in harness.send_log
            }
            for record in harness.delivery_log:
                frame = record["frame"]
                if frame.kind != "envelope":
                    continue
                envelope = frame.envelope
                assert envelope is not None
                received_at = EPOCH + timedelta(seconds=record["received_at"])
                assert received_at >= envelope.created_at
                if envelope.not_before is not None:
                    assert received_at >= envelope.not_before
                assert record["delivery_time"] >= (
                    send_times[envelope.message_id] + profile.one_way_delay_seconds
                )
        finally:
            harness.close()

    _run(scenario())


@pytest.mark.parametrize("restart_node", ["mission", "field"])
def test_duplicate_deliveries_and_restart_reconcile_one_effect(
    tmp_path: Path, restart_node: Literal["mission", "field"]
) -> None:
    async def scenario() -> None:
        delay = 30.0
        harness = DomainHarness(
            tmp_path,
            FaultProfile(one_way_delay_seconds=delay, duplicate_probability=1.0, seed=7),
            delay,
        )
        try:
            intent = harness.mission.submit_press_button(expires_in_seconds=2 * delay + 120.0)
            assert harness.advance_to_next_work()
            assert await harness.submit_pending_link("mission")
            assert harness.advance_to_next_work()
            assert await harness.deliver_link_due()

            # The intent has been durably admitted before the selected endpoint is reopened.
            assert any(
                envelope.message_id == intent.message_id
                for envelope in harness.field_store.inbox_messages()
            )
            await harness.restart(restart_node)
            await harness.settle()

            assert harness.robot.effect_counter == 1
            assert harness.mission.view()["terminal_state"] == ContractState.SUCCEEDED.value
            assert len(
                [
                    envelope
                    for envelope in harness.field_store.inbox_messages()
                    if envelope.message_id == intent.message_id
                ]
            ) == 1
            assert len(harness.robot_store.inspect_execution_journal()) == 1
            assert harness.link.metrics["duplicates_injected"] > 0
            assert harness.link.metrics["delivered"] > harness.link.metrics["submitted"] // 2
        finally:
            harness.close()

    _run(scenario())


def test_transport_expiry_with_sixty_second_ttl_never_admits_or_fakes_success(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        delay = 900.0
        harness = DomainHarness(
            tmp_path,
            FaultProfile(one_way_delay_seconds=delay),
            delay,
        )
        try:
            intent = harness.mission.submit_press_button(expires_in_seconds=60.0)
            assert harness.advance_to_next_work()
            assert await harness.submit_pending_link("mission")
            assert harness.link.metrics["expired"] == 1
            assert harness.link.status(now=harness.clock.seconds)["queued_deliveries"] == 0
            assert harness.field_store.inbox_messages() == []
            assert harness.robot.effect_counter == 0
            assert harness.mission.view()["terminal_state"] is None
            assert [
                row
                for row in harness.mission_store.inspect_outbox()
                if row["message_id"] == str(intent.message_id)
                and row["ack_state"] == "PENDING"
            ]
        finally:
            harness.close()

    _run(scenario())


def test_expired_envelope_already_durable_is_held_by_field_at_processing_boundary(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = DomainHarness(
            tmp_path,
            FaultProfile(one_way_delay_seconds=0.0),
            configured_one_way_delay=0.0,
        )
        try:
            intent = harness.mission.submit_press_button(expires_in_seconds=60.0)
            assert harness.advance_to_next_work()
            assert await harness.submit_pending_link("mission")
            assert harness.link.metrics["expired"] == 0
            assert intent.expires_at is not None
            harness.clock.advance_to((intent.expires_at - EPOCH).total_seconds())
            assert await harness.deliver_link_due()

            assert harness.robot.effect_counter == 0
            held_events = [
                envelope.payload
                for envelope in harness.field_store.outbox_messages()
                if isinstance(envelope.payload, ExecutionEvent)
            ]
            assert len(held_events) == 1
            assert held_events[0].previous_state is ContractState.RECEIVED
            assert held_events[0].next_state is ContractState.HELD
            assert harness.field_store.pending_outbox(now=harness.clock.now())
            assert not any(
                envelope.destination_id == "dummy-robot-1"
                for envelope in harness.field_store.outbox_messages()
            )
        finally:
            harness.close()

    _run(scenario())


def test_fifteen_minute_blackout_is_distinct_from_one_way_delay(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        profile = FaultProfile(
            one_way_delay_seconds=0.0,
            blackout_intervals=((60.0, 960.0),),
        )
        harness = DomainHarness(tmp_path, profile, configured_one_way_delay=0.0)
        try:
            harness.clock.advance_to(60.0)
            intent = harness.mission.submit_press_button(expires_in_seconds=2_000.0)
            assert harness.advance_to_next_work()
            await harness.settle()

            assert harness.robot.effect_counter == 1
            assert harness.mission.view()["terminal_state"] == ContractState.SUCCEEDED.value
            assert harness.link.metrics["blackout_deferrals"] >= 1
            assert profile.one_way_delay_seconds == 0.0
            assert profile.blackout_intervals == ((60.0, 960.0),)
            intent_delivery = next(
                record
                for record in harness.delivery_log
                if record["frame"].kind == "envelope"
                and record["frame"].envelope.message_id == intent.message_id
            )
            assert intent_delivery["delivery_time"] == pytest.approx(960.0)
        finally:
            harness.close()

    _run(scenario())
