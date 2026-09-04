"""M1 delayed-dummy domain services shared by the executable node processes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from deferred_teleop.external_effect import (
    ExternalEffectAdapter,
    ExternalEffectObservation,
    ExternalOutcome,
    coerce_observation,
)
from deferred_teleop.mission_view import (
    ArrivalBeliefView,
    ArticulatedMissionViewState,
    ConfirmedStateView,
    MissionConnectionState,
    MissionConnectionStatus,
    MissionViewState,
    MissionViewStatus,
    TargetBranchView,
    TimedTrajectorySample,
    TrajectorySampleSource,
)
from deferred_teleop.protocol import (
    ApprovalPolicy,
    ArticulatedRobotState,
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
from deferred_teleop.storage import NodeStore, RecordConflictError

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


class MissionViewSelectionError(ValueError):
    """The Mission inbox/outbox cannot identify one operation unambiguously."""


@dataclass(frozen=True)
class _MissionViewSelection:
    intent: MessageEnvelope | None
    snapshot: MessageEnvelope | None
    forecast: MessageEnvelope | None
    terminal: MessageEnvelope | None


@dataclass(frozen=True)
class _MissionViewProjection:
    estimated_arrival_at: datetime | None
    confirmed: ConfirmedStateView | None
    arrival: ArrivalBeliefView | None
    target: TargetBranchView | None
    trajectory: tuple[TimedTrajectorySample, ...]
    manifests: tuple[PredictionManifest, ...]


def _select_articulated_robot_state(
    inbox: Iterable[MessageEnvelope],
    outbox: Iterable[MessageEnvelope],
    intent: MessageEnvelope | None,
) -> ArticulatedRobotState | None:
    """Select only causal, executor-matching measured articulated evidence.

    M2 has no predictor or IK source for the other two layers.  A state from another
    correlation or another executor must therefore never become an arrival/target substitute.
    """

    if intent is None or not isinstance(intent.payload, OperationIntent):
        return None
    correlation_id = intent.correlation_id
    candidates = tuple(
        message
        for message in tuple(inbox) + tuple(outbox)
        if isinstance(message.payload, ArticulatedRobotState)
        and message.correlation_id == correlation_id
    )

    # The latest evidence is authoritative for this correlation, including when it is
    # incompatible.  Do not fall back to an older compatible state after a newer robot,
    # provenance, or frame mismatch.
    selected = (
        max(
            candidates,
            key=lambda message: (
                message.payload.evidence.world_revision,
                message.payload.evidence.observed_at,
                message.payload.evidence.produced_at,
                _message_id_key(message),
            ),
        )
        if candidates
        else None
    )
    if selected is None:
        return None
    state = selected.payload
    if state.robot_id != intent.payload.preferred_executor:
        return None
    if state.evidence.provenance not in {ProvenanceKind.MEASURED, ProvenanceKind.FUSED}:
        return None

    # If a grounded record explicitly carries a frame/calibration reference, reject an
    # incomparable articulated root while retaining the state in durable storage for diagnosis.
    grounded = tuple(
        message
        for message in tuple(inbox) + tuple(outbox)
        if isinstance(message.payload, GroundedOperation)
        and message.correlation_id == correlation_id
        and message.payload.operation_id == intent.payload.operation_id
    )
    if grounded:
        reference = max(
            grounded,
            key=lambda message: (
                message.payload.evidence.world_revision,
                message.payload.evidence.observed_at,
                message.payload.evidence.produced_at,
                _message_id_key(message),
            ),
        ).payload.target_pose.frame
        if state.root_pose.frame != reference:
            return None
    return state


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


def _durable_timestamp(value: object, *, field_name: str) -> datetime:
    """Decode one UTC timestamp persisted in the execution journal."""

    if not isinstance(value, str):
        raise RecordConflictError(f"journal {field_name} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RecordConflictError(f"journal {field_name} is not a timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecordConflictError(f"journal {field_name} must be timezone-aware")
    return parsed


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


def _message_id_key(message: MessageEnvelope) -> str:
    """Return the stable final tie-break key used by Mission projections."""

    return str(message.message_id)


def _select_mission_view(
    inbox: Iterable[MessageEnvelope], outbox: Iterable[MessageEnvelope]
) -> _MissionViewSelection:
    """Select one operation and its correlation-scoped observations.

    The helper is intentionally pure: it only inspects the supplied immutable
    envelopes.  Storage order therefore cannot change which intent, snapshot,
    forecast, or terminal event is projected.
    """

    inbox_messages = tuple(inbox)
    outbox_messages = tuple(outbox)
    intents = tuple(
        message
        for message in outbox_messages
        if isinstance(message.payload, OperationIntent)
    )

    correlations_by_operation: dict[UUID, set[UUID]] = {}
    operations_by_correlation: dict[UUID, set[UUID]] = {}
    for message in intents:
        operation_id = message.payload.operation_id
        correlations_by_operation.setdefault(operation_id, set()).add(
            message.correlation_id
        )
        operations_by_correlation.setdefault(message.correlation_id, set()).add(operation_id)

    ambiguous_operations = {
        operation_id: correlations
        for operation_id, correlations in correlations_by_operation.items()
        if len(correlations) > 1
    }
    if ambiguous_operations:
        operation_id, correlations = min(
            ambiguous_operations.items(), key=lambda item: str(item[0])
        )
        rendered = ", ".join(sorted(str(correlation) for correlation in correlations))
        raise MissionViewSelectionError(
            "ambiguous correlation_id for operation_id "
            f"{operation_id}: {rendered}"
        )

    ambiguous_correlations = {
        correlation_id: operation_ids
        for correlation_id, operation_ids in operations_by_correlation.items()
        if len(operation_ids) > 1
    }
    if ambiguous_correlations:
        correlation_id, operation_ids = min(
            ambiguous_correlations.items(), key=lambda item: str(item[0])
        )
        rendered = ", ".join(sorted(str(operation_id) for operation_id in operation_ids))
        raise MissionViewSelectionError(
            "ambiguous operation_id for correlation_id "
            f"{correlation_id}: {rendered}"
        )

    intent = (
        max(
            intents,
            key=lambda message: (message.created_at, _message_id_key(message)),
        )
        if intents
        else None
    )
    if intent is None:
        return _MissionViewSelection(None, None, None, None)

    correlation_id = intent.correlation_id
    snapshots = tuple(
        message
        for message in inbox_messages
        if isinstance(message.payload, SiteSnapshot)
        and message.correlation_id == correlation_id
    )
    forecasts = tuple(
        message
        for message in inbox_messages
        if isinstance(message.payload, RobotForecast)
        and message.correlation_id == correlation_id
    )
    terminal_events = tuple(
        message
        for message in inbox_messages
        if isinstance(message.payload, ExecutionEvent)
        and message.correlation_id == correlation_id
        and message.payload.next_state in TERMINAL_STATES
    )

    snapshot = (
        max(
            snapshots,
            key=lambda message: (
                message.payload.evidence.world_revision,
                message.payload.evidence.observed_at,
                message.payload.evidence.produced_at,
                _message_id_key(message),
            ),
        )
        if snapshots
        else None
    )
    forecast = (
        max(
            forecasts,
            key=lambda message: (
                message.payload.evidence.produced_at,
                message.payload.predicted_for,
                _message_id_key(message),
            ),
        )
        if forecasts
        else None
    )

    terminal: MessageEnvelope | None = None
    if terminal_events:
        terminal_states_by_contract: dict[tuple[UUID, int], set[ContractState]] = {}
        for message in terminal_events:
            key = (
                message.payload.contract_id,
                message.payload.contract_revision,
            )
            terminal_states_by_contract.setdefault(key, set()).add(
                message.payload.next_state
            )
        if any(len(states) > 1 for states in terminal_states_by_contract.values()) or len(
            {message.payload.next_state for message in terminal_events}
        ) > 1:
            return _MissionViewSelection(intent, snapshot, forecast, None)

        latest_terminal_key = max(
            (
                message.payload.occurred_at,
                message.payload.contract_revision,
            )
            for message in terminal_events
        )
        latest_terminals = tuple(
            message
            for message in terminal_events
            if (
                message.payload.occurred_at,
                message.payload.contract_revision,
            )
            == latest_terminal_key
        )
        terminal_states = {message.payload.next_state for message in latest_terminals}
        if len(terminal_states) == 1:
            terminal = max(
                latest_terminals,
                key=lambda message: (
                    message.payload.occurred_at,
                    message.payload.contract_revision,
                    _message_id_key(message),
                ),
            )

    return _MissionViewSelection(intent, snapshot, forecast, terminal)


def _select_snapshot_robot(
    snapshot: SiteSnapshot, preferred_robot_id: str
) -> RobotState | None:
    """Choose a stable robot observation from the selected snapshot."""

    if not snapshot.robot_states:
        return None
    candidates = tuple(
        state for state in snapshot.robot_states if state.robot_id == preferred_robot_id
    )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda state: (
            state.evidence.world_revision,
            state.evidence.observed_at,
            state.evidence.produced_at,
            state.robot_id,
        ),
    )


def _project_mission_view(
    selection: _MissionViewSelection, configured_one_way_delay: float
) -> _MissionViewProjection:
    """Build shared typed projections for legacy and strict Mission views."""

    intent = selection.intent
    snapshot = selection.snapshot.payload if selection.snapshot is not None else None
    forecast = selection.forecast.payload if selection.forecast is not None else None
    estimated_arrival_at = (
        intent.created_at + timedelta(seconds=configured_one_way_delay)
        if intent is not None
        else None
    )

    confirmed_robot = (
        _select_snapshot_robot(snapshot, intent.payload.preferred_executor)
        if snapshot is not None and intent is not None
        else None
    )
    confirmed = (
        ConfirmedStateView(
            site_id=snapshot.site_id,
            robot_id=confirmed_robot.robot_id,
            pose=confirmed_robot.pose,
            evidence=confirmed_robot.evidence,
        )
        if snapshot is not None and confirmed_robot is not None
        else None
    )

    forecast_compatible = forecast is not None and intent is not None and (
        forecast.robot_id == intent.payload.preferred_executor
        and (
            confirmed is None
            or (
                forecast.robot_id == confirmed.robot_id
                and forecast.predicted_pose.frame == confirmed.pose.frame
            )
        )
    )
    arrival = (
        ArrivalBeliefView(
            robot_id=forecast.robot_id,
            pose=forecast.predicted_pose,
            predicted_for=forecast.predicted_for,
            estimated_intent_arrival_at=estimated_arrival_at,
            link_one_way_delay_seconds=configured_one_way_delay,
            evidence=forecast.evidence,
        )
        if forecast is not None and forecast_compatible
        else None
    )

    target: TargetBranchView | None = None
    if intent is not None:
        target_pose = dummy_pose(pressed=True)
        if confirmed is None or target_pose.frame == confirmed.pose.frame:
            target = TargetBranchView(
                entity_id=intent.payload.selector.entity_id,
                pose=target_pose,
                evidence=evidence(
                    "mission-operator-1",
                    intent.created_at,
                    ProvenanceKind.OPERATOR_ASSERTED,
                    world_revision=snapshot.evidence.world_revision if snapshot else 1,
                    fresh_for_seconds=60.0,
                ),
            )

    trajectory: list[TimedTrajectorySample] = []
    if confirmed is not None:
        trajectory.append(
            TimedTrajectorySample(
                sample_time=confirmed.evidence.observed_at,
                pose=confirmed.pose,
                source=TrajectorySampleSource.CONFIRMED_STATE,
                provenance=confirmed.evidence.provenance,
            )
        )
    if arrival is not None:
        trajectory.append(
            TimedTrajectorySample(
                sample_time=arrival.predicted_for,
                pose=arrival.pose,
                source=TrajectorySampleSource.ARRIVAL_BELIEF,
                provenance=arrival.evidence.provenance,
            )
        )

    manifests: tuple[PredictionManifest, ...] = ()
    if selection.forecast is not None and arrival is not None:
        manifests = (
            PredictionManifest(
                manifest_id=uuid5(
                    NAMESPACE_URL, f"dtt-manifest:{selection.forecast.message_id}"
                ),
                site_id="dummy-site-1",
                forecast_ids=(selection.forecast.message_id,),
                generated_for_world_revision=forecast.evidence.world_revision,
                evidence=forecast.evidence,
            ),
        )

    return _MissionViewProjection(
        estimated_arrival_at=estimated_arrival_at,
        confirmed=confirmed,
        arrival=arrival,
        target=target,
        trajectory=tuple(trajectory),
        manifests=manifests,
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
        self._link_connection_state = MissionConnectionState.DISCONNECTED
        self._link_status_changed_at = self.clock.now()
        self._view_sequence = 0
        self._articulated_view_sequence = 0

    def set_link_connected(self, connected: bool) -> None:
        next_state = (
            MissionConnectionState.CONNECTED
            if connected
            else MissionConnectionState.DISCONNECTED
        )
        if next_state is self._link_connection_state:
            return
        self._link_connection_state = next_state
        self._link_status_changed_at = self.clock.now()

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
        selection = _select_mission_view(inbox, self.store.outbox_messages())
        projection = _project_mission_view(selection, self.configured_one_way_delay)
        intent = selection.intent
        snapshot = selection.snapshot.payload if selection.snapshot is not None else None
        forecast = selection.forecast.payload if selection.forecast is not None else None
        manifest = projection.manifests[0] if projection.manifests else None
        return {
            "node_id": self.factory.node_id,
            "operation_id": str(intent.payload.operation_id) if intent else None,
            "correlation_id": str(intent.correlation_id) if intent else None,
            "estimated_arrival_at": projection.estimated_arrival_at.isoformat()
            if projection.estimated_arrival_at
            else None,
            "confirmed_state": snapshot.model_dump(mode="json")
            if snapshot is not None and projection.confirmed is not None
            else None,
            "arrival_belief": {
                **forecast.model_dump(mode="json"),
                "estimated_intent_arrival_at": projection.estimated_arrival_at.isoformat()
                if projection.estimated_arrival_at
                else None,
                "link_one_way_delay_seconds": self.configured_one_way_delay,
            }
            if forecast is not None and projection.arrival is not None
            else None,
            "prediction_manifest": manifest.model_dump(mode="json") if manifest else None,
            "target_branch": (
                {
                    "condition": projection.target.condition,
                    "entity_id": projection.target.entity_id,
                    "requested_state": projection.target.requested_state,
                    "provenance": projection.target.evidence.provenance.value,
                }
                if projection.target is not None
                else None
            ),
            "terminal_state": (
                selection.terminal.payload.next_state.value
                if selection.terminal is not None
                else None
            ),
            "terminal_contract_id": (
                str(selection.terminal.payload.contract_id)
                if selection.terminal is not None
                else None
            ),
            "received_message_count": len(inbox),
        }

    def view_state(self) -> MissionViewState:
        """Build the strict, presentation-neutral snapshot consumed by Unreal."""

        inbox = self.store.inbox_messages()
        selection = _select_mission_view(inbox, self.store.outbox_messages())
        projection = _project_mission_view(selection, self.configured_one_way_delay)
        intent = selection.intent

        self._view_sequence += 1
        return MissionViewState(
            source_id=self.factory.node_id,
            source_sequence=self._view_sequence,
            produced_at=self.clock.now(),
            connection=MissionConnectionStatus(
                mission_to_field=self._link_connection_state,
                changed_at=self._link_status_changed_at,
            ),
            confirmed_state=projection.confirmed,
            arrival_belief=projection.arrival,
            target_branch=projection.target,
            trajectory_forecasts=projection.trajectory,
            prediction_manifests=projection.manifests,
            status=MissionViewStatus(
                operation_id=intent.payload.operation_id if intent else None,
                correlation_id=intent.correlation_id if intent else None,
                terminal_state=(
                    selection.terminal.payload.next_state
                    if selection.terminal is not None
                    else None
                ),
                terminal_contract_id=(
                    selection.terminal.payload.contract_id
                    if selection.terminal is not None
                    else None
                ),
                received_message_count=len(inbox),
            ),
        )

    def articulated_view_state(self) -> ArticulatedMissionViewState:
        """Build the opt-in M2 frame with a confirmed articulated layer only.

        Arrival and target are deliberately emitted as explicit nulls until M2 has a predictor
        and an IK authoring path.  Selection still runs through the M1 correlation ambiguity
        checks, so a newer operation cannot borrow evidence from an older branch.
        """

        inbox = self.store.inbox_messages()
        outbox = self.store.outbox_messages()
        selection = _select_mission_view(inbox, outbox)
        intent = selection.intent
        confirmed = _select_articulated_robot_state(inbox, outbox, intent)

        self._articulated_view_sequence += 1
        return ArticulatedMissionViewState(
            source_id=self.factory.node_id,
            source_sequence=self._articulated_view_sequence,
            produced_at=self.clock.now(),
            connection=MissionConnectionStatus(
                mission_to_field=self._link_connection_state,
                changed_at=self._link_status_changed_at,
            ),
            status=MissionViewStatus(
                operation_id=intent.payload.operation_id if intent else None,
                correlation_id=intent.correlation_id if intent else None,
                terminal_state=(
                    selection.terminal.payload.next_state
                    if selection.terminal is not None
                    else None
                ),
                terminal_contract_id=(
                    selection.terminal.payload.contract_id
                    if selection.terminal is not None
                    else None
                ),
                received_message_count=len(inbox),
            ),
            confirmed_robot_state=confirmed,
            arrival_robot_state=None,
            target_robot_state=None,
        )

class FieldService(RuntimeService):
    def __init__(
        self,
        store: NodeStore,
        factory: EnvelopeFactory,
        *,
        mission_id: str = "mission-1",
        robot_id: str = "dummy-robot-1",
        # The historical no-telemetry ordering is opt-in.  The golden replay
        # enables it explicitly; a normal Field only reconciles measured or
        # fused RobotState evidence.
        dummy_fixture_compatibility: bool = False,
        emit: EventSink = _ignore_event,
    ) -> None:
        super().__init__(store, factory, emit=emit)
        self.mission_id = mission_id
        self.robot_id = robot_id
        self.dummy_fixture_compatibility = dummy_fixture_compatibility

    async def process_claimed(self, envelope: MessageEnvelope) -> None:
        if isinstance(envelope.payload, OperationIntent):
            self._process_intent(envelope)
            return
        if isinstance(
            envelope.payload,
            (ExecutionEvent, RobotState, ArticulatedRobotState, RobotForecast),
        ):
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
                robot_states=(
                    RobotState(
                        robot_id=self.robot_id,
                        pose=dummy_pose(),
                        evidence=evidence(
                            "field-fixture-1",
                            now,
                            ProvenanceKind.MEASURED,
                            world_revision=1,
                        ),
                    ),
                ),
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
            and envelope.payload.next_state is ContractState.SUCCEEDED
        ):
            robot_state = next(
                (
                    message.payload
                    for message in reversed(self.store.inbox_messages())
                    if (
                        message.correlation_id == envelope.correlation_id
                        and isinstance(message.payload, RobotState)
                        and message.payload.robot_id == self.robot_id
                        and message.payload.evidence.provenance
                        in {ProvenanceKind.MEASURED, ProvenanceKind.FUSED}
                    )
                ),
                None,
            )
            # A terminal event alone is not physical evidence.  In particular,
            # never borrow a state from another operation or synthesize a
            # measured pose for an external effect that emitted no telemetry.
            if robot_state is not None:
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
            elif self.dummy_fixture_compatibility:
                # Preserve the historical M1 golden replay for the original
                # database dummy.  Its fixture emits the terminal before its
                # pre-effect telemetry.  This compatibility option is explicit
                # and is never inferred from the message shape; callers
                # handling an external adapter must disable it.
                snapshot = self.factory.make(
                    "site.snapshot",
                    self.mission_id,
                    envelope.correlation_id,
                    SiteSnapshot(
                        site_id="dummy-site-1",
                        entities=("dummy-button-1",),
                        robot_states=(
                            RobotState(
                                robot_id=self.robot_id,
                                pose=dummy_pose(pressed=True),
                                evidence=evidence(
                                    "field-fixture-1",
                                    now,
                                    ProvenanceKind.MEASURED,
                                    world_revision=2,
                                ),
                            ),
                        ),
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
        external_effect_adapter: ExternalEffectAdapter | None = None,
        emit: EventSink = _ignore_event,
    ) -> None:
        super().__init__(store, factory, emit=emit)
        self.field_id = field_id
        self.phase_duration = phase_duration
        self.external_effect_adapter = external_effect_adapter

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

        external_device_id = self._external_device_id()
        external_mode = self.external_effect_adapter is not None
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
        self._assert_external_dispatch_configuration(journal, external_device_id)
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
                occurred_at=(
                    _durable_timestamp(journal["accepted_at"], field_name="accepted_at")
                    if external_mode
                    else None
                ),
            )
        dispatch_recorded_now = False
        if journal["state"] == ContractState.ACCEPTED.value:
            dispatch_recorded_now = self.store.record_dispatch(
                contract.contract_id,
                contract.contract_revision,
                recorded_at=self.clock.now(),
                device_id=external_device_id,
            )
            journal = self._journal(contract)
            self._enqueue_transition(
                envelope,
                ContractState.ACCEPTED,
                ContractState.DISPATCH_RECORDED,
                ordinal=2,
                occurred_at=(
                    _durable_timestamp(
                        journal["dispatch_recorded_at"],
                        field_name="dispatch_recorded_at",
                    )
                    if external_mode
                    else None
                ),
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

        if self.external_effect_adapter is not None:
            journal = self._journal(contract)
            self._assert_external_dispatch_configuration(journal, external_device_id)
            await self._process_external_contract(
                envelope,
                effect_key=effect_key,
                dispatch_recorded_now=dispatch_recorded_now,
                expected_device_id=external_device_id,
            )
            return

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

    def _external_device_id(self) -> str | None:
        adapter = self.external_effect_adapter
        if adapter is None:
            return None
        device_id = getattr(adapter, "device_id", None)
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError(
                "an external effect adapter must expose a non-empty string device_id"
            )
        return device_id

    def _assert_external_dispatch_configuration(
        self, journal: Mapping[str, Any], expected_device_id: str | None
    ) -> None:
        """Prevent a durable external dispatch from falling back to dummy I/O."""

        durable_device_id = journal.get("dispatch_device_id")
        dispatch_recorded = journal.get("dispatch_recorded_at") is not None
        if durable_device_id is not None:
            if self.external_effect_adapter is None:
                raise RecordConflictError(
                    "external dispatch requires its original adapter for recovery"
                )
            assert expected_device_id is not None
            self._assert_external_identity(journal, expected_device_id)
        elif dispatch_recorded and self.external_effect_adapter is not None:
            # A pre-v3 journal has no trustworthy external device binding.  It
            # may still be replayed by the historical database dummy, but an
            # injected adapter must not guess which device performed dispatch.
            raise RecordConflictError(
                "external adapter cannot recover a dispatch without durable device identity"
            )

    @staticmethod
    def _assert_external_identity(
        journal: Mapping[str, Any], expected_device_id: str
    ) -> None:
        durable_device_id = journal.get("dispatch_device_id")
        if durable_device_id != expected_device_id:
            raise RecordConflictError(
                "external adapter device_id differs from durable dispatch identity"
            )

    def _external_clock_at_or_after(self, persisted_at: datetime) -> datetime:
        """Refuse external recovery while the process clock is behind dispatch.

        The durable dispatch boundary is the earliest valid timestamp for the
        remaining external lifecycle.  A restarted process whose clock has
        moved backwards must wait for a trusted clock before it can observe or
        resolve the effect.  Returning the checked value also prevents a
        second ``now()`` call from producing a terminal timestamp before the
        check that guarded it.
        """

        current = self.clock.now()
        if (
            not isinstance(current, datetime)
            or current.tzinfo is None
            or current.utcoffset() is None
        ):
            raise RecordConflictError(
                "external recovery requires a timezone-aware runtime clock"
            )
        if current < persisted_at:
            raise RecordConflictError(
                "external recovery clock is earlier than durable dispatch_recorded_at"
            )
        return current

    async def _process_external_contract(
        self,
        envelope: MessageEnvelope,
        *,
        effect_key: str,
        dispatch_recorded_now: bool,
        expected_device_id: str,
    ) -> None:
        """Issue or recover one external effect without a hidden retry.

        The durable dispatch boundary is deliberately checked before calling
        the adapter.  A fresh contract may press once after
        ``ACCEPTED -> DISPATCH_RECORDED``.  Any later invocation observes the
        external device, even when the previous process died before writing its
        terminal result.
        """

        contract = envelope.payload
        assert isinstance(contract, ExecutionContract)
        adapter = self.external_effect_adapter
        assert adapter is not None
        journal = self._journal(contract)
        self._assert_external_identity(journal, expected_device_id)
        dispatch_recorded_at = _durable_timestamp(
            journal["dispatch_recorded_at"], field_name="dispatch_recorded_at"
        )
        # Check before constructing/enqueuing RUNNING and before any adapter
        # observation or press.  This keeps a clock-regressed restart from
        # adding a synthetic lifecycle event or issuing an impulse.
        self._external_clock_at_or_after(dispatch_recorded_at)

        running_event = self._transition(
            envelope,
            ContractState.DISPATCH_RECORDED,
            ContractState.RUNNING,
            ordinal=3,
            occurred_at=dispatch_recorded_at,
        )
        self._enqueue_if_absent(running_event)
        if dispatch_recorded_now:
            self._external_clock_at_or_after(dispatch_recorded_at)
            invocation_id = uuid5(
                NAMESPACE_URL,
                f"dtt-external-skill:{contract.contract_id}:{contract.contract_revision}",
            )
            self.store.append_execution_audit(
                contract.contract_id,
                contract.contract_revision,
                event_type="external-effect-dispatch",
                metadata={
                    "effect_key": effect_key,
                    "device_id": expected_device_id,
                    "invocation_id": str(invocation_id),
                },
                recorded_at=self.clock.now(),
            )
            self.emit(
                "robot.external_effect_dispatch",
                {
                    "contract_id": str(contract.contract_id),
                    "effect_key": effect_key,
                    "device_id": expected_device_id,
                },
            )
            # A conforming adapter may persist the physical/test action and
            # then raise to model a crash.  Let that exception escape: the
            # inbox remains PROCESSING and recovery will observe, never press.
            adapter.press(effect_key)
        else:
            self._external_clock_at_or_after(dispatch_recorded_at)
            self.store.append_execution_audit(
                contract.contract_id,
                contract.contract_revision,
                event_type="external-effect-recovery-observation",
                metadata={
                    "effect_key": effect_key,
                    "device_id": expected_device_id,
                    "reason": "dispatch-already-recorded",
                },
                recorded_at=self.clock.now(),
            )

        self._external_clock_at_or_after(dispatch_recorded_at)
        observation: ExternalEffectObservation = coerce_observation(
            adapter.observe(effect_key),
            expected_effect_key=effect_key,
            expected_device_id=expected_device_id,
        )
        terminal_state = (
            ContractState.SUCCEEDED
            if observation.outcome is ExternalOutcome.APPLIED
            else ContractState.HELD
        )
        terminal_at = self._external_clock_at_or_after(dispatch_recorded_at)
        terminal_event = self._transition(
            envelope,
            ContractState.RUNNING,
            terminal_state,
            ordinal=4,
            occurred_at=terminal_at,
            causation_id=running_event.message_id,
        )
        resolution = {
            ExternalOutcome.APPLIED: "APPLIED",
            ExternalOutcome.UNKNOWN: "OUTCOME_UNKNOWN",
            ExternalOutcome.NOT_APPLIED: "NOT_APPLIED_AFTER_UNCERTAIN_DISPATCH",
        }[observation.outcome]
        committed = self.store.resolve_external_outcome(
            contract.contract_id,
            contract.contract_revision,
            observation=observation,
            expected_device_id=expected_device_id,
            terminal_state=terminal_state,
            terminal_result={
                "effect_key": effect_key,
                "device_id": observation.device_id,
                "outcome": resolution,
                "external_outcome": observation.outcome.value,
            },
            occurred_at=terminal_at,
            terminal_event=terminal_event,
        )
        self.store.append_execution_audit(
            contract.contract_id,
            contract.contract_revision,
            event_type="external-effect-outcome",
            metadata=observation.model_dump(),
            recorded_at=terminal_at,
        )
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=self.clock.now(),
            handler_result_reference=f"external-{resolution.lower()}:{contract.contract_id}",
        )
        self.emit(
            "robot.external_effect_resolved",
            {
                "contract_id": str(contract.contract_id),
                "effect_key": effect_key,
                "device_id": observation.device_id,
                "outcome": observation.outcome.value,
                "terminal_state": terminal_state.value,
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
        occurred_at: datetime | None = None,
        causation_id: UUID | None = None,
    ) -> MessageEnvelope:
        contract = cause.payload
        assert isinstance(contract, ExecutionContract)
        stable = f"{contract.contract_id}:{contract.contract_revision}:{next_state.value}"
        event_id = uuid5(NAMESPACE_URL, f"dtt-event:{stable}")
        message_id = uuid5(NAMESPACE_URL, f"dtt-envelope:{stable}")
        event_occurred_at = (
            occurred_at
            if occurred_at is not None
            else cause.created_at + timedelta(milliseconds=ordinal)
        )
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
                occurred_at=event_occurred_at,
            ),
            causation_id=causation_id or cause.message_id,
            message_id=message_id,
            created_at=event_occurred_at,
        )

    def _enqueue_transition(
        self,
        cause: MessageEnvelope,
        previous: ContractState,
        next_state: ContractState,
        *,
        ordinal: int,
        occurred_at: datetime | None = None,
        causation_id: UUID | None = None,
    ) -> None:
        self._enqueue_if_absent(
            self._transition(
                cause,
                previous,
                next_state,
                ordinal=ordinal,
                occurred_at=occurred_at,
                causation_id=causation_id,
            )
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
