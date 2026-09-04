"""Virtual-time proofs for an external effect across long asymmetric links.

The harness deliberately reuses the M1 domain harness.  Only the Mission/Field
link is split into two deterministic directional links; Field/Robot still uses
the durable direct service path from the existing harness.  The external device
is a separate non-idempotent JSON-lines journal, so a second ``press`` is an
observable failure even when the Robot SQLite store is reopened.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest
from deferred_teleop.external_effect import (
    ExternalEffectObservation,
    ExternalOutcome,
    PersistentDummyExternalEffect,
)
from deferred_teleop.link import DeterministicLink, FaultProfile, LinkFrame, ScheduledFrame
from deferred_teleop.protocol import (
    ContractState,
    ExecutionContract,
    ExecutionEvent,
    MessageEnvelope,
    OperationIntent,
    SiteSnapshot,
)
from deferred_teleop.runtime import DummyRobotService
from deferred_teleop.storage import NodeStore
from test_long_delay_domain import EPOCH, RETRY_HORIZON_SECONDS, DomainHarness, VirtualClock

AdapterFactory = Callable[[Path, VirtualClock], PersistentDummyExternalEffect]


class CrashAfterExternalPress(PersistentDummyExternalEffect):
    """Persist one physical pulse, then interrupt the Robot before its commit."""

    def __init__(self, path: str | Path, **kwargs: object) -> None:
        super().__init__(path, **kwargs)
        self._crash = True

    def press(self, effect_key: str) -> ExternalEffectObservation:
        observation = super().press(effect_key)
        if self._crash:
            self._crash = False
            raise RuntimeError("injected crash after external press")
        return observation


def _persistent_adapter(path: Path, clock: VirtualClock) -> PersistentDummyExternalEffect:
    return PersistentDummyExternalEffect(path, clock=clock)


class ExternalDelayHarness(DomainHarness):
    """The previous domain harness with real directional delay and external I/O."""

    def __init__(
        self,
        data_dir: Path,
        *,
        forward_delay: float,
        return_delay: float,
        forward_profile: FaultProfile | None = None,
        return_profile: FaultProfile | None = None,
        adapter_factory: AdapterFactory = _persistent_adapter,
        clock: VirtualClock | None = None,
        hold_field_intent: bool = False,
    ) -> None:
        self.forward_delay = forward_delay
        self.return_delay = return_delay
        self._return_profile = return_profile or FaultProfile(
            one_way_delay_seconds=return_delay
        )
        self._adapter_factory = adapter_factory
        self.external_path = data_dir / "external-device.jsonl"
        self._hold_field_intent = hold_field_intent
        self._held_field_intents: list[MessageEnvelope] = []
        super().__init__(
            data_dir,
            forward_profile or FaultProfile(one_way_delay_seconds=forward_delay),
            configured_one_way_delay=forward_delay,
            clock=clock or VirtualClock(),
        )
        # DomainHarness constructs one link before opening the services.  Replace
        # that in-memory scheduler with the two physical directions after the
        # services have been opened; all stores and factories remain shared.
        self.outbound_link = DeterministicLink(self.profile, wall_epoch=EPOCH)
        self.return_link = DeterministicLink(self._return_profile, wall_epoch=EPOCH)
        self.link = self.outbound_link

    def _open_robot(self) -> None:
        self.robot_store = NodeStore(self.data_dir / "robot.sqlite3")
        adapter = self._adapter_factory(self.external_path, self.clock)
        self.external_adapter = adapter
        self.robot = DummyRobotService(
            self.robot_store,
            self._factories["dummy-robot-1"],
            phase_duration=0.0,
            external_effect_adapter=adapter,
        )

    async def reopen_robot(
        self, adapter_factory: AdapterFactory, *, recover: bool = True
    ) -> None:
        """Reopen only Robot SQLite and its independently persisted device."""

        self.robot_store.close()
        self._adapter_factory = adapter_factory
        self._open_robot()
        if recover:
            await self.robot.recover()

    def _link_for_source(self, source: Literal["mission", "field"]) -> DeterministicLink:
        return self.outbound_link if source == "mission" else self.return_link

    async def submit_pending_link(self, source: Literal["mission", "field"]) -> bool:
        if source == "mission":
            service = self.mission
            destination = "field-1"
            source_label = "mission->outbound"
        else:
            service = self.field
            destination = "mission-1"
            source_label = "field->return"
        submitted = False
        link = self._link_for_source(source)
        for envelope in self._pending_due(service.store):
            if envelope.destination_id != destination:
                continue
            self._record_send(source_label, envelope)
            service.store.record_attempt(
                envelope.message_id,
                next_attempt_at=self.clock.now() + timedelta(seconds=RETRY_HORIZON_SECONDS),
            )
            link.submit(source, LinkFrame.for_envelope(envelope), now=self.clock.seconds)
            submitted = True
        return submitted

    async def deliver_link_due(self) -> bool:
        due: list[tuple[ScheduledFrame, str]] = []
        for link_name, link in (("outbound", self.outbound_link), ("return", self.return_link)):
            due.extend((item, link_name) for item in link.deliver_due(now=self.clock.seconds))
        # Each direction has its own sequence.  The direction rank makes equal
        # virtual timestamps reproducible without inventing a future timestamp.
        due.sort(
            key=lambda entry: (
                entry[0].delivery_time,
                entry[0].priority,
                entry[1],
                entry[0].sequence,
            )
        )

        for item, link_name in due:
            self.clock.advance_to(max(self.clock.seconds, item.delivery_time))
            self.delivery_log.append(
                {
                    "delivery_time": item.delivery_time,
                    "received_at": self.clock.seconds,
                    "destination": item.destination,
                    "link": link_name,
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
                ack_link = self.return_link
            else:
                target = self.mission
                target_store = self.mission_store
                ack_source = "mission"
                ack_link = self.outbound_link
            is_new = target_store.receive(envelope, received_at=received_at)
            # ACK follows the durable inbox write and travels over the opposite
            # physical direction, including for a duplicate envelope.
            ack_link.submit(
                ack_source,
                LinkFrame.for_ack(envelope.message_id),
                now=self.clock.seconds,
            )
            if is_new:
                if (
                    self._hold_field_intent
                    and item.destination == "field"
                    and isinstance(envelope.payload, OperationIntent)
                ):
                    self._held_field_intents.append(envelope)
                else:
                    await target.handle(envelope)
        return bool(due)

    def advance_to_next_work(self) -> bool:
        next_deliveries = [
            link.status(now=self.clock.seconds)["next_delivery_time"]
            for link in (self.outbound_link, self.return_link)
        ]
        next_delivery = min((value for value in next_deliveries if value is not None), default=None)
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

    async def release_field_intents(self) -> None:
        held = tuple(self._held_field_intents)
        self._held_field_intents.clear()
        for envelope in held:
            await self.field.handle(envelope)

    async def settle(self, *, before_return: object | None = None) -> None:
        """Converge all due domain work while advancing only real deadlines."""

        checkpoint_called = False
        for _ in range(256):
            progressed = False
            if await self.submit_pending_link("mission"):
                progressed = True
            if await self.submit_pending_link("field"):
                progressed = True
            if await self.deliver_link_due():
                progressed = True
            if await self.drain_field_robot():
                progressed = True
            if (
                before_return is not None
                and not checkpoint_called
                and self.external_adapter.press_count
            ):
                assert callable(before_return)
                before_return()
                checkpoint_called = True
            if await self.submit_pending_link("mission"):
                progressed = True
            if await self.submit_pending_link("field"):
                progressed = True
            if await self.deliver_link_due():
                progressed = True
            if await self.drain_field_robot():
                progressed = True

            next_deliveries = [
                link.status(now=self.clock.seconds)["next_delivery_time"]
                for link in (self.outbound_link, self.return_link)
            ]
            next_delivery = min(
                (value for value in next_deliveries if value is not None), default=None
            )
            next_outbox = self._next_outbox_attempt()
            if self.advance_to_next_work():
                progressed = True
            if not progressed and next_delivery is None and next_outbox is None:
                return
        raise AssertionError("external long-delay domain did not converge")


def _run(coroutine):
    return asyncio.run(coroutine)


def _assert_virtual_timing(harness: ExternalDelayHarness) -> None:
    """Check send/receive/not-before/expiry against the same virtual clock."""

    link_sources = {"outbound": "mission->outbound", "return": "field->return"}
    link_delays = {"outbound": harness.forward_delay, "return": harness.return_delay}
    for record in harness.send_log:
        envelope = record["envelope"]
        assert isinstance(envelope, MessageEnvelope)
        sent_at = EPOCH + timedelta(seconds=float(record["send_time"]))
        assert sent_at >= envelope.created_at
        if envelope.not_before is not None:
            assert sent_at >= envelope.not_before
        if envelope.expires_at is not None:
            assert sent_at < envelope.expires_at

    for record in harness.delivery_log:
        frame = record["frame"]
        if frame.kind != "envelope":
            continue
        envelope = frame.envelope
        assert envelope is not None
        received_at = EPOCH + timedelta(seconds=float(record["received_at"]))
        assert received_at >= envelope.created_at
        if envelope.not_before is not None:
            assert received_at >= envelope.not_before
        if envelope.expires_at is not None:
            assert received_at < envelope.expires_at
        link_name = str(record["link"])
        if link_name not in link_sources:
            continue
        source = link_sources[link_name]
        send = next(
            entry
            for entry in harness.send_log
            if entry["source"] == source
            and entry["envelope"].message_id == envelope.message_id
        )
        expected_delivery = float(send["send_time"]) + link_delays[link_name]
        assert float(record["delivery_time"]) + 1e-9 >= expected_delivery


def _journal_result(harness: ExternalDelayHarness) -> tuple[dict[str, object], dict[str, object]]:
    journal = harness.robot_store.inspect_execution_journal()
    assert len(journal) == 1
    row = journal[0]
    result = json.loads(str(row["terminal_result_json"]))
    assert isinstance(result, dict)
    return row, result


def _write_machine_table(data_dir: Path, row: dict[str, object]) -> None:
    """Export one actual run without committing a random/golden artifact."""

    report = data_dir / "external-effect-long-delay-results.json"
    report.write_text(
        json.dumps(
            {"schema": "dtt.external-effect-long-delay.v1", "rows": [row]},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    encoded = json.loads(report.read_text(encoding="utf-8"))
    assert encoded["schema"] == "dtt.external-effect-long-delay.v1"
    assert len(encoded["rows"]) == 1


@pytest.mark.parametrize(
    ("forward_delay", "return_delay"),
    [(1200.0, 1200.0), (900.0, 1200.0)],
    ids=["symmetric-1200", "asymmetric-900-out-1200-back"],
)
def test_external_effect_long_delay_nominal_reconciles_after_return(
    tmp_path: Path, forward_delay: float, return_delay: float
) -> None:
    async def scenario() -> None:
        harness = ExternalDelayHarness(
            tmp_path,
            forward_delay=forward_delay,
            return_delay=return_delay,
        )
        try:
            ttl_seconds = forward_delay + return_delay + 600.0
            intent = harness.mission.submit_press_button(expires_in_seconds=ttl_seconds)
            checkpoints: list[float] = []

            def assert_before_return() -> None:
                checkpoints.append(harness.clock.seconds)
                assert harness.external_adapter.press_count == 1
                assert harness.robot.effect_counter == 0
                assert harness.mission.view()["terminal_state"] is None

            await harness.settle(before_return=assert_before_return)
            view = harness.mission.view()
            assert checkpoints == [pytest.approx(forward_delay)]
            assert view["operation_id"] == str(intent.payload.operation_id)
            assert view["terminal_state"] == ContractState.SUCCEEDED.value
            assert harness.external_adapter.press_count == 1
            assert harness.robot.effect_counter == 0

            journal, result = _journal_result(harness)
            effect_key = f"press:{intent.payload.operation_id}:1"
            assert journal["contract_revision"] == 1
            assert journal["state"] == ContractState.SUCCEEDED.value
            assert journal["effect_count"] == 0
            assert result["effect_key"] == effect_key
            assert result["device_id"] == "dummy-external-button-1"
            assert result["external_outcome"] == ExternalOutcome.APPLIED.value
            assert isinstance(result["observation_id"], str)
            records = harness.external_adapter.records
            assert len(records) == 1
            assert records[0]["effect_key"] == effect_key
            assert records[0]["device_id"] == result["device_id"]

            # External resolution has no robot telemetry.  Field forwards its
            # initial measured snapshot, but never invents a completion snapshot.
            snapshots = [
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, SiteSnapshot)
            ]
            assert len(snapshots) == 1
            assert snapshots[0].payload.evidence.world_revision == 1

            terminal_send = next(
                record
                for record in harness.send_log
                if record["source"] == "field->return"
                and isinstance(record["envelope"].payload, ExecutionEvent)
                and record["envelope"].payload.next_state is ContractState.SUCCEEDED
            )
            terminal_delivery = next(
                record
                for record in harness.delivery_log
                if record["destination"] == "mission"
                and record["frame"].kind == "envelope"
                and isinstance(record["frame"].envelope.payload, ExecutionEvent)
                and record["frame"].envelope.payload.next_state is ContractState.SUCCEEDED
            )
            assert terminal_send["send_time"] == pytest.approx(forward_delay)
            assert terminal_delivery["delivery_time"] == pytest.approx(
                forward_delay + return_delay
            )
            _assert_virtual_timing(harness)
            _write_machine_table(
                tmp_path,
                {
                    "scenario": "nominal",
                    "forward_delay_seconds": forward_delay,
                    "return_delay_seconds": return_delay,
                    "ttl_seconds": ttl_seconds,
                    "effect_pulses": harness.external_adapter.press_count,
                    "terminal_state": view["terminal_state"],
                    "virtual_seconds": harness.clock.seconds,
                },
            )
        finally:
            harness.close()

    _run(scenario())


@pytest.mark.parametrize(
    ("recovery_outcome", "expected_state"),
    [
        (ExternalOutcome.APPLIED, ContractState.SUCCEEDED),
        (ExternalOutcome.UNKNOWN, ContractState.HELD),
    ],
    ids=["recovery-applied", "recovery-unknown-held"],
)
def test_external_effect_crash_duplicate_contract_reopens_without_second_pulse(
    tmp_path: Path, recovery_outcome: ExternalOutcome, expected_state: ContractState
) -> None:
    async def scenario() -> None:
        harness = ExternalDelayHarness(
            tmp_path,
            forward_delay=900.0,
            return_delay=1200.0,
            adapter_factory=lambda path, clock: CrashAfterExternalPress(path, clock=clock),
        )
        try:
            intent = harness.mission.submit_press_button(expires_in_seconds=3000.0)
            with pytest.raises(RuntimeError, match="after external press"):
                await harness.settle()

            contract = next(
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
            )
            assert harness.external_adapter.press_count == 1
            assert harness.robot.effect_counter == 0
            assert harness.mission.view()["terminal_state"] is None
            assert harness.robot_store.inspect_inbox()[-1]["processing_state"] == "PROCESSING"

            def reopened_adapter(path: Path, clock: VirtualClock) -> PersistentDummyExternalEffect:
                return PersistentDummyExternalEffect(
                    path,
                    clock=clock,
                    observation_outcome=recovery_outcome,
                )

            await harness.reopen_robot(reopened_adapter, recover=False)
            # The same contract is received again while the interrupted inbox
            # row is still durable.  NodeStore deduplicates it by message_id.
            assert harness.robot_store.receive(contract, received_at=harness.clock.now()) is False
            assert await harness.robot.recover() == 1
            assert harness.external_adapter.press_count == 1
            assert harness.robot.effect_counter == 0

            await harness.settle()
            view = harness.mission.view()
            assert view["operation_id"] == str(intent.payload.operation_id)
            assert view["terminal_state"] == expected_state.value
            journal, result = _journal_result(harness)
            effect_key = f"press:{intent.payload.operation_id}:1"
            assert journal["contract_revision"] == 1
            assert journal["state"] == expected_state.value
            assert journal["effect_count"] == 0
            assert result["effect_key"] == effect_key
            assert result["device_id"] == "dummy-external-button-1"
            assert result["external_outcome"] == recovery_outcome.value
            assert len(harness.external_adapter.records) == 1
            assert harness.external_adapter.records[0]["effect_key"] == effect_key
            assert harness.external_adapter.records[0]["device_id"] == result["device_id"]

            terminal_states = {
                message.payload.next_state
                for message in harness.mission_store.inbox_messages()
                if isinstance(message.payload, ExecutionEvent)
            }
            if expected_state is ContractState.HELD:
                assert ContractState.SUCCEEDED not in terminal_states
            assert expected_state in terminal_states
            snapshots = [
                message
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, SiteSnapshot)
            ]
            assert len(snapshots) == 1
            assert snapshots[0].payload.evidence.world_revision == 1
            _assert_virtual_timing(harness)
        finally:
            harness.close()

    _run(scenario())


def test_external_effect_transport_expiry_before_field_admission_has_zero_pulses(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = ExternalDelayHarness(
            tmp_path,
            forward_delay=900.0,
            return_delay=1200.0,
        )
        try:
            intent = harness.mission.submit_press_button(expires_in_seconds=60.0)
            await harness.settle()
            assert harness.outbound_link.metrics["expired"] == 1
            assert harness.outbound_link.status(now=harness.clock.seconds)["queued_deliveries"] == 0
            assert harness.field_store.inbox_messages() == []
            assert harness.external_adapter.press_count == 0
            assert harness.robot.effect_counter == 0
            assert harness.mission.view()["terminal_state"] is None
            pending = [
                row
                for row in harness.mission_store.inspect_outbox()
                if row["message_id"] == str(intent.message_id)
            ]
            assert len(pending) == 1
            assert pending[0]["ack_state"] == "PENDING"
        finally:
            harness.close()

    _run(scenario())


def test_external_effect_field_inbox_queue_expires_without_invented_completion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = ExternalDelayHarness(
            tmp_path,
            forward_delay=0.0,
            return_delay=1200.0,
            hold_field_intent=True,
        )
        try:
            intent = harness.mission.submit_press_button(expires_in_seconds=60.0)
            # EnvelopeFactory preserves a monotonic microsecond boundary after
            # the epoch; advance to that real eligibility instant before sending.
            assert harness.advance_to_next_work()
            assert await harness.submit_pending_link("mission")
            assert await harness.deliver_link_due()
            assert harness.clock.seconds > 0.0
            assert harness.clock.seconds < 0.001
            assert len(harness._held_field_intents) == 1
            field_row = next(
                row
                for row in harness.field_store.inspect_inbox()
                if row["message_id"] == str(intent.message_id)
            )
            assert field_row["processing_state"] == "RECEIVED"
            assert harness.external_adapter.press_count == 0
            assert harness.field_store.outbox_messages() == []

            assert intent.expires_at is not None
            harness.clock.advance_to((intent.expires_at - EPOCH).total_seconds())
            await harness.release_field_intents()
            assert harness.external_adapter.press_count == 0
            held_events = [
                message.payload
                for message in harness.field_store.outbox_messages()
                if isinstance(message.payload, ExecutionEvent)
            ]
            assert len(held_events) == 1
            assert held_events[0].next_state is ContractState.HELD
            assert not any(
                isinstance(message.payload, SiteSnapshot)
                for message in harness.field_store.outbox_messages()
            )

            await harness.settle()
            assert harness.external_adapter.press_count == 0
            assert harness.robot.effect_counter == 0
            assert harness.mission.view()["terminal_state"] == ContractState.HELD.value
            _assert_virtual_timing(harness)
        finally:
            harness.close()

    _run(scenario())
