"""M1 delayed-dummy domain services shared by the executable node processes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from deferred_teleop.protocol import (
    ApprovalPolicy,
    ContractState,
    EntitySelector,
    EvidenceMetadata,
    ExecutionContract,
    ExecutionEvent,
    GroundedOperation,
    MessageEnvelope,
    OperationIntent,
    OperationPlan,
    OperationState,
    OperationType,
    Pose,
    PredictionManifest,
    ProvenanceKind,
    Quaternion,
    RobotForecast,
    RobotState,
    SiteSnapshot,
    SpatialFrame,
    TaskAssignment,
    TaskNode,
    Vector3,
    WireModel,
)
from deferred_teleop.storage import NodeStore

TERMINAL_STATES = frozenset(
    {
        ContractState.SUCCEEDED,
        ContractState.FAILED,
        ContractState.HELD,
        ContractState.CANCELLED,
    }
)
DUMMY_PHASES = (
    "VALIDATING",
    "APPROACHING",
    "CONTACTING",
    "VERIFYING_EFFECT",
    "RETRACTING",
    "SUCCEEDED",
)


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)


EventSink = Callable[[str, Mapping[str, Any]], None]


def _ignore_event(_event: str, _fields: Mapping[str, Any]) -> None:
    return None


@dataclass
class EnvelopeFactory:
    node_id: str
    clock: Clock
    boot_id: UUID = field(default_factory=uuid4)
    uuid_factory: Callable[[], UUID] = uuid4
    _sequence: int = 0

    def make(
        self,
        message_type: str,
        destination_id: str,
        correlation_id: UUID,
        payload: WireModel,
        *,
        causation_id: UUID | None = None,
        expires_at: datetime | None = None,
        message_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> MessageEnvelope:
        self._sequence += 1
        timestamp = created_at or self.clock.now() + timedelta(microseconds=self._sequence)
        return MessageEnvelope(
            message_id=message_id or self.uuid_factory(),
            message_type=message_type,
            source_id=self.node_id,
            source_boot_id=self.boot_id,
            source_sequence=self._sequence,
            destination_id=destination_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            created_at=timestamp,
            expires_at=expires_at,
            payload=payload,
        )


def dummy_pose(*, pressed: bool = False) -> Pose:
    return Pose(
        position=Vector3(x=0.4, y=0.1, z=0.18 if pressed else 0.2),
        orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
        frame=SpatialFrame(frame_id="field-world", calibration_version="dummy-cal-1"),
    )


def evidence(
    source_id: str,
    now: datetime,
    provenance: ProvenanceKind,
    *,
    world_revision: int,
    fresh_for_seconds: float = 30.0,
) -> EvidenceMetadata:
    return EvidenceMetadata(
        source_ids=(source_id,),
        observed_at=now,
        produced_at=now,
        provenance=provenance,
        world_revision=world_revision,
        fresh_until=now + timedelta(seconds=fresh_for_seconds),
        model_version="dummy-constant-velocity-v1"
        if provenance in {ProvenanceKind.PREDICTED, ProvenanceKind.SIMULATED}
        else None,
    )


class RuntimeService:
    def __init__(
        self,
        store: NodeStore,
        factory: EnvelopeFactory,
        *,
        emit: EventSink = _ignore_event,
    ) -> None:
        self.store = store
        self.factory = factory
        self.clock = factory.clock
        self.emit = emit

    async def handle(self, envelope: MessageEnvelope) -> None:
        claimed = self.store.claim_inbox(envelope.message_id)
        if claimed is not None:
            await self.process_claimed(claimed)
            # Give one older retryable dependency failure another chance whenever
            # fresh input arrives, without allowing a poison record to busy-loop.
            retry = self.store.claim_next_inbox()
            if retry is not None:
                await self.process_claimed(retry)

    async def recover(self) -> int:
        recovered = self.store.recover_interrupted_processing()
        claimable = sum(
            row["processing_state"] in {"RECEIVED", "FAILED"}
            for row in self.store.inspect_inbox()
        )
        # First prefer never-attempted input, then make one bounded retry pass for
        # dependency failures whose prerequisite may have just been recovered.
        for _ in range(claimable * 2):
            envelope = self.store.claim_next_inbox()
            if envelope is None:
                break
            await self.process_claimed(envelope)
        return recovered

    async def process_claimed(self, envelope: MessageEnvelope) -> None:
        raise NotImplementedError


class MissionService(RuntimeService):
    def __init__(
        self,
        store: NodeStore,
        factory: EnvelopeFactory,
        *,
        configured_one_way_delay: float,
        emit: EventSink = _ignore_event,
    ) -> None:
        super().__init__(store, factory, emit=emit)
        self.configured_one_way_delay = configured_one_way_delay

    def submit_press_button(
        self,
        *,
        entity_id: str = "dummy-button-1",
        executor_id: str = "dummy-robot-1",
        expires_in_seconds: float | None = 60.0,
    ) -> MessageEnvelope:
        operation_id = self.factory.uuid_factory()
        now = self.clock.now()
        intent = self.factory.make(
            "operation.intent",
            "field-1",
            operation_id,
            OperationIntent(
                operation_id=operation_id,
                operation_type=OperationType.PRESS_BUTTON,
                selector=EntitySelector(entity_id=entity_id),
                preferred_executor=executor_id,
                approval_policy=ApprovalPolicy.AUTO_IF_WHITELISTED,
                state=OperationState.SUBMITTED,
            ),
            expires_at=now + timedelta(seconds=expires_in_seconds)
            if expires_in_seconds is not None
            else None,
        )
        self.store.enqueue(intent)
        self.emit(
            "mission.operation_submitted",
            {
                "operation_id": str(operation_id),
                "message_id": str(intent.message_id),
                "estimated_arrival_at": (
                    intent.created_at + timedelta(seconds=self.configured_one_way_delay)
                ).isoformat(),
            },
        )
        return intent

    async def process_claimed(self, envelope: MessageEnvelope) -> None:
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=self.clock.now(),
            handler_result_reference=f"mission-view:{envelope.message_type}",
        )
        fields: dict[str, Any] = {
            "message_id": str(envelope.message_id),
            "message_type": envelope.message_type,
            "correlation_id": str(envelope.correlation_id),
        }
        if isinstance(envelope.payload, ExecutionEvent):
            fields["contract_id"] = str(envelope.payload.contract_id)
            fields["state"] = envelope.payload.next_state.value
        self.emit("mission.confirmed_message", fields)

    def view(self) -> dict[str, Any]:
        inbox = self.store.inbox_messages()
        outbox = self.store.outbox_messages()
        intents = [message for message in outbox if isinstance(message.payload, OperationIntent)]
        snapshots = [message for message in inbox if isinstance(message.payload, SiteSnapshot)]
        forecasts = [message for message in inbox if isinstance(message.payload, RobotForecast)]
        events = [message for message in inbox if isinstance(message.payload, ExecutionEvent)]
        terminal = next(
            (
                message
                for message in reversed(events)
                if message.payload.next_state in TERMINAL_STATES
            ),
            None,
        )
        intent = intents[-1] if intents else None
        forecast = forecasts[-1] if forecasts else None
        estimated_arrival_at = (
            intent.created_at + timedelta(seconds=self.configured_one_way_delay)
            if intent
            else None
        )
        manifest: PredictionManifest | None = None
        if forecast is not None:
            manifest = PredictionManifest(
                manifest_id=uuid5(NAMESPACE_URL, f"dtt-manifest:{forecast.message_id}"),
                site_id="dummy-site-1",
                forecast_ids=(forecast.message_id,),
                generated_for_world_revision=forecast.payload.evidence.world_revision,
                evidence=forecast.payload.evidence,
            )
        return {
            "node_id": self.factory.node_id,
            "operation_id": str(intent.payload.operation_id) if intent else None,
            "correlation_id": str(intent.correlation_id) if intent else None,
            "estimated_arrival_at": estimated_arrival_at.isoformat()
            if estimated_arrival_at
            else None,
            "confirmed_state": snapshots[-1].payload.model_dump(mode="json")
            if snapshots
            else None,
            "arrival_belief": {
                **forecast.payload.model_dump(mode="json"),
                "estimated_intent_arrival_at": estimated_arrival_at.isoformat()
                if estimated_arrival_at
                else None,
                "link_one_way_delay_seconds": self.configured_one_way_delay,
            }
            if forecast
            else None,
            "prediction_manifest": manifest.model_dump(mode="json") if manifest else None,
            "target_branch": {
                "condition": "button effect succeeds",
                "entity_id": intent.payload.selector.entity_id,
                "requested_state": "PRESSED",
                "provenance": ProvenanceKind.OPERATOR_ASSERTED.value,
            }
            if intent
            else None,
            "terminal_state": terminal.payload.next_state.value if terminal else None,
            "terminal_contract_id": str(terminal.payload.contract_id) if terminal else None,
            "received_message_count": len(inbox),
        }


class FieldService(RuntimeService):
    def __init__(
        self,
        store: NodeStore,
        factory: EnvelopeFactory,
        *,
        mission_id: str = "mission-1",
        robot_id: str = "dummy-robot-1",
        emit: EventSink = _ignore_event,
    ) -> None:
        super().__init__(store, factory, emit=emit)
        self.mission_id = mission_id
        self.robot_id = robot_id

    async def process_claimed(self, envelope: MessageEnvelope) -> None:
        if isinstance(envelope.payload, OperationIntent):
            self._process_intent(envelope)
            return
        if isinstance(envelope.payload, (ExecutionEvent, RobotState, RobotForecast)):
            self._process_robot_message(envelope)
            return
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=self.clock.now(),
            handler_result_reference=f"field-ignored:{envelope.message_type}",
        )

    def _process_intent(self, envelope: MessageEnvelope) -> None:
        intent = envelope.payload
        assert isinstance(intent, OperationIntent)
        prior_contract = next(
            (
                message
                for message in self.store.outbox_messages()
                if isinstance(message.payload, ExecutionContract)
                and message.payload.operation_id == intent.operation_id
            ),
            None,
        )
        if prior_contract is not None:
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference=f"duplicate-operation:{prior_contract.payload.contract_id}",
            )
            self.emit(
                "field.operation_duplicate",
                {
                    "operation_id": str(intent.operation_id),
                    "contract_id": str(prior_contract.payload.contract_id),
                },
            )
            return

        now = self.clock.now()
        operation_id = intent.operation_id
        initial_snapshot = self.factory.make(
            "site.snapshot",
            self.mission_id,
            envelope.correlation_id,
            SiteSnapshot(
                site_id="dummy-site-1",
                entities=("dummy-button-1",),
                robot_states=(),
                evidence=evidence(
                    "field-fixture-1", now, ProvenanceKind.MEASURED, world_revision=1
                ),
            ),
            causation_id=envelope.message_id,
        )
        grounded = self.factory.make(
            "operation.grounded",
            self.mission_id,
            envelope.correlation_id,
            GroundedOperation(
                operation_id=operation_id,
                target_entity_id=intent.selector.entity_id,
                target_pose=dummy_pose(),
                state=OperationState.ADMITTED,
                evidence=evidence(
                    "field-fixture-1", now, ProvenanceKind.MEASURED, world_revision=1
                ),
            ),
            causation_id=envelope.message_id,
        )
        task_id = self.factory.uuid_factory()
        plan = self.factory.make(
            "operation.plan",
            self.mission_id,
            envelope.correlation_id,
            OperationPlan(
                plan_id=self.factory.uuid_factory(),
                operation_id=operation_id,
                tasks=(
                    TaskNode(
                        task_id=task_id,
                        skill=OperationType.PRESS_BUTTON,
                        target_entity_id=intent.selector.entity_id,
                    ),
                ),
            ),
            causation_id=grounded.message_id,
        )
        assignment = self.factory.make(
            "task.assignment",
            self.robot_id,
            envelope.correlation_id,
            TaskAssignment(
                assignment_id=self.factory.uuid_factory(),
                plan_id=plan.payload.plan_id,
                task_id=task_id,
                executor_id=self.robot_id,
            ),
            causation_id=plan.message_id,
        )
        contract = self.factory.make(
            "execution.contract",
            self.robot_id,
            envelope.correlation_id,
            ExecutionContract(
                contract_id=self.factory.uuid_factory(),
                contract_revision=1,
                operation_id=operation_id,
                assignment_id=assignment.payload.assignment_id,
                state=ContractState.RECEIVED,
            ),
            causation_id=assignment.message_id,
        )

        hold_reason: str | None = None
        if envelope.expires_at is not None and now >= envelope.expires_at:
            hold_reason = "expired"
        elif intent.selector.entity_id != "dummy-button-1":
            hold_reason = "selector-not-whitelisted"
        elif intent.preferred_executor != self.robot_id:
            hold_reason = "executor-unavailable"
        elif intent.operation_type is not OperationType.PRESS_BUTTON:
            hold_reason = "capability-unavailable"
        elif intent.approval_policy is not ApprovalPolicy.AUTO_IF_WHITELISTED:
            hold_reason = "approval-policy-denied"
        elif contract.payload.contract_revision != 1:
            hold_reason = "unsupported-contract-revision"

        if hold_reason is not None:
            held_contract = contract.model_copy(
                update={
                    "destination_id": self.mission_id,
                    "causation_id": envelope.message_id,
                }
            )
            held_event = self.factory.make(
                "execution.event",
                self.mission_id,
                envelope.correlation_id,
                ExecutionEvent(
                    event_id=self.factory.uuid_factory(),
                    contract_id=contract.payload.contract_id,
                    contract_revision=1,
                    previous_state=ContractState.RECEIVED,
                    next_state=ContractState.HELD,
                    occurred_at=now,
                ),
                causation_id=held_contract.message_id,
            )
            outgoing = (held_contract, held_event)
            result = f"held-{hold_reason}:{contract.payload.contract_id}"
            event_name = "field.operation_held"
        else:
            outgoing = (initial_snapshot, grounded, plan, assignment, contract)
            result = f"admitted:{contract.payload.contract_id}"
            event_name = "field.operation_admitted"

        self.store.complete_inbox(
            envelope.message_id,
            processed_at=now,
            handler_result_reference=result,
            outgoing=outgoing,
        )
        self.emit(
            event_name,
            {
                "operation_id": str(operation_id),
                "contract_id": str(contract.payload.contract_id),
                "hold_reason": hold_reason,
            },
        )

    def _process_robot_message(self, envelope: MessageEnvelope) -> None:
        now = self.clock.now()
        forwarded = self.factory.make(
            envelope.message_type,
            self.mission_id,
            envelope.correlation_id,
            envelope.payload,
            causation_id=envelope.message_id,
        )
        outgoing: list[MessageEnvelope] = [forwarded]
        if (
            isinstance(envelope.payload, ExecutionEvent)
            and envelope.payload.next_state in TERMINAL_STATES
        ):
            robot_state = next(
                (
                    message.payload
                    for message in reversed(self.store.inbox_messages())
                    if isinstance(message.payload, RobotState)
                ),
                RobotState(
                    robot_id=self.robot_id,
                    pose=dummy_pose(pressed=True),
                    evidence=evidence(
                        "field-fixture-1", now, ProvenanceKind.MEASURED, world_revision=2
                    ),
                ),
            )
            snapshot = self.factory.make(
                "site.snapshot",
                self.mission_id,
                envelope.correlation_id,
                SiteSnapshot(
                    site_id="dummy-site-1",
                    entities=("dummy-button-1",),
                    robot_states=(robot_state,),
                    evidence=evidence(
                        "field-fixture-1", now, ProvenanceKind.MEASURED, world_revision=2
                    ),
                ),
                causation_id=envelope.message_id,
            )
            outgoing.append(snapshot)
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=now,
            handler_result_reference=f"forwarded:{forwarded.message_id}",
            outgoing=outgoing,
        )
        fields: dict[str, Any] = {
            "message_id": str(envelope.message_id),
            "message_type": envelope.message_type,
        }
        if isinstance(envelope.payload, ExecutionEvent):
            fields["state"] = envelope.payload.next_state.value
        self.emit("field.robot_message", fields)


class DummyRobotService(RuntimeService):
    def __init__(
        self,
        store: NodeStore,
        factory: EnvelopeFactory,
        *,
        field_id: str = "field-1",
        phase_duration: float = 0.05,
        emit: EventSink = _ignore_event,
    ) -> None:
        super().__init__(store, factory, emit=emit)
        self.field_id = field_id
        self.phase_duration = phase_duration

    @property
    def capabilities(self) -> tuple[str, ...]:
        return (OperationType.PRESS_BUTTON.value,)

    @property
    def effect_counter(self) -> int:
        return sum(int(row["effect_count"]) for row in self.store.inspect_execution_journal())

    async def process_claimed(self, envelope: MessageEnvelope) -> None:
        if isinstance(envelope.payload, TaskAssignment):
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference=f"assignment:{envelope.payload.assignment_id}",
            )
            self.emit(
                "robot.assignment_received",
                {"assignment_id": str(envelope.payload.assignment_id)},
            )
            return
        if isinstance(envelope.payload, ExecutionContract):
            await self._process_contract(envelope)
            return
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=self.clock.now(),
            handler_result_reference=f"robot-ignored:{envelope.message_type}",
        )

    async def _process_contract(self, envelope: MessageEnvelope) -> None:
        contract = envelope.payload
        assert isinstance(contract, ExecutionContract)
        assignment = next(
            (
                message.payload
                for message in self.store.inbox_messages()
                if isinstance(message.payload, TaskAssignment)
                and message.payload.assignment_id == contract.assignment_id
            ),
            None,
        )
        if assignment is None:
            self.store.fail_inbox(
                envelope.message_id,
                {"reason": "assignment-not-yet-persisted", "retryable": True},
            )
            self.emit(
                "robot.contract_held",
                {"contract_id": str(contract.contract_id), "reason": "missing-assignment"},
            )
            return

        hold_reason: str | None = None
        if assignment.executor_id != self.factory.node_id:
            hold_reason = "assignment-addressed-to-different-executor"
        elif contract.contract_revision != 1:
            hold_reason = "unsupported-contract-revision"
        if hold_reason is not None:
            held_event = self._transition(
                envelope,
                ContractState.RECEIVED,
                ContractState.HELD,
                ordinal=1,
            )
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference=f"held:{hold_reason}:{contract.contract_id}",
                outgoing=(held_event,),
            )
            self.emit(
                "robot.contract_held",
                {"contract_id": str(contract.contract_id), "reason": hold_reason},
            )
            return

        effect_key = f"press:{contract.operation_id}:{contract.contract_revision}"
        accepted_now = self.store.accept_contract(
            contract_id=contract.contract_id,
            contract_revision=contract.contract_revision,
            operation_id=contract.operation_id,
            task_id=assignment.task_id,
            effect_key=effect_key,
            accepted_at=self.clock.now(),
        )
        journal = self._journal(contract)
        if journal["state"] in {state.value for state in TERMINAL_STATES}:
            self._enqueue_terminal_replay(envelope, journal)
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference=f"terminal-replay:{contract.contract_id}",
            )
            return

        if accepted_now:
            self._enqueue_transition(
                envelope,
                ContractState.RECEIVED,
                ContractState.ACCEPTED,
                ordinal=1,
            )
        if journal["state"] == ContractState.ACCEPTED.value:
            self.store.record_dispatch(
                contract.contract_id,
                contract.contract_revision,
                recorded_at=self.clock.now(),
            )
            self._enqueue_transition(
                envelope,
                ContractState.ACCEPTED,
                ContractState.DISPATCH_RECORDED,
                ordinal=2,
            )
            invocation_id = uuid5(
                NAMESPACE_URL,
                f"dtt-skill:{contract.contract_id}:{contract.contract_revision}",
            )
            invocation = {
                "invocation_id": str(invocation_id),
                "skill": OperationType.PRESS_BUTTON.value,
                "target_entity_id": "dummy-button-1",
            }
            self.store.append_execution_audit(
                contract.contract_id,
                contract.contract_revision,
                event_type="skill-invocation",
                metadata=invocation,
                recorded_at=self.clock.now(),
            )
            self.emit(
                "robot.skill_invoked",
                {"contract_id": str(contract.contract_id), **invocation},
            )

        self._enqueue_transition(
            envelope,
            ContractState.DISPATCH_RECORDED,
            ContractState.RUNNING,
            ordinal=3,
        )
        self._enqueue_telemetry(envelope)
        for phase in DUMMY_PHASES[:-1]:
            now = self.clock.now()
            self.store.append_execution_audit(
                contract.contract_id,
                contract.contract_revision,
                event_type="dummy-skill-phase",
                metadata={
                    "phase": phase,
                    "safe_to_interrupt": phase not in {"CONTACTING", "VERIFYING_EFFECT"},
                },
                recorded_at=now,
            )
            self.emit(
                "robot.phase",
                {
                    "contract_id": str(contract.contract_id),
                    "phase": phase,
                    "safe_to_interrupt": phase not in {"CONTACTING", "VERIFYING_EFFECT"},
                },
            )
            await self.clock.sleep(self.phase_duration)

        terminal_event = self._transition(
            envelope,
            ContractState.RUNNING,
            ContractState.SUCCEEDED,
            ordinal=4,
        )
        committed = self.store.commit_dummy_effect(
            contract.contract_id,
            contract.contract_revision,
            terminal_state=ContractState.SUCCEEDED,
            terminal_result={
                "effect_key": effect_key,
                "button": "dummy-button-1",
                "button_state": "PRESSED",
                "effect_counter": self.effect_counter + 1,
            },
            occurred_at=self.clock.now(),
            terminal_event=terminal_event,
        )
        self.store.append_execution_audit(
            contract.contract_id,
            contract.contract_revision,
            event_type="dummy-skill-phase",
            metadata={"phase": "SUCCEEDED", "safe_to_interrupt": True},
            recorded_at=self.clock.now(),
        )
        self.emit(
            "robot.phase",
            {
                "contract_id": str(contract.contract_id),
                "phase": "SUCCEEDED",
                "safe_to_interrupt": True,
            },
        )
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=self.clock.now(),
            handler_result_reference=f"terminal:{contract.contract_id}",
        )
        self.emit(
            "robot.effect_committed",
            {
                "contract_id": str(contract.contract_id),
                "effect_counter": self.effect_counter,
                "committed": committed,
            },
        )

    def _journal(self, contract: ExecutionContract) -> dict[str, Any]:
        return next(
            row
            for row in self.store.inspect_execution_journal()
            if row["contract_id"] == str(contract.contract_id)
            and row["contract_revision"] == contract.contract_revision
        )

    def _transition(
        self,
        cause: MessageEnvelope,
        previous: ContractState,
        next_state: ContractState,
        *,
        ordinal: int,
    ) -> MessageEnvelope:
        contract = cause.payload
        assert isinstance(contract, ExecutionContract)
        stable = f"{contract.contract_id}:{contract.contract_revision}:{next_state.value}"
        event_id = uuid5(NAMESPACE_URL, f"dtt-event:{stable}")
        message_id = uuid5(NAMESPACE_URL, f"dtt-envelope:{stable}")
        occurred_at = cause.created_at + timedelta(milliseconds=ordinal)
        return self.factory.make(
            "execution.event",
            self.field_id,
            cause.correlation_id,
            ExecutionEvent(
                event_id=event_id,
                contract_id=contract.contract_id,
                contract_revision=contract.contract_revision,
                previous_state=previous,
                next_state=next_state,
                occurred_at=occurred_at,
            ),
            causation_id=cause.message_id,
            message_id=message_id,
            created_at=occurred_at,
        )

    def _enqueue_transition(
        self,
        cause: MessageEnvelope,
        previous: ContractState,
        next_state: ContractState,
        *,
        ordinal: int,
    ) -> None:
        self._enqueue_if_absent(
            self._transition(cause, previous, next_state, ordinal=ordinal)
        )

    def _enqueue_if_absent(self, envelope: MessageEnvelope) -> bool:
        if any(
            existing.message_id == envelope.message_id
            for existing in self.store.outbox_messages()
        ):
            return False
        return self.store.enqueue(envelope)

    def _enqueue_telemetry(self, cause: MessageEnvelope) -> None:
        now = self.clock.now()
        state = self.factory.make(
            "robot.state",
            self.field_id,
            cause.correlation_id,
            RobotState(
                robot_id=self.factory.node_id,
                pose=dummy_pose(),
                evidence=evidence(
                    self.factory.node_id,
                    now,
                    ProvenanceKind.MEASURED,
                    world_revision=1,
                ),
            ),
            message_id=uuid5(NAMESPACE_URL, f"dtt-state:{cause.payload.contract_id}"),
            created_at=cause.created_at + timedelta(milliseconds=5),
        )
        forecast_time = now + timedelta(seconds=max(1.0, self.phase_duration * len(DUMMY_PHASES)))
        forecast = self.factory.make(
            "robot.forecast",
            self.field_id,
            cause.correlation_id,
            RobotForecast(
                robot_id=self.factory.node_id,
                predicted_pose=dummy_pose(pressed=True),
                predicted_for=forecast_time,
                evidence=evidence(
                    self.factory.node_id,
                    now,
                    ProvenanceKind.PREDICTED,
                    world_revision=1,
                ),
            ),
            causation_id=cause.message_id,
            message_id=uuid5(NAMESPACE_URL, f"dtt-forecast:{cause.payload.contract_id}"),
            created_at=cause.created_at + timedelta(milliseconds=6),
        )
        self._enqueue_if_absent(state)
        self._enqueue_if_absent(forecast)

    def _enqueue_terminal_replay(
        self, cause: MessageEnvelope, journal: Mapping[str, Any]
    ) -> None:
        contract = cause.payload
        assert isinstance(contract, ExecutionContract)
        terminal = ContractState(str(journal["state"]))
        replay_id = uuid5(NAMESPACE_URL, f"dtt-replay:{cause.message_id}:{terminal.value}")
        replay = self.factory.make(
            "execution.event",
            self.field_id,
            cause.correlation_id,
            ExecutionEvent(
                event_id=replay_id,
                contract_id=contract.contract_id,
                contract_revision=contract.contract_revision,
                previous_state=ContractState.RUNNING,
                next_state=terminal,
                occurred_at=datetime.fromisoformat(
                    str(journal["terminal_at"]).replace("Z", "+00:00")
                ),
            ),
            causation_id=cause.message_id,
            message_id=uuid5(NAMESPACE_URL, f"dtt-replay-envelope:{replay_id}"),
        )
        self.store.enqueue(replay)
        self.emit(
            "robot.terminal_replayed",
            {"contract_id": str(contract.contract_id), "state": terminal.value},
        )
