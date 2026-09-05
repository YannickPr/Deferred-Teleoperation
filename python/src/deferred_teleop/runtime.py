"""M1 delayed-dummy domain services shared by the executable node processes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from deferred_teleop.external_effect import (
    ExternalEffectAdapter,
    ExternalEffectObservation,
    ExternalOutcome,
    coerce_observation,
)
from deferred_teleop.m3a_types import (
    EntityDetection as LocalEntityDetection,
)
from deferred_teleop.m3a_types import (
    LocalTwoButtonDecision as LocalM3aDecision,
)
from deferred_teleop.m3a_types import (
    M3aEnsureLatchedIntent as LocalM3aIntent,
)
from deferred_teleop.m3a_types import (
    M3aSpatialExecutionContext as LocalM3aContext,
)
from deferred_teleop.m3a_types import (
    SpatialPressCommand as LocalM3aCommand,
)
from deferred_teleop.m3a_types import (
    TwoButtonAction,
)
from deferred_teleop.m3a_types import (
    TwoButtonLevelEvidence as LocalM3aLevelEvidence,
)
from deferred_teleop.m3a_types import (
    TwoButtonObservation as LocalM3aObservation,
)
from deferred_teleop.m3a_types import (
    canonical_digest as m3a_canonical_digest,
)
from deferred_teleop.mission_view import (
    ArrivalBeliefView,
    ArticulatedMissionViewState,
    ConfirmedStateView,
    M3aMissionViewState,
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
    EntityDetection,
    EntitySelector,
    EvidenceMetadata,
    ExecutionContract,
    ExecutionEvent,
    GroundedOperation,
    LocalTwoButtonDecision,
    M3aAction,
    M3aEnsureLatchedIntent,
    M3aSpatialExecutionContext,
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
    SpatialPressCommand,
    TaskAssignment,
    TaskNode,
    TwoButtonEffectEvidence,
    TwoButtonLevelEvidence,
    TwoButtonObservation,
    Vector3,
    WireModel,
)
from deferred_teleop.storage import (
    BUDGET_DEADLINE_EXPIRED,
    BUDGET_LIMIT_EXHAUSTED,
    BUDGET_POLICY_CONFLICT,
    BUDGET_SCOPE_CONFLICT,
    LEGACY_OBSERVE_ONLY,
    LEGACY_UNBUDGETED_HOLD,
    BudgetDeadlineError,
    BudgetLimitError,
    BudgetPolicyConflictError,
    BudgetScopeConflictError,
    NodeStore,
    RecordConflictError,
)
from deferred_teleop.two_button_policy import (
    decide_two_button,
    derive_spatial_press_command,
    verify_reference,
)

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
        not_before: datetime | None = None,
        expires_at: datetime | None = None,
        message_id: UUID | None = None,
        created_at: datetime | None = None,
        source_id: str | None = None,
        source_boot_id: UUID | None = None,
    ) -> MessageEnvelope:
        self._sequence += 1
        timestamp = created_at or self.clock.now() + timedelta(microseconds=self._sequence)
        return MessageEnvelope(
            message_id=message_id or self.uuid_factory(),
            message_type=message_type,
            source_id=source_id or self.node_id,
            source_boot_id=source_boot_id or self.boot_id,
            source_sequence=self._sequence,
            destination_id=destination_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            created_at=timestamp,
            not_before=not_before,
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
    produced_at: datetime | None = None,
) -> EvidenceMetadata:
    produced = produced_at or now
    return EvidenceMetadata(
        source_ids=(source_id,),
        observed_at=now,
        produced_at=produced,
        provenance=provenance,
        world_revision=world_revision,
        fresh_until=produced + timedelta(seconds=fresh_for_seconds),
        model_version="dummy-constant-velocity-v1"
        if provenance in {ProvenanceKind.PREDICTED, ProvenanceKind.SIMULATED}
        else None,
    )


def _m3a_local_observation(payload: TwoButtonObservation) -> LocalM3aObservation:
    """Convert a strict wire observation to the pure-policy value type."""

    return LocalM3aObservation(
        observation_id=payload.observation_id,
        source_id=payload.source_id,
        world_revision=payload.world_revision,
        observed_at=payload.observed_at,
        produced_at=payload.produced_at,
        frame_id=payload.frame_id,
        calibration_version=payload.calibration_version,
        detections=tuple(
            LocalEntityDetection(
                detection_id=detection.detection_id,
                candidate_entity_ids=tuple(detection.candidate_entity_ids),
                pose=detection.pose,
                visibility=detection.visibility,
                source_evidence_id=detection.source_evidence_id,
            )
            for detection in payload.detections
        ),
        canonical_payload_digest=payload.canonical_payload_digest,
    )


def _m3a_wire_observation(payload: LocalM3aObservation) -> TwoButtonObservation:
    """Convert an immutable local observation into its wire envelope payload."""

    return TwoButtonObservation(
        observation_id=payload.observation_id,
        source_id=payload.source_id,
        world_revision=payload.world_revision,
        observed_at=payload.observed_at,
        produced_at=payload.produced_at,
        frame_id=payload.frame_id,
        calibration_version=payload.calibration_version,
        canonical_payload_digest=payload.canonical_payload_digest,
        detections=tuple(
            EntityDetection(
                detection_id=detection.detection_id,
                candidate_entity_ids=tuple(detection.candidate_entity_ids),
                pose=detection.pose,
                visibility=detection.visibility,
                source_evidence_id=detection.source_evidence_id,
            )
            for detection in payload.detections
        ),
    )


def _m3a_local_intent(payload: M3aEnsureLatchedIntent) -> LocalM3aIntent:
    return LocalM3aIntent(
        operation_id=payload.operation_id,
        intent_revision=payload.intent_revision,
        semantic_effect_id=payload.semantic_effect_id,
        target_entity_id=payload.target_entity_id,
        desired_latched=payload.desired_latched,
        reference_observation_id=payload.reference_observation_id,
        reference_detection_id=payload.reference_detection_id,
        reference_digest=payload.reference_digest,
        reference_pose=payload.reference_pose,
        reference_frame_id=payload.reference_frame_id,
        reference_calibration_version=payload.reference_calibration_version,
        reference_world_revision=payload.reference_world_revision,
        reference_observed_at=payload.reference_observed_at,
        same_identity_only=payload.same_identity_only,
        max_displacement_m=payload.max_displacement_m,
        expires_at=payload.expires_at,
    )


def _m3a_wire_level(payload: LocalM3aLevelEvidence) -> TwoButtonLevelEvidence:
    return TwoButtonLevelEvidence(
        target_entity_id=payload.target_entity_id,
        desired_latched=payload.desired_latched,
        actual_latched=payload.actual_latched,
        device_id=payload.device_id,
        counter=payload.counter,
        observed_at=payload.observed_at,
        evidence_observation_id=payload.evidence_observation_id,
    )


def _m3a_local_level(payload: TwoButtonLevelEvidence) -> LocalM3aLevelEvidence:
    """Convert independent level proof to the local policy value type."""

    return LocalM3aLevelEvidence(
        target_entity_id=payload.target_entity_id,
        desired_latched=payload.desired_latched,
        actual_latched=payload.actual_latched,
        device_id=payload.device_id,
        counter=payload.counter,
        observed_at=payload.observed_at,
        evidence_observation_id=payload.evidence_observation_id,
    )


def _m3a_wire_decision(payload: LocalM3aDecision) -> LocalTwoButtonDecision:
    return LocalTwoButtonDecision(
        operation_id=payload.operation_id,
        intent_revision=payload.intent_revision,
        semantic_effect_id=payload.semantic_effect_id,
        reference_observation_id=payload.reference_observation_id,
        current_observation_id=payload.current_observation_id,
        action=M3aAction(payload.action.value),
        reason=payload.reason,
        selected_detection_id=payload.selected_detection_id,
        displacement_m=payload.displacement_m,
        budget_state=payload.budget_state,
        command_digest=payload.command_digest,
        level_evidence_observation_id=payload.level_evidence_observation_id,
    )


def _m3a_wire_command(payload: LocalM3aCommand) -> SpatialPressCommand:
    return SpatialPressCommand(
        command_id=payload.command_id,
        effect_key=payload.effect_key,
        position_m=Vector3(
            x=payload.position_m[0], y=payload.position_m[1], z=payload.position_m[2]
        ),
        frame_id=payload.frame_id,
        calibration_version=payload.calibration_version,
        source_observation_id=payload.source_observation_id,
        source_detection_id=payload.source_detection_id,
        command_digest=payload.command_digest,
    )


def _m3a_local_context(payload: M3aSpatialExecutionContext) -> LocalM3aContext:
    """Convert the wire context into the policy's immutable local context."""

    return LocalM3aContext(
        operation_id=payload.operation_id,
        intent_revision=payload.intent_revision,
        contract_id=payload.contract_id,
        contract_revision=payload.contract_revision,
        task_id=payload.task_id,
        semantic_effect_id=payload.semantic_effect_id,
        target_entity_id=payload.target_entity_id,
        reference_observation_id=payload.reference_observation_id,
        reference_detection_id=payload.reference_detection_id,
        reference_digest=payload.reference_digest,
        reference_pose=payload.reference_pose,
        reference_frame_id=payload.reference_frame_id,
        reference_calibration_version=payload.reference_calibration_version,
        reference_world_revision=payload.reference_world_revision,
        reference_observed_at=payload.reference_observed_at,
        current_observation_envelope_id=payload.current_observation_envelope_id,
        current_observation=_m3a_local_observation(payload.current_observation),
        reference_observation=(
            _m3a_local_observation(payload.reference_observation)
            if payload.reference_observation is not None
            else None
        ),
        same_identity_only=payload.same_identity_only,
        max_displacement_m=payload.max_displacement_m,
        expires_at=payload.expires_at,
        expected_device_id=payload.expected_device_id,
    )


def _m3a_intent_digest(payload: M3aEnsureLatchedIntent) -> str:
    return m3a_canonical_digest(payload.model_dump(mode="python"))


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
    DEFAULT_EXTERNAL_MAX_ELAPSED_SECONDS = 60.0

    def __init__(
        self,
        store: NodeStore,
        factory: EnvelopeFactory,
        *,
        field_id: str = "field-1",
        phase_duration: float = 0.05,
        external_effect_adapter: ExternalEffectAdapter | None = None,
        max_elapsed_seconds: float = DEFAULT_EXTERNAL_MAX_ELAPSED_SECONDS,
        emit: EventSink = _ignore_event,
    ) -> None:
        super().__init__(store, factory, emit=emit)
        self.field_id = field_id
        self.phase_duration = phase_duration
        self.external_effect_adapter = external_effect_adapter
        if external_effect_adapter is not None:
            if isinstance(max_elapsed_seconds, bool):
                raise ValueError("max_elapsed_seconds must be a finite positive float")
            try:
                import math

                valid_window = float(max_elapsed_seconds)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "max_elapsed_seconds must be a finite positive float"
                ) from error
            if not math.isfinite(valid_window) or valid_window <= 0.0:
                raise ValueError("max_elapsed_seconds must be a finite positive float")
            self.max_elapsed_seconds = valid_window
        else:
            # The local policy is external-adapter-only.  Retain the value for
            # introspection while leaving the historical dummy path untouched.
            self.max_elapsed_seconds = max_elapsed_seconds

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
        existing_journal = self.store.find_execution_journal(
            contract.contract_id, contract.contract_revision
        )
        legacy_classification = self.store.budget_legacy_classification(
            contract.contract_id, contract.contract_revision
        )
        if legacy_classification == LEGACY_UNBUDGETED_HOLD:
            self._complete_budget_denial(
                envelope,
                operation_id=contract.operation_id,
                reason=LEGACY_UNBUDGETED_HOLD,
                previous_state=ContractState.ACCEPTED,
            )
            return
        if not external_mode and self.store.find_autonomy_budget(
            contract.contract_id, contract.contract_revision
        ) is not None:
            raise RecordConflictError(
                "durable external autonomy budget requires its original adapter"
            )
        # A pre-v4 dispatch without a durable device identity cannot be
        # admitted as a new budget.  Let the immutable recovery guard below
        # reject an injected adapter with the historical, precise reason.
        historical_dispatch_without_device = (
            existing_journal is not None
            and existing_journal["dispatch_recorded_at"] is not None
            and existing_journal["dispatch_device_id"] is None
        )
        if (
            external_mode
            and legacy_classification != LEGACY_OBSERVE_ONLY
            and not historical_dispatch_without_device
        ):
            try:
                accepted_now = self.store.admit_external_budget_contract(
                    contract_id=contract.contract_id,
                    contract_revision=contract.contract_revision,
                    operation_id=contract.operation_id,
                    task_id=assignment.task_id,
                    effect_key=effect_key,
                    accepted_at=self.clock.now(),
                    max_elapsed_seconds=self.max_elapsed_seconds,
                )
            except BudgetScopeConflictError as error:
                self._complete_budget_denial(
                    envelope,
                    operation_id=contract.operation_id,
                    reason=BUDGET_SCOPE_CONFLICT,
                    previous_state=ContractState.RECEIVED,
                )
                self.emit(
                    "robot.contract_held",
                    {
                        "contract_id": str(contract.contract_id),
                        "reason": BUDGET_SCOPE_CONFLICT,
                        "bound_contract_id": error.bound_contract_id,
                    },
                )
                return
        elif external_mode:
            # A v3 dispatch with a durable device identity has no policy
            # snapshot.  Migration marks it observe-only; skip admission and
            # all current-policy comparisons during replay/recovery.
            accepted_now = False
        else:
            accepted_now = self.store.accept_contract(
                contract_id=contract.contract_id,
                contract_revision=contract.contract_revision,
                operation_id=contract.operation_id,
                task_id=assignment.task_id,
                effect_key=effect_key,
                accepted_at=self.clock.now(),
            )
            # Recheck after the atomic journal admission as another Robot
            # process may have admitted this contract's external budget between
            # the initial read above and this transaction.
            if self.store.find_autonomy_budget(
                contract.contract_id, contract.contract_revision
            ) is not None:
                raise RecordConflictError(
                    "durable external autonomy budget requires its original adapter"
                )
        journal = self._journal(contract)
        self._assert_external_dispatch_configuration(journal, external_device_id)
        if journal["state"] in {state.value for state in TERMINAL_STATES}:
            if external_mode and journal["dispatch_recorded_at"] is None:
                denial_event = self.store.budget_denial_event(
                    contract.contract_id, contract.contract_revision
                )
                if denial_event is not None:
                    self._enqueue_if_absent(denial_event)
                else:
                    self._enqueue_terminal_replay(envelope, journal)
            else:
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
        if external_mode:
            if journal["state"] == ContractState.ACCEPTED.value:
                try:
                    dispatch_recorded_now = self.store.reserve_external_dispatch_with_budget(
                        contract.contract_id,
                        contract.contract_revision,
                        recorded_at=self.clock.now(),
                        device_id=external_device_id or "",
                        max_elapsed_seconds=self.max_elapsed_seconds,
                    )
                except (
                    BudgetPolicyConflictError,
                    BudgetDeadlineError,
                    BudgetLimitError,
                ) as error:
                    reason = self._budget_denial_reason(error)
                    self._complete_budget_denial(
                        envelope,
                        operation_id=contract.operation_id,
                        reason=reason,
                        previous_state=ContractState.ACCEPTED,
                    )
                    self.emit(
                        "robot.contract_held",
                        {"contract_id": str(contract.contract_id), "reason": reason},
                    )
                    return
                journal = self._journal(contract)
                self._enqueue_transition(
                    envelope,
                    ContractState.ACCEPTED,
                    ContractState.DISPATCH_RECORDED,
                    ordinal=2,
                    occurred_at=_durable_timestamp(
                        journal["dispatch_recorded_at"],
                        field_name="dispatch_recorded_at",
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
            journal = self._journal(contract)
            self._assert_external_dispatch_configuration(journal, external_device_id)
            await self._process_external_contract(
                envelope,
                effect_key=effect_key,
                dispatch_recorded_now=dispatch_recorded_now,
                expected_device_id=external_device_id,
            )
            return

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

    @staticmethod
    def _budget_denial_reason(error: Exception) -> str:
        if isinstance(error, BudgetPolicyConflictError):
            return BUDGET_POLICY_CONFLICT
        if isinstance(error, BudgetDeadlineError):
            return BUDGET_DEADLINE_EXPIRED
        if isinstance(error, BudgetLimitError):
            return BUDGET_LIMIT_EXHAUSTED
        raise TypeError(f"unsupported budget denial: {type(error).__name__}")

    def _complete_budget_denial(
        self,
        envelope: MessageEnvelope,
        *,
        operation_id: UUID,
        reason: str,
        previous_state: ContractState,
    ) -> None:
        contract = envelope.payload
        if not isinstance(contract, ExecutionContract):
            raise ValueError("budget denial requires an execution contract envelope")
        denial_at = self.clock.now()
        held_event = self._transition(
            envelope,
            previous_state,
            ContractState.HELD,
            ordinal=1 if previous_state is ContractState.RECEIVED else 4,
            occurred_at=denial_at,
        )
        self.store.complete_budget_scope_denial(
            contract.contract_id,
            contract.contract_revision,
            operation_id=operation_id,
            reason=reason,
            first_envelope=envelope,
            held_event=held_event,
            inbox_message_id=envelope.message_id,
            processed_at=denial_at,
        )

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
        expected_contact: str | None = None,
        m3a_context: LocalM3aContext | None = None,
        command_digest: str | None = None,
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
        unverified_digest_diagnostic: str | None = None
        if m3a_context is not None:
            reported_digest = observation.details.get("command_digest")
            if command_digest is None:
                unverified_digest_diagnostic = "M3A_COMMAND_DIGEST_EXPECTATION_UNAVAILABLE"
            elif reported_digest is None:
                unverified_digest_diagnostic = "M3A_COMMAND_DIGEST_MISSING"
            elif reported_digest != command_digest:
                unverified_digest_diagnostic = "M3A_COMMAND_DIGEST_MISMATCH"
            if unverified_digest_diagnostic is not None:
                observation = ExternalEffectObservation(
                    effect_key=observation.effect_key,
                    device_id=observation.device_id,
                    outcome=ExternalOutcome.UNKNOWN,
                    observed_at=observation.observed_at,
                    observation_id=observation.observation_id,
                    details={
                        **dict(observation.details),
                        "command_digest_verified": False,
                        "command_digest_diagnostic": unverified_digest_diagnostic,
                        "expected_command_digest": command_digest,
                        "reported_command_digest": reported_digest,
                    },
                )
        # An external adapter's APPLIED proof establishes that a command made
        # contact with *something*.  M3a's semantic goal is narrower: the
        # intended button must be the contact.  Preserve the low-level proof
        # in ``details`` while resolving a B/NONE contact as a held semantic
        # goal, so a fixed-reference ablation cannot forge A success.
        if unverified_digest_diagnostic is None and expected_contact is not None and (
            observation.outcome is not ExternalOutcome.APPLIED
            or observation.details.get("contact") != expected_contact
        ):
            observation = ExternalEffectObservation(
                effect_key=observation.effect_key,
                device_id=observation.device_id,
                outcome=ExternalOutcome.NOT_APPLIED,
                observed_at=observation.observed_at,
                observation_id=observation.observation_id,
                details={
                    **dict(observation.details),
                    "command_executed": observation.outcome is ExternalOutcome.APPLIED,
                    "semantic_goal_attained": False,
                    "expected_contact": expected_contact,
                },
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
        m3a_outgoing: tuple[MessageEnvelope, ...] = ()
        if m3a_context is not None:
            if command_digest is not None:
                effect_envelope = (
                    self._m3a_unverified_effect_envelope(
                        envelope,
                        context=m3a_context,
                        command_digest=command_digest,
                        observation=observation,
                        terminal_event=terminal_event,
                        diagnostic=unverified_digest_diagnostic,
                    )
                    if unverified_digest_diagnostic is not None
                    else self._m3a_effect_envelope(
                        envelope,
                        context=m3a_context,
                        command_digest=command_digest,
                        observation=observation,
                        terminal_event=terminal_event,
                    )
                )
                m3a_outgoing = (effect_envelope,)
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
            outgoing=m3a_outgoing,
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
        previous_state = (
            ContractState.ACCEPTED
            if terminal is ContractState.HELD and journal.get("dispatch_recorded_at") is None
            else ContractState.RUNNING
        )
        replay_id = uuid5(NAMESPACE_URL, f"dtt-replay:{cause.message_id}:{terminal.value}")
        replay = self.factory.make(
            "execution.event",
            self.field_id,
            cause.correlation_id,
            ExecutionEvent(
                event_id=replay_id,
                contract_id=contract.contract_id,
                contract_revision=contract.contract_revision,
                previous_state=previous_state,
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


def _m3a_as_wire_observation(
    value: TwoButtonObservation | LocalM3aObservation,
) -> TwoButtonObservation:
    if isinstance(value, TwoButtonObservation):
        return value
    if isinstance(value, LocalM3aObservation):
        return _m3a_wire_observation(value)
    raise TypeError("M3a observation must be a wire or local TwoButtonObservation")


def _m3a_uuid(operation_id: UUID, label: str) -> UUID:
    """Return a stable identifier for one M3a bundle member."""

    return uuid5(NAMESPACE_URL, f"dtt-m3a:{operation_id}:1:{label}")


class _M3aMissionMixin:
    """Mission authoring and read-model support for the isolated M3a slice."""

    def submit_ensure_button_latched(
        self,
        reference_observation: TwoButtonObservation | LocalM3aObservation,
        *,
        current_observation: TwoButtonObservation | LocalM3aObservation | None = None,
        target_entity_id: str = "A",
        semantic_effect_id: str = "ensure-latched:A",
        max_displacement_m: float = 0.05,
        operation_id: UUID | None = None,
        correlation_id: UUID | None = None,
        observer_id: str | None = None,
        transit_seconds: float | None = None,
        expires_in_seconds: float | None = None,
    ) -> MessageEnvelope:
        """Persist one reference and author its delayed revision-1 intent.

        The caller supplies observer output; Mission never asks the device for
        a hidden position.  It persists and forwards the authoring reference,
        then authors the delayed intent.  Current observations are acquired
        locally by Field after the delayed transit.  The optional current value
        is retained only as a Mission-side compatibility/view record and is
        never forwarded to Field by this method.
        """

        reference = _m3a_as_wire_observation(reference_observation)
        current = (
            _m3a_as_wire_observation(current_observation)
            if current_observation is not None
            else None
        )
        operation = operation_id or self.factory.uuid_factory()
        correlation = correlation_id or operation
        observer = observer_id or reference.source_id
        delay = (
            self.configured_one_way_delay
            if transit_seconds is None
            else float(transit_seconds)
        )
        if delay < 0.0:
            raise ValueError("transit_seconds must be >= 0")
        now = self.clock.now()
        reference_envelope = self.factory.make(
            "m3a.two_button.observation",
            self.factory.node_id,
            correlation,
            reference,
            source_id=observer,
            created_at=now,
        )
        # Observer output is durably present at Mission before authoring.  A
        # duplicate submission keeps the first immutable payload.
        self.store.receive(reference_envelope, received_at=now)
        field_reference = self.factory.make(
            "m3a.two_button.observation",
            "field-1",
            correlation,
            reference,
            source_id=observer,
            source_boot_id=reference_envelope.source_boot_id,
            created_at=reference_envelope.created_at,
            message_id=_m3a_uuid(operation, "reference-field-envelope"),
        )
        self.store.enqueue(field_reference)

        not_before = reference_envelope.created_at + timedelta(seconds=delay)
        if current is not None:
            # TEST-ONLY compatibility: retain a Mission copy for the dedicated
            # view.  This value is never sent to Field; production callers use
            # record_m3a_current_observation after the delayed transit.
            mission_current = self.factory.make(
                "m3a.two_button.observation",
                self.factory.node_id,
                correlation,
                current,
                source_id=observer,
                source_boot_id=reference_envelope.source_boot_id,
                created_at=now,
                not_before=not_before,
                message_id=_m3a_uuid(operation, "current-mission-envelope"),
            )
            self.store.enqueue(mission_current)

        persisted_reference = next(
            (
                message.payload
                for message in self.store.inbox_messages()
                if isinstance(message.payload, TwoButtonObservation)
                and message.payload.observation_id == reference.observation_id
            ),
            None,
        )
        if persisted_reference is None:
            raise RecordConflictError("M3a reference observation was not persisted at Mission")
        detections = tuple(
            detection
            for detection in persisted_reference.detections
            if detection.detection_id
            and detection.candidate_entity_ids == (target_entity_id,)
        )
        if len(detections) != 1:
            raise ValueError("M3a authoring requires one unique target detection")
        expires_at = (
            now + timedelta(seconds=float(expires_in_seconds))
            if expires_in_seconds is not None
            else None
        )
        intent_payload = M3aEnsureLatchedIntent(
            operation_id=operation,
            intent_revision=1,
            semantic_effect_id=semantic_effect_id,
            target_entity_id=target_entity_id,
            desired_latched=True,
            reference_observation_id=persisted_reference.observation_id,
            reference_detection_id=detections[0].detection_id,
            reference_digest=persisted_reference.canonical_payload_digest,
            reference_pose=detections[0].pose,
            reference_frame_id=persisted_reference.frame_id,
            reference_calibration_version=persisted_reference.calibration_version,
            reference_world_revision=persisted_reference.world_revision,
            reference_observed_at=persisted_reference.observed_at,
            same_identity_only=True,
            max_displacement_m=float(max_displacement_m),
            expires_at=expires_at,
        )
        intent_envelope = self.factory.make(
            "m3a.ensure_latched.intent",
            "field-1",
            correlation,
            intent_payload,
            not_before=not_before,
            expires_at=expires_at,
            source_id=self.factory.node_id,
            created_at=now,
            message_id=_m3a_uuid(operation, "intent-envelope"),
        )
        self.store.enqueue(intent_envelope)
        self.emit(
            "mission.m3a_intent_submitted",
            {
                "operation_id": str(operation),
                "message_id": str(intent_envelope.message_id),
                "not_before": not_before.isoformat(),
            },
        )
        return intent_envelope

    # Common spellings used by small integration harnesses.
    submit_m3a_ensure_latched = submit_ensure_button_latched
    submit_m3a_intent = submit_ensure_button_latched

    def publish_m3a_current_observation(
        self,
        observation: TwoButtonObservation | LocalM3aObservation,
        *,
        operation_id: UUID,
        correlation_id: UUID | None = None,
        observer_id: str | None = None,
        transit_seconds: float | None = None,
    ) -> MessageEnvelope:
        """Schedule a pre-produced current observation (compatibility/test-only).

        The central M3a service path acquires current truth through Field's
        local ``record_m3a_current_observation`` after the virtual transit.
        This method remains only for older harnesses that explicitly model a
        separately transported current payload.
        """

        payload = _m3a_as_wire_observation(observation)
        correlation = correlation_id or operation_id
        delay = (
            self.configured_one_way_delay
            if transit_seconds is None
            else float(transit_seconds)
        )
        created_at = self.clock.now()
        envelope = self.factory.make(
            "m3a.two_button.observation",
            "field-1",
            correlation,
            payload,
            source_id=observer_id or payload.source_id,
            not_before=created_at + timedelta(seconds=delay),
            created_at=created_at,
            message_id=_m3a_uuid(operation_id, f"current-field-envelope:{payload.observation_id}"),
        )
        self.store.enqueue(envelope)
        return envelope

    async def process_claimed(self, envelope: MessageEnvelope) -> None:
        if isinstance(envelope.payload, M3aSpatialExecutionContext):
            if (
                envelope.source_id != "field-1"
                or envelope.destination_id != self.factory.node_id
            ):
                self.store.complete_inbox(
                    envelope.message_id,
                    processed_at=self.clock.now(),
                    handler_result_reference="mission-m3a-context-rejected:transport",
                )
                return
            try:
                self.store.bind_m3a_context(
                    envelope,
                    bound_at=self.clock.now(),
                )
            except RecordConflictError as error:
                self.store.complete_inbox(
                    envelope.message_id,
                    processed_at=self.clock.now(),
                    handler_result_reference=f"mission-m3a-context-conflict:{error}",
                )
                return
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference="mission-m3a-context-bound",
            )
            return
        if isinstance(envelope.payload, TwoButtonEffectEvidence):
            self._process_m3a_effect_at_mission(envelope)
            return
        if isinstance(
            envelope.payload,
            (
                TwoButtonObservation,
                M3aEnsureLatchedIntent,
                M3aSpatialExecutionContext,
                SpatialPressCommand,
                TwoButtonLevelEvidence,
                TwoButtonEffectEvidence,
                LocalTwoButtonDecision,
            ),
        ):
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference=f"mission-m3a:{envelope.message_type}",
            )
            self.emit(
                "mission.m3a_message",
                {
                    "message_id": str(envelope.message_id),
                    "message_type": envelope.message_type,
                },
            )
            return
        await super().process_claimed(envelope)

    def _m3a_effect_is_attributable(
        self,
        message: MessageEnvelope,
        *,
        intent_message: MessageEnvelope,
        messages: tuple[MessageEnvelope, ...],
    ) -> bool:
        """Keep only effect proofs bound to the canonical M3a evidence chain."""

        effect = message.payload
        intent = intent_message.payload
        if not isinstance(effect, TwoButtonEffectEvidence) or not isinstance(
            intent, M3aEnsureLatchedIntent
        ):
            return False
        if (
            message.source_id != "field-1"
            or message.destination_id != "mission-1"
            or message.correlation_id != intent_message.correlation_id
            or effect.operation_id != intent.operation_id
            or effect.intent_revision != intent.intent_revision
            or effect.semantic_effect_id != intent.semantic_effect_id
            or effect.target_entity_id != intent.target_entity_id
        ):
            return False
        context_binding = self.store.find_m3a_context_binding(
            effect.contract_id,
            effect.contract_revision,
        )
        if context_binding is None or (
            context_binding.get("operation_id") != str(intent.operation_id)
            or context_binding.get("intent_revision") != intent.intent_revision
            or context_binding.get("source_id") != "field-1"
            or context_binding.get("correlation_id") != str(intent_message.correlation_id)
        ):
            return False
        expected_contexts = tuple(
            candidate.payload
            for candidate in messages
            if isinstance(candidate.payload, M3aSpatialExecutionContext)
            and candidate.source_id == "field-1"
            and candidate.destination_id == "mission-1"
            and candidate.correlation_id == intent_message.correlation_id
            and candidate.payload.operation_id == intent.operation_id
            and candidate.payload.intent_revision == intent.intent_revision
            and candidate.payload.contract_id == effect.contract_id
            and candidate.payload.contract_revision == effect.contract_revision
            and candidate.payload.semantic_effect_id == intent.semantic_effect_id
            and candidate.payload.target_entity_id == intent.target_entity_id
            and m3a_canonical_digest(candidate.payload.model_dump(mode="json"))
            == context_binding.get("context_digest")
        )
        if not expected_contexts:
            return False
        context = expected_contexts[0]
        expected_device_id = context.expected_device_id
        if not expected_device_id or effect.device_id != expected_device_id:
            return False
        terminal_contract_ids = {
            candidate.payload.contract_id
            for candidate in messages
            if isinstance(candidate.payload, ExecutionEvent)
            and candidate.source_id == "field-1"
            and candidate.destination_id == "mission-1"
            and candidate.payload.next_state in TERMINAL_STATES
            and candidate.correlation_id == intent_message.correlation_id
        }
        if effect.contract_id not in terminal_contract_ids:
            return False
        expected_effect_key = f"press:{effect.operation_id}:{effect.contract_revision}"
        if effect.effect_key != expected_effect_key:
            return False
        matching_commands = {
            candidate.payload.command_digest
            for candidate in messages
            if isinstance(candidate.payload, SpatialPressCommand)
            and candidate.source_id == "field-1"
            and candidate.destination_id == "mission-1"
            and candidate.correlation_id == intent_message.correlation_id
            and candidate.payload.effect_key == expected_effect_key
        }
        if effect.command_digest not in matching_commands:
            return False
        matching_decisions = tuple(
            candidate.payload
            for candidate in messages
            if isinstance(candidate.payload, LocalTwoButtonDecision)
            and candidate.source_id == "field-1"
            and candidate.destination_id == "mission-1"
            and candidate.correlation_id == intent_message.correlation_id
            and candidate.payload.operation_id == intent.operation_id
            and candidate.payload.semantic_effect_id == intent.semantic_effect_id
            and candidate.payload.reference_observation_id
            == intent.reference_observation_id
            and candidate.payload.command_digest == effect.command_digest
        )
        if not matching_decisions:
            return False
        if not any(
            candidate.action in {
                M3aAction.EXECUTE,
                M3aAction.REANCHOR_EXECUTE,
            }
            for candidate in matching_decisions
        ):
            return False
        if not effect.command_digest_verified:
            if (
                effect.outcome != "UNKNOWN"
                or effect.physical_contact is not None
                or effect.command_executed is not None
                or effect.semantic_goal_attained is not None
                or effect.a_counter is not None
                or effect.b_counter is not None
                or effect.a_latched is not None
                or effect.b_latched is not None
            ):
                return False
            return True
        if effect.outcome == "APPLIED" and (
            effect.command_executed is not True
            or effect.semantic_goal_attained is not True
            or effect.physical_contact != effect.target_entity_id
        ):
            return False
        return not (
            effect.semantic_goal_attained is True and effect.outcome != "APPLIED"
        )

    def _m3a_effect_matches_binding(
        self,
        message: MessageEnvelope,
        binding: Mapping[str, Any],
    ) -> bool:
        """Check a message against the persisted effect and semantic provenance."""

        effect = message.payload
        bound_effect = binding.get("effect")
        if not isinstance(effect, TwoButtonEffectEvidence) or not isinstance(
            bound_effect, TwoButtonEffectEvidence
        ):
            return False
        return (
            binding.get("operation_id") == str(effect.operation_id)
            and binding.get("intent_revision") == effect.intent_revision
            and binding.get("effect_digest")
            == m3a_canonical_digest(effect.model_dump(mode="json"))
            and bound_effect.model_dump(mode="json") == effect.model_dump(mode="json")
            and binding.get("source_id") == message.source_id
            and binding.get("destination_id") == message.destination_id
            and binding.get("correlation_id") == str(message.correlation_id)
        )

    def _m3a_effect_missing_dependency(
        self,
        effect: TwoButtonEffectEvidence,
        *,
        intent_message: MessageEnvelope,
        messages: tuple[MessageEnvelope, ...],
    ) -> str | None:
        """Return a retry reason when Mission has not received chain facts yet."""

        intent = intent_message.payload
        assert isinstance(intent, M3aEnsureLatchedIntent)
        context_binding = self.store.find_m3a_context_binding(
            effect.contract_id,
            effect.contract_revision,
        )
        if context_binding is None:
            return "EFFECT_CONTEXT_NOT_YET_PERSISTED"
        contexts = tuple(
            candidate.payload
            for candidate in messages
            if isinstance(candidate.payload, M3aSpatialExecutionContext)
            and candidate.source_id == "field-1"
            and candidate.destination_id == self.factory.node_id
            and candidate.correlation_id == intent_message.correlation_id
            and candidate.payload.operation_id == intent.operation_id
            and candidate.payload.intent_revision == intent.intent_revision
            and candidate.payload.contract_id == effect.contract_id
            and candidate.payload.contract_revision == effect.contract_revision
            and candidate.payload.semantic_effect_id == intent.semantic_effect_id
            and candidate.payload.target_entity_id == intent.target_entity_id
            and m3a_canonical_digest(candidate.payload.model_dump(mode="json"))
            == context_binding.get("context_digest")
        )
        if not contexts:
            return "EFFECT_CONTEXT_NOT_YET_PERSISTED"
        context = contexts[0]
        if effect.device_id != context.expected_device_id:
            return None
        terminal_events = tuple(
            candidate.payload
            for candidate in messages
            if isinstance(candidate.payload, ExecutionEvent)
            and candidate.source_id == "field-1"
            and candidate.destination_id == self.factory.node_id
            and candidate.correlation_id == intent_message.correlation_id
            and candidate.payload.next_state in TERMINAL_STATES
            and candidate.payload.contract_id == effect.contract_id
            and candidate.payload.contract_revision == effect.contract_revision
        )
        if not terminal_events:
            return "EFFECT_TERMINAL_NOT_YET_PERSISTED"
        expected_effect_key = f"press:{effect.operation_id}:{effect.contract_revision}"
        commands = tuple(
            candidate.payload
            for candidate in messages
            if isinstance(candidate.payload, SpatialPressCommand)
            and candidate.source_id == "field-1"
            and candidate.destination_id == self.factory.node_id
            and candidate.correlation_id == intent_message.correlation_id
            and candidate.payload.effect_key == expected_effect_key
        )
        if not commands:
            return "EFFECT_COMMAND_NOT_YET_PERSISTED"
        if effect.command_digest not in {command.command_digest for command in commands}:
            return None
        decisions = tuple(
            candidate.payload
            for candidate in messages
            if isinstance(candidate.payload, LocalTwoButtonDecision)
            and candidate.source_id == "field-1"
            and candidate.destination_id == self.factory.node_id
            and candidate.correlation_id == intent_message.correlation_id
            and candidate.payload.operation_id == effect.operation_id
        )
        if not decisions:
            return "EFFECT_DECISION_NOT_YET_PERSISTED"
        if not any(decision.command_digest == effect.command_digest for decision in decisions):
            return None
        return None

    def _m3a_effect_for_view(
        self,
        candidates: tuple[MessageEnvelope, ...],
    ) -> MessageEnvelope | None:
        """Read the durable first proof, keeping later transport copies inert."""

        ordered = tuple(
            sorted(candidates, key=lambda value: (value.created_at, str(value.message_id)))
        )
        for candidate in ordered:
            effect = candidate.payload
            assert isinstance(effect, TwoButtonEffectEvidence)
            binding = self.store.find_m3a_effect_binding(
                effect.contract_id,
                effect.contract_revision,
            )
            if binding is None:
                continue
            canonical = binding.get("effect_envelope")
            if isinstance(canonical, MessageEnvelope) and self._m3a_effect_matches_binding(
                canonical,
                binding,
            ):
                return canonical
        for candidate in ordered:
            effect = candidate.payload
            assert isinstance(effect, TwoButtonEffectEvidence)
            diagnostic = self.store.find_m3a_effect_diagnostic(
                effect.contract_id,
                effect.contract_revision,
            )
            if diagnostic is None:
                continue
            canonical = diagnostic.get("effect_envelope")
            if isinstance(canonical, MessageEnvelope) and self._m3a_effect_matches_binding(
                canonical,
                diagnostic,
            ):
                return canonical
        # Unverified UNKNOWN is exposed only from its durable diagnostic record;
        # arbitrary transport copies never become view facts.
        return None

    def _process_m3a_effect_at_mission(self, envelope: MessageEnvelope) -> None:
        """Bind a validated Mission-side copy before exposing it in the view."""

        effect = envelope.payload
        assert isinstance(effect, TwoButtonEffectEvidence)
        messages = tuple(self.store.inbox_messages()) + tuple(self.store.outbox_messages())
        intents = tuple(
            message
            for message in messages
            if isinstance(message.payload, M3aEnsureLatchedIntent)
            and message.payload.operation_id == effect.operation_id
            and message.correlation_id == envelope.correlation_id
        )
        intent_message = max(
            intents,
            key=lambda message: (message.created_at, str(message.message_id)),
            default=None,
        )
        route_matches = (
            envelope.source_id == "field-1"
            and envelope.destination_id == self.factory.node_id
            and intent_message is not None
            and envelope.correlation_id == intent_message.correlation_id
            and effect.operation_id == intent_message.payload.operation_id
            and effect.intent_revision == intent_message.payload.intent_revision
            and effect.semantic_effect_id == intent_message.payload.semantic_effect_id
            and effect.target_entity_id == intent_message.payload.target_entity_id
        )
        if intent_message is None:
            if (
                envelope.source_id == "field-1"
                and envelope.destination_id == self.factory.node_id
            ):
                self.store.fail_inbox(
                    envelope.message_id,
                    {"reason": "EFFECT_INTENT_NOT_YET_PERSISTED", "retryable": True},
                )
            else:
                self.store.complete_inbox(
                    envelope.message_id,
                    processed_at=self.clock.now(),
                    handler_result_reference="mission-m3a-effect-rejected:attribution",
                )
            return
        if not route_matches:
            if self.store.find_m3a_effect_binding(
                effect.contract_id,
                effect.contract_revision,
            ) is not None:
                self.store.record_m3a_effect_conflict(
                    envelope,
                    reason="EFFECT_ROUTE_OR_INTENT_MISMATCH",
                    recorded_at=self.clock.now(),
                )
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference="mission-m3a-effect-rejected:attribution",
            )
            return
        missing_dependency = self._m3a_effect_missing_dependency(
            effect,
            intent_message=intent_message,
            messages=messages,
        )
        if missing_dependency is not None:
            self.store.fail_inbox(
                envelope.message_id,
                {"reason": missing_dependency, "retryable": True},
            )
            return
        if not self._m3a_effect_is_attributable(
            envelope,
            intent_message=intent_message,
            messages=messages,
        ):
            if self.store.find_m3a_effect_binding(
                effect.contract_id,
                effect.contract_revision,
            ) is not None:
                self.store.record_m3a_effect_conflict(
                    envelope,
                    reason="EFFECT_ATTRIBUTION_MISMATCH",
                    recorded_at=self.clock.now(),
                )
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference="mission-m3a-effect-rejected:attribution",
            )
            return
        try:
            self.store.bind_m3a_effect(
                envelope,
                bound_at=self.clock.now(),
            )
        except RecordConflictError as error:
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference=f"mission-m3a-effect-conflict:{error}",
            )
            return
        if (
            not effect.command_digest_verified
            and self.store.find_m3a_effect_binding(
                effect.contract_id,
                effect.contract_revision,
            )
            is None
        ):
            try:
                self.store.bind_m3a_effect_diagnostic(
                    envelope,
                    recorded_at=self.clock.now(),
                )
            except RecordConflictError as error:
                self.store.complete_inbox(
                    envelope.message_id,
                    processed_at=self.clock.now(),
                    handler_result_reference=f"mission-m3a-effect-diagnostic-conflict:{error}",
                )
                return
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=self.clock.now(),
            handler_result_reference="mission-m3a-effect-bound",
        )
        self.emit(
            "mission.m3a_message",
            {
                "message_id": str(envelope.message_id),
                "message_type": envelope.message_type,
            },
        )

    def m3a_view_state(self) -> M3aMissionViewState:
        messages = tuple(self.store.inbox_messages()) + tuple(self.store.outbox_messages())
        intent_message = max(
            (
                message
                for message in messages
                if isinstance(message.payload, M3aEnsureLatchedIntent)
            ),
            key=lambda message: (message.created_at, str(message.message_id)),
            default=None,
        )
        operation_id = intent_message.payload.operation_id if intent_message else None
        correlation_id = intent_message.correlation_id if intent_message else None
        scoped = tuple(
            message
            for message in messages
            if operation_id is not None
            and (
                message.correlation_id == correlation_id
                or getattr(message.payload, "operation_id", None) == operation_id
                or (
                    isinstance(message.payload, ExecutionEvent)
                    and message.payload.contract_id
                    in {
                        candidate.payload.contract_id
                        for candidate in messages
                        if isinstance(candidate.payload, ExecutionContract)
                        and candidate.payload.operation_id == operation_id
                    }
                )
            )
        )
        reference = next(
            (
                message.payload
                for message in scoped
                if isinstance(message.payload, TwoButtonObservation)
                and intent_message is not None
                and message.payload.observation_id
                == intent_message.payload.reference_observation_id
            ),
            None,
        )
        decision_message = max(
            (
                message
                for message in scoped
                if isinstance(message.payload, LocalTwoButtonDecision)
            ),
            key=lambda message: (message.created_at, str(message.message_id)),
            default=None,
        )
        current = next(
            (
                message.payload
                for message in scoped
                if isinstance(message.payload, TwoButtonObservation)
                and decision_message is not None
                and message.payload.observation_id
                == decision_message.payload.current_observation_id
            ),
            None,
        )
        level_message = max(
            (
                message
                for message in scoped
                if isinstance(message.payload, TwoButtonLevelEvidence)
            ),
            key=lambda message: (message.created_at, str(message.message_id)),
            default=None,
        )
        command_message = max(
            (
                message
                for message in scoped
                if isinstance(message.payload, SpatialPressCommand)
            ),
            key=lambda message: (message.created_at, str(message.message_id)),
            default=None,
        )
        contracts = tuple(
            message.payload
            for message in scoped
            if isinstance(message.payload, ExecutionContract)
        )
        contract = max(contracts, key=lambda value: str(value.contract_id), default=None)
        terminal_message = max(
            (
                message
                for message in scoped
                if isinstance(message.payload, ExecutionEvent)
                and message.payload.next_state in TERMINAL_STATES
            ),
            key=lambda message: (message.payload.occurred_at, str(message.message_id)),
            default=None,
        )
        attributable_effects = tuple(
            message
            for message in scoped
            if isinstance(message.payload, TwoButtonEffectEvidence)
            and intent_message is not None
            and self._m3a_effect_is_attributable(
                message,
                intent_message=intent_message,
                messages=messages,
            )
        )
        effect_message = self._m3a_effect_for_view(attributable_effects)
        level = level_message.payload if level_message else None
        effect = effect_message.payload if effect_message else None
        resolved_contract_id = (
            contract.contract_id
            if contract is not None
            else effect.contract_id
            if effect is not None
            else terminal_message.payload.contract_id
            if terminal_message is not None
            else None
        )
        effect_result = effect.model_dump(mode="json") if effect is not None else None
        if effect is not None:
            business_result = (
                "APPLIED"
                if effect.semantic_goal_attained is True
                else "NOT_APPLIED_AFTER_UNCERTAIN_DISPATCH"
                if effect.outcome == "NOT_APPLIED"
                else "OUTCOME_UNKNOWN"
            )
        else:
            business_result = (
                "RECOGNIZED_ALREADY_EFFECTIVE"
                if decision_message is not None
                and decision_message.payload.action is M3aAction.RECOGNIZE_EFFECT
                else None
            )
        return M3aMissionViewState(
            source_id=self.factory.node_id,
            source_sequence=max(1, self._view_sequence + 1),
            produced_at=self.clock.now(),
            operation_id=operation_id,
            correlation_id=correlation_id,
            configured_one_way_delay_seconds=self.configured_one_way_delay,
            reference_observation=reference,
            current_observation=current,
            level_evidence=level,
            decision=decision_message.payload if decision_message else None,
            command=command_message.payload if command_message else None,
            effect_evidence=effect,
            contract_id=resolved_contract_id,
            contract_state=(
                terminal_message.payload.next_state
                if terminal_message is not None
                else None
            ),
            terminal_result=effect_result,
            business_result=business_result,
            physical_contact=effect.physical_contact if effect is not None else None,
            a_counter=effect.a_counter if effect is not None else None,
            b_counter=effect.b_counter if effect is not None else None,
            a_latched=effect.a_latched if effect is not None else None,
            b_latched=effect.b_latched if effect is not None else None,
        )

    def m3a_view(self) -> dict[str, Any]:
        return self.m3a_view_state().model_dump(mode="json")

    view_m3a = m3a_view


class M3aMissionService(_M3aMissionMixin, MissionService):
    """Mission service with the M3a observed-spatial authoring path enabled."""


class _M3aFieldMixin:
    """Field validation, immutable root binding, and M3a forwarding."""

    m3a_device_id = "two-button-device-1"

    def record_m3a_current_observation(
        self,
        observation: TwoButtonObservation | LocalM3aObservation,
        *,
        operation_id: UUID,
        correlation_id: UUID | None = None,
        observer_id: str | None = None,
    ) -> MessageEnvelope:
        """Persist one current observation acquired by Field's local observer.

        This is a local observer entry point: callers invoke it after the
        Mission-to-Field delay, and the payload is written directly to Field's
        inbox.  Field forwards the same observed payload to Mission only after
        its local inbox completion, so the read model can expose the current
        observation without making Mission the observation author.  The method
        does not inspect fixture truth.  The returned root envelope can be
        handed to :meth:`RuntimeService.handle` to run the normal durable inbox path.
        """

        payload = _m3a_as_wire_observation(observation)
        correlation = correlation_id or operation_id
        now = self.clock.now()
        envelope = self.factory.make(
            "m3a.two_button.observation",
            self.factory.node_id,
            correlation,
            payload,
            source_id=observer_id or payload.source_id,
            created_at=now,
            message_id=_m3a_uuid(
                operation_id,
                f"current-field-local:{payload.observation_id}",
            ),
        )
        self.store.receive(envelope, received_at=now)
        self.emit(
            "field.m3a_current_observation_recorded",
            {
                "operation_id": str(operation_id),
                "observation_id": payload.observation_id,
                "message_id": str(envelope.message_id),
                "observed_at": payload.observed_at.isoformat(),
            },
        )
        return envelope

    def _m3a_operation_bundle(self, operation_id: UUID) -> tuple[MessageEnvelope, ...]:
        payloads = tuple(
            message
            for message in self.store.outbox_messages()
            if (
                isinstance(message.payload, (SiteSnapshot, GroundedOperation, OperationPlan))
                and getattr(message.payload, "operation_id", None) == operation_id
            )
            or (
                isinstance(message.payload, TaskAssignment)
                and any(
                    candidate.payload.plan_id == message.payload.plan_id
                    for candidate in self.store.outbox_messages()
                    if isinstance(candidate.payload, OperationPlan)
                    and candidate.payload.operation_id == operation_id
                )
            )
            or (
                isinstance(
                    message.payload,
                    (ExecutionContract, M3aSpatialExecutionContext),
                )
                and message.payload.operation_id == operation_id
            )
        )
        return tuple(
            sorted(payloads, key=lambda message: (message.created_at, str(message.message_id)))
        )

    def _m3a_effect_validation_reason(
        self,
        envelope: MessageEnvelope,
        effect: TwoButtonEffectEvidence,
    ) -> str | None:
        """Validate post-effect attribution before forwarding it to Mission.

        A proof can arrive before the command or decision because each is an
        independently retried message.  In that case return a retryable reason;
        intrinsic source, contract, target, or digest mismatches are terminal
        for this proof and are never forwarded.
        """

        if envelope.source_id != self.robot_id:
            return "EFFECT_SOURCE_MISMATCH"
        messages = tuple(self.store.inbox_messages()) + tuple(self.store.outbox_messages())
        contexts = tuple(
            message
            for message in messages
            if isinstance(message.payload, M3aSpatialExecutionContext)
        )
        exact_contexts = tuple(
            message
            for message in contexts
            if message.correlation_id == envelope.correlation_id
            and message.payload.operation_id == effect.operation_id
            and message.payload.contract_id == effect.contract_id
            and message.payload.contract_revision == effect.contract_revision
        )
        if not exact_contexts:
            related_context = any(
                message.payload.operation_id == effect.operation_id
                or message.correlation_id == envelope.correlation_id
                for message in contexts
            )
            return (
                "EFFECT_CONTEXT_MISMATCH"
                if related_context
                else "EFFECT_CONTEXT_NOT_YET_PERSISTED"
            )
        context_digests = {
            m3a_canonical_digest(message.payload.model_dump(mode="json"))
            for message in exact_contexts
        }
        if len(context_digests) != 1:
            return "EFFECT_CONTEXT_CONFLICT"
        context = exact_contexts[0].payload
        expected_device_id = context.expected_device_id or self.m3a_device_id
        expected_effect_key = f"press:{effect.operation_id}:{effect.contract_revision}"
        if (
            effect.semantic_effect_id != context.semantic_effect_id
            or effect.target_entity_id != context.target_entity_id
            or effect.effect_key != expected_effect_key
            or effect.device_id != expected_device_id
        ):
            return "EFFECT_CONTEXT_BINDING_MISMATCH"

        commands = tuple(
            message.payload
            for message in messages
            if isinstance(message.payload, SpatialPressCommand)
            and message.source_id == self.robot_id
            and message.destination_id == self.factory.node_id
            and message.correlation_id == envelope.correlation_id
            and message.payload.effect_key == expected_effect_key
        )
        if not commands:
            return "EFFECT_COMMAND_NOT_YET_PERSISTED"
        command_digests = {command.command_digest for command in commands}
        if len(command_digests) != 1:
            return "EFFECT_COMMAND_CONFLICT"
        command = commands[0]
        if effect.command_digest != command.command_digest:
            return "EFFECT_COMMAND_DIGEST_MISMATCH"

        decisions = tuple(
            message.payload
            for message in messages
            if isinstance(message.payload, LocalTwoButtonDecision)
            and message.source_id == self.robot_id
            and message.destination_id == self.factory.node_id
            and message.correlation_id == envelope.correlation_id
            and message.payload.operation_id == effect.operation_id
        )
        if not decisions:
            return "EFFECT_DECISION_NOT_YET_PERSISTED"
        decision_digests = {decision.command_digest for decision in decisions}
        if len(decision_digests) != 1:
            return "EFFECT_DECISION_CONFLICT"
        decision = decisions[0]
        if decision.command_digest != effect.command_digest:
            return "EFFECT_DECISION_DIGEST_MISMATCH"
        if not effect.command_digest_verified:
            if (
                effect.outcome != "UNKNOWN"
                or effect.physical_contact is not None
                or effect.command_executed is not None
                or effect.semantic_goal_attained is not None
                or effect.a_counter is not None
                or effect.b_counter is not None
                or effect.a_latched is not None
                or effect.b_latched is not None
            ):
                return "EFFECT_UNVERIFIED_PROOF_FIELDS"
            return None
        if effect.outcome == "APPLIED" and (
            effect.command_executed is not True
            or effect.semantic_goal_attained is not True
            or effect.physical_contact != effect.target_entity_id
        ):
            return "EFFECT_SEMANTIC_PROOF_MISSING"
        if effect.semantic_goal_attained is True and effect.outcome != "APPLIED":
            return "EFFECT_SEMANTIC_OUTCOME_MISMATCH"
        return None

    def _process_m3a_effect_evidence(self, envelope: MessageEnvelope) -> None:
        effect = envelope.payload
        assert isinstance(effect, TwoButtonEffectEvidence)
        reason = self._m3a_effect_validation_reason(envelope, effect)
        if reason is None:
            try:
                self.store.bind_m3a_effect(
                    envelope,
                    bound_at=self.clock.now(),
                )
            except RecordConflictError as error:
                self._m3a_complete_no_bundle(
                    envelope,
                    result=f"m3a-effect-conflict:{error}",
                )
                return
            binding = self.store.find_m3a_effect_binding(
                effect.contract_id,
                effect.contract_revision,
            )
            if binding is None and not effect.command_digest_verified:
                try:
                    self.store.bind_m3a_effect_diagnostic(
                        envelope,
                        recorded_at=self.clock.now(),
                    )
                except RecordConflictError as error:
                    self._m3a_complete_no_bundle(
                        envelope,
                        result=f"m3a-effect-diagnostic-conflict:{error}",
                    )
                    return
                diagnostic = self.store.find_m3a_effect_diagnostic(
                    effect.contract_id,
                    effect.contract_revision,
                )
                if diagnostic is not None:
                    binding = diagnostic
            canonical_effect = (
                binding["effect"]
                if binding is not None
                and isinstance(binding.get("effect"), TwoButtonEffectEvidence)
                else effect
            )
            self._process_m3a_robot_message(envelope, payload=canonical_effect)
            return
        if reason.endswith("NOT_YET_PERSISTED"):
            self.store.fail_inbox(
                envelope.message_id,
                {"reason": reason, "retryable": True},
            )
            return
        if (
            self.store.find_m3a_effect_binding(
                effect.contract_id,
                effect.contract_revision,
            )
            is not None
        ):
            self.store.record_m3a_effect_conflict(
                envelope,
                reason=reason,
                recorded_at=self.clock.now(),
            )
        self._m3a_complete_no_bundle(
            envelope,
            result=f"m3a-effect-rejected:{reason}",
        )

    def _m3a_reference_candidates(
        self, observation_id: str, correlation_id: UUID
    ) -> tuple[MessageEnvelope, ...]:
        return tuple(
            message
            for message in self.store.inbox_messages()
            if isinstance(message.payload, TwoButtonObservation)
            and message.payload.observation_id == observation_id
            and message.correlation_id == correlation_id
        )

    def _m3a_current_candidate(
        self, reference: TwoButtonObservation, correlation_id: UUID
    ) -> MessageEnvelope | None:
        candidates = tuple(
            message
            for message in self.store.inbox_messages()
            if isinstance(message.payload, TwoButtonObservation)
            and message.correlation_id == correlation_id
            and message.payload.observation_id != reference.observation_id
            and message.payload.source_id == reference.source_id
        )
        return max(
            candidates,
            key=lambda message: (
                message.payload.world_revision,
                message.payload.observed_at,
                message.payload.produced_at,
                str(message.message_id),
            ),
            default=None,
        )

    def _m3a_hold_envelope(
        self,
        cause: MessageEnvelope,
        intent: M3aEnsureLatchedIntent,
        current: TwoButtonObservation,
        *,
        action: M3aAction,
        reason: str,
    ) -> MessageEnvelope:
        decision = LocalM3aDecision(
            operation_id=intent.operation_id,
            intent_revision=intent.intent_revision,
            semantic_effect_id=intent.semantic_effect_id,
            reference_observation_id=intent.reference_observation_id,
            current_observation_id=current.observation_id,
            action=action,
            reason=reason,
            budget_state="NOT_ADMITTED",
        )
        return self.factory.make(
            "m3a.spatial.decision",
            self.mission_id,
            cause.correlation_id,
            _m3a_wire_decision(decision),
            causation_id=cause.message_id,
            message_id=_m3a_uuid(intent.operation_id, f"field-hold:{reason}"),
        )

    def _m3a_complete_no_bundle(
        self,
        envelope: MessageEnvelope,
        *,
        result: str,
        outgoing: tuple[MessageEnvelope, ...] = (),
    ) -> None:
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=self.clock.now(),
            handler_result_reference=result,
            outgoing=outgoing,
        )

    def _m3a_make_bundle(
        self,
        envelope: MessageEnvelope,
        intent: M3aEnsureLatchedIntent,
        reference: TwoButtonObservation,
        current_message: MessageEnvelope,
    ) -> tuple[MessageEnvelope, ...]:
        now = self.clock.now()
        operation_id = intent.operation_id
        target_detection = next(
            detection
            for detection in reference.detections
            if detection.detection_id == intent.reference_detection_id
        )
        snapshot = self.factory.make(
            "site.snapshot",
            self.mission_id,
            envelope.correlation_id,
            SiteSnapshot(
                site_id="two-button-site-1",
                entities=(intent.target_entity_id,),
                robot_states=(
                    RobotState(
                        robot_id=self.robot_id,
                        pose=target_detection.pose,
                        evidence=evidence(
                            reference.source_id,
                            reference.observed_at,
                            ProvenanceKind.MEASURED,
                            world_revision=reference.world_revision,
                            produced_at=reference.produced_at,
                        ),
                    ),
                ),
                evidence=evidence(
                    reference.source_id,
                    reference.observed_at,
                    ProvenanceKind.MEASURED,
                    world_revision=reference.world_revision,
                    produced_at=reference.produced_at,
                ),
            ),
            causation_id=envelope.message_id,
            message_id=_m3a_uuid(operation_id, "snapshot-envelope"),
            created_at=now,
        )
        grounded = self.factory.make(
            "operation.grounded",
            self.mission_id,
            envelope.correlation_id,
            GroundedOperation(
                operation_id=operation_id,
                target_entity_id=intent.target_entity_id,
                target_pose=target_detection.pose,
                state=OperationState.ADMITTED,
                evidence=evidence(
                    reference.source_id,
                    reference.observed_at,
                    ProvenanceKind.MEASURED,
                    world_revision=reference.world_revision,
                    produced_at=reference.produced_at,
                ),
            ),
            causation_id=envelope.message_id,
            message_id=_m3a_uuid(operation_id, "grounded-envelope"),
            created_at=now + timedelta(microseconds=1),
        )
        task_id = _m3a_uuid(operation_id, "task")
        plan = self.factory.make(
            "operation.plan",
            self.mission_id,
            envelope.correlation_id,
            OperationPlan(
                plan_id=_m3a_uuid(operation_id, "plan"),
                operation_id=operation_id,
                tasks=(
                    TaskNode(
                        task_id=task_id,
                        skill=OperationType.PRESS_BUTTON,
                        target_entity_id=intent.target_entity_id,
                    ),
                ),
            ),
            causation_id=grounded.message_id,
            message_id=_m3a_uuid(operation_id, "plan-envelope"),
            created_at=now + timedelta(microseconds=2),
        )
        assignment = self.factory.make(
            "task.assignment",
            self.robot_id,
            envelope.correlation_id,
            TaskAssignment(
                assignment_id=_m3a_uuid(operation_id, "assignment"),
                plan_id=plan.payload.plan_id,
                task_id=task_id,
                executor_id=self.robot_id,
            ),
            causation_id=plan.message_id,
            message_id=_m3a_uuid(operation_id, "assignment-envelope"),
            created_at=now + timedelta(microseconds=3),
        )
        contract = self.factory.make(
            "execution.contract",
            self.robot_id,
            envelope.correlation_id,
            ExecutionContract(
                contract_id=_m3a_uuid(operation_id, "contract"),
                contract_revision=1,
                operation_id=operation_id,
                assignment_id=assignment.payload.assignment_id,
                state=ContractState.RECEIVED,
            ),
            causation_id=assignment.message_id,
            message_id=_m3a_uuid(operation_id, "contract-envelope"),
            created_at=now + timedelta(microseconds=4),
            expires_at=intent.expires_at,
        )
        context = self.factory.make(
            "m3a.spatial.context",
            self.robot_id,
            envelope.correlation_id,
            M3aSpatialExecutionContext(
                operation_id=operation_id,
                intent_revision=1,
                contract_id=contract.payload.contract_id,
                contract_revision=1,
                task_id=task_id,
                semantic_effect_id=intent.semantic_effect_id,
                target_entity_id=intent.target_entity_id,
                reference_observation_id=intent.reference_observation_id,
                reference_detection_id=intent.reference_detection_id,
                reference_digest=intent.reference_digest,
                reference_pose=intent.reference_pose,
                reference_frame_id=intent.reference_frame_id,
                reference_calibration_version=intent.reference_calibration_version,
                reference_world_revision=intent.reference_world_revision,
                reference_observed_at=intent.reference_observed_at,
                current_observation_envelope_id=current_message.message_id.__str__(),
                current_observation=current_message.payload,
                reference_observation=reference,
                same_identity_only=intent.same_identity_only,
                max_displacement_m=intent.max_displacement_m,
                expires_at=intent.expires_at,
                expected_device_id=self.m3a_device_id,
            ),
            causation_id=contract.message_id,
            message_id=_m3a_uuid(operation_id, "context-envelope"),
            created_at=now + timedelta(microseconds=5),
            expires_at=intent.expires_at,
        )
        # Mission receives an immutable copy of the Field context so its read
        # model can bind effect evidence to the same durable contract/device
        # identity.  The Robot copy above remains the only execution input.
        mission_context = self.factory.make(
            "m3a.spatial.context",
            self.mission_id,
            envelope.correlation_id,
            context.payload,
            causation_id=context.message_id,
            message_id=_m3a_uuid(operation_id, "context-mission-envelope"),
            created_at=now + timedelta(microseconds=6),
            expires_at=intent.expires_at,
        )
        # Deliberately place the contract before context.  Robot must remain
        # retryable until both assignment and matching context are durable;
        # tests can therefore deliver these dependencies in either order.
        return (snapshot, grounded, plan, assignment, contract, context, mission_context)

    def _process_m3a_observation(self, envelope: MessageEnvelope) -> None:
        outgoing: tuple[MessageEnvelope, ...] = ()
        # A local Field observer uses Field's boot ID.  Remote reference/current
        # messages retain their originating Mission boot ID and must not loop
        # back into Mission.  Completing the inbox with this forwarding copy
        # keeps local observation publication atomic with its processing state.
        if envelope.source_boot_id == self.factory.boot_id:
            payload = envelope.payload
            assert isinstance(payload, TwoButtonObservation)
            outgoing = (
                self.factory.make(
                    "m3a.two_button.observation",
                    self.mission_id,
                    envelope.correlation_id,
                    payload,
                    causation_id=envelope.message_id,
                    message_id=uuid5(
                        NAMESPACE_URL,
                        f"dtt-m3a:current-forward:{envelope.correlation_id}:{payload.observation_id}",
                    ),
                    created_at=self.clock.now(),
                ),
            )
        self._m3a_complete_no_bundle(
            envelope,
            result=f"m3a-observation:{getattr(envelope.payload, 'observation_id', '')}",
            outgoing=outgoing,
        )

    def _process_m3a_intent(self, envelope: MessageEnvelope) -> None:
        intent = envelope.payload
        assert isinstance(intent, M3aEnsureLatchedIntent)
        # The atomic root binding is checked before reading or creating a
        # bundle.  This classifies changed source/correlation/semantic claims
        # durably and leaves the first bundle untouched.
        semantic_fields = intent.model_dump(mode="json")
        digest = _m3a_intent_digest(intent)
        try:
            fresh_binding = self.store.bind_m3a_intent(
                operation_id=intent.operation_id,
                intent_revision=intent.intent_revision,
                canonical_intent_digest=digest,
                source_id=envelope.source_id,
                correlation_id=envelope.correlation_id,
                semantic_fields=semantic_fields,
                bound_at=self.clock.now(),
            )
        except RecordConflictError as error:
            self._m3a_complete_no_bundle(
                envelope,
                result=f"m3a-intent-conflict:{error}",
            )
            self.emit(
                "field.m3a_intent_conflict",
                {"operation_id": str(intent.operation_id), "reason": str(error)},
            )
            return

        if not fresh_binding:
            prior_bundle = self._m3a_operation_bundle(intent.operation_id)
            if prior_bundle:
                self._m3a_complete_no_bundle(
                    envelope,
                    result=f"m3a-duplicate:{intent.operation_id}",
                    outgoing=prior_bundle,
                )
                return

        references = self._m3a_reference_candidates(
            intent.reference_observation_id, envelope.correlation_id
        )
        if not references:
            # A reference must be durably observed before intent acceptance.
            self.store.fail_inbox(
                envelope.message_id,
                {"reason": "m3a-reference-not-yet-persisted", "retryable": True},
            )
            return
        reference = references[-1].payload
        assert isinstance(reference, TwoButtonObservation)
        if len(
            {
                (
                    candidate.payload.canonical_payload_digest,
                    candidate.payload.frame_id,
                    candidate.payload.calibration_version,
                )
                for candidate in references
            }
        ) > 1:
            # A repeated observation UUID is idempotent only when its payload
            # is byte-identical.  Do not let SQLite ordering choose between a
            # changed pose and the original authoring record.
            context_changed = any(
                candidate.payload.frame_id != intent.reference_frame_id
                or candidate.payload.calibration_version
                != intent.reference_calibration_version
                for candidate in references
            )
            current_message = self._m3a_current_candidate(
                reference, envelope.correlation_id
            )
            outgoing: tuple[MessageEnvelope, ...] = ()
            if current_message is not None:
                outgoing = (
                    self._m3a_hold_envelope(
                        envelope,
                        intent,
                        current_message.payload,
                        action=(
                            M3aAction.HOLD_CONTEXT_MISMATCH
                            if context_changed
                            else M3aAction.HOLD_REFERENCE_MISMATCH
                        ),
                        reason=(
                            "REFERENCE_CONTEXT_CONFLICT"
                            if context_changed
                            else "REFERENCE_OBSERVATION_UUID_CONFLICT"
                        ),
                    ),
                )
            self._m3a_complete_no_bundle(
                envelope,
                result="m3a-reference-uuid-conflict",
                outgoing=outgoing,
            )
            return
        try:
            local_intent = _m3a_local_intent(intent)
            local_reference = _m3a_local_observation(reference)
            verification = verify_reference(local_intent, local_reference)
        except (TypeError, ValueError) as error:
            verification = None
            self._m3a_complete_no_bundle(
                envelope,
                result=f"m3a-reference-mismatch:{error}",
            )
            return
        if verification is not None and not verification.valid:
            current_message = self._m3a_current_candidate(reference, envelope.correlation_id)
            outgoing: tuple[MessageEnvelope, ...] = ()
            if current_message is not None:
                outgoing = (
                    self._m3a_hold_envelope(
                        envelope,
                        intent,
                        current_message.payload,
                        action=M3aAction(verification.action or M3aAction.HOLD_REFERENCE_MISMATCH),
                        reason=verification.reason,
                    ),
                )
            self._m3a_complete_no_bundle(
                envelope,
                result=f"m3a-reference-hold:{verification.reason}",
                outgoing=outgoing,
            )
            return

        current_message = self._m3a_current_candidate(reference, envelope.correlation_id)
        if current_message is None or not isinstance(current_message.payload, TwoButtonObservation):
            self.store.fail_inbox(
                envelope.message_id,
                {"reason": "m3a-current-observation-not-yet-persisted", "retryable": True},
            )
            return
        current = current_message.payload
        if (
            current.source_id != reference.source_id
            or current.frame_id != intent.reference_frame_id
            or current.calibration_version != intent.reference_calibration_version
        ):
            decision = self._m3a_hold_envelope(
                envelope,
                intent,
                current,
                action=M3aAction.HOLD_CONTEXT_MISMATCH,
                reason="CURRENT_OBSERVATION_CONTEXT_MISMATCH",
            )
            self._m3a_complete_no_bundle(
                envelope,
                result="m3a-current-context-hold",
                outgoing=(decision,),
            )
            return
        if intent.expires_at is not None and self.clock.now() >= intent.expires_at:
            decision = self._m3a_hold_envelope(
                envelope,
                intent,
                current,
                action=M3aAction.HOLD_CONTEXT_MISMATCH,
                reason="INTENT_EXPIRED",
            )
            self._m3a_complete_no_bundle(
                envelope,
                result="m3a-expired",
                outgoing=(decision,),
            )
            return

        outgoing = self._m3a_make_bundle(envelope, intent, reference, current_message)
        self._m3a_complete_no_bundle(
            envelope,
            result=f"m3a-admitted:{intent.operation_id}",
            outgoing=outgoing,
        )
        self.emit(
            "field.m3a_operation_admitted",
            {
                "operation_id": str(intent.operation_id),
                "contract_id": str(outgoing[-1].payload.contract_id),
                "reference_observation_id": intent.reference_observation_id,
                "current_observation_id": current.observation_id,
            },
        )

    def _process_m3a_robot_message(
        self,
        envelope: MessageEnvelope,
        *,
        payload: WireModel | None = None,
    ) -> None:
        forwarded_payload = payload if payload is not None else envelope.payload
        forwarded = self.factory.make(
            envelope.message_type,
            self.mission_id,
            envelope.correlation_id,
            forwarded_payload,
            causation_id=envelope.message_id,
            message_id=_m3a_uuid(
                getattr(forwarded_payload, "operation_id", envelope.correlation_id),
                f"forward:{envelope.message_id}",
            ),
        )
        self._m3a_complete_no_bundle(
            envelope,
            result=f"m3a-forwarded:{forwarded.message_id}",
            outgoing=(forwarded,),
        )

    async def process_claimed(self, envelope: MessageEnvelope) -> None:
        if isinstance(envelope.payload, TwoButtonObservation):
            self._process_m3a_observation(envelope)
            return
        if isinstance(envelope.payload, M3aEnsureLatchedIntent):
            self._process_m3a_intent(envelope)
            return
        if isinstance(envelope.payload, TwoButtonEffectEvidence):
            self._process_m3a_effect_evidence(envelope)
            return
        if isinstance(
            envelope.payload,
            (
                LocalTwoButtonDecision,
                TwoButtonLevelEvidence,
                TwoButtonEffectEvidence,
                SpatialPressCommand,
            ),
        ):
            self._process_m3a_robot_message(envelope)
            return
        if (
            isinstance(envelope.payload, ExecutionEvent)
            and envelope.message_type == "execution.event"
        ):
            # The standard Field path handles legacy robot events.  M3a Robot
            # events are forwarded by the same route when their causation is a
            # M3a decision/level envelope.
            if any(
                message.message_id == envelope.causation_id
                and isinstance(
                    message.payload,
                    (LocalTwoButtonDecision, TwoButtonLevelEvidence),
                )
                for message in self.store.inbox_messages()
            ) or any(
                isinstance(message.payload, M3aSpatialExecutionContext)
                and message.payload.contract_id == envelope.payload.contract_id
                and message.payload.contract_revision == envelope.payload.contract_revision
                for message in self.store.inbox_messages()
            ) or any(
                isinstance(message.payload, M3aSpatialExecutionContext)
                and message.payload.contract_id == envelope.payload.contract_id
                and message.payload.contract_revision == envelope.payload.contract_revision
                for message in self.store.outbox_messages()
            ):
                self._process_m3a_robot_message(envelope)
                return
        await super().process_claimed(envelope)


class M3aFieldService(_M3aFieldMixin, FieldService):
    """Field service with immutable M3a authoring/context binding enabled."""

    def __init__(
        self,
        *args: Any,
        m3a_device_id: str = "two-button-device-1",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if not isinstance(m3a_device_id, str) or not m3a_device_id.strip():
            raise ValueError("m3a_device_id must be a non-empty string")
        self.m3a_device_id = m3a_device_id


class _M3aRobotMixin:
    """Robot-side policy, binding, budget, and exact decision replay."""

    _m3a_only = False

    def _m3a_context_for(
        self,
        contract: ExecutionContract,
        *,
        correlation_id: UUID | None = None,
    ) -> M3aSpatialExecutionContext | None:
        """Find the context for a contract, retaining mismatches for refusal.

        An exact contract/revision match wins.  If that is absent, a context
        with the same operation or transport correlation is still returned so
        a present-but-mutated context is classified as ``HOLD_CONTEXT_MISMATCH``
        rather than being mistaken for a retryable missing dependency.
        """

        candidates = self._m3a_context_candidates_for(contract, correlation_id=correlation_id)
        return candidates[0].payload if candidates else None

    def _m3a_context_candidates_for(
        self,
        contract: ExecutionContract,
        *,
        correlation_id: UUID | None = None,
    ) -> tuple[MessageEnvelope, ...]:
        messages = tuple(
            message
            for message in self.store.inbox_messages()
            if isinstance(message.payload, M3aSpatialExecutionContext)
        )
        exact = tuple(
            message
            for message in messages
            if message.payload.contract_id == contract.contract_id
            and message.payload.contract_revision == contract.contract_revision
        )
        return exact or tuple(
            message
            for message in messages
            if message.payload.operation_id == contract.operation_id
            or (correlation_id is not None and message.correlation_id == correlation_id)
        )

    def _m3a_context_has_conflict(
        self,
        contract: ExecutionContract,
        *,
        correlation_id: UUID | None = None,
    ) -> bool:
        """Detect a substituted context before any policy or budget action."""

        candidates = self._m3a_context_candidates_for(
            contract,
            correlation_id=correlation_id,
        )
        digests = {
            m3a_canonical_digest(message.payload.model_dump(mode="json"))
            for message in candidates
        }
        if len(digests) > 1:
            return True
        if self.store.inspect_m3a_context_conflicts(
            contract.contract_id,
            contract.contract_revision,
        ):
            return True
        binding = self.store.find_m3a_context_binding(
            contract.contract_id,
            contract.contract_revision,
        )
        return binding is not None and bool(digests) and next(iter(digests)) != binding[
            "context_digest"
        ]

    @staticmethod
    def _m3a_intent_from_context(context: LocalM3aContext) -> LocalM3aIntent:
        return LocalM3aIntent(
            operation_id=context.operation_id,
            intent_revision=context.intent_revision,
            semantic_effect_id=context.semantic_effect_id,
            target_entity_id=context.target_entity_id,
            desired_latched=True,
            reference_observation_id=context.reference_observation_id,
            reference_detection_id=context.reference_detection_id,
            reference_digest=context.reference_digest,
            reference_pose=context.reference_pose,
            reference_frame_id=context.reference_frame_id,
            reference_calibration_version=context.reference_calibration_version,
            reference_world_revision=context.reference_world_revision,
            reference_observed_at=context.reference_observed_at,
            same_identity_only=context.same_identity_only,
            max_displacement_m=context.max_displacement_m,
            expires_at=context.expires_at,
        )

    @staticmethod
    def _m3a_expected_contact(target_entity_id: str) -> str | None:
        """Map the verified semantic target to the fixture's contact proof."""

        return target_entity_id if target_entity_id in {"A", "B"} else None

    def _m3a_level_from_adapter(
        self,
        target_entity_id: str,
        *,
        expected_device_id: str,
    ) -> tuple[LocalM3aLevelEvidence, str | None]:
        adapter = self.external_effect_adapter
        if adapter is None:
            return (
                LocalM3aLevelEvidence(
                    target_entity_id=target_entity_id,
                    desired_latched=True,
                    actual_latched=False,
                    device_id=expected_device_id,
                    counter=0,
                    observed_at=self.clock.now(),
                    evidence_observation_id="level:unavailable",
                ),
                "LEVEL_ADAPTER_UNAVAILABLE",
            )
        reader = getattr(adapter, "level_evidence", None)
        if not callable(reader):
            reader = getattr(adapter, "level", None)
        if not callable(reader):
            return (
                LocalM3aLevelEvidence(
                    target_entity_id=target_entity_id,
                    desired_latched=True,
                    actual_latched=False,
                    device_id=expected_device_id,
                    counter=0,
                    observed_at=self.clock.now(),
                    evidence_observation_id="level:unavailable",
                ),
                "LEVEL_READER_UNAVAILABLE",
            )
        try:
            result = reader(target_entity_id, observed_at=self.clock.now())
            if isinstance(result, TwoButtonLevelEvidence):
                result = _m3a_local_level(result)
            if not isinstance(result, LocalM3aLevelEvidence):
                raise TypeError("level reader must return TwoButtonLevelEvidence")
            return result, None
        except (TypeError, ValueError, RuntimeError) as error:
            return (
                LocalM3aLevelEvidence(
                    target_entity_id=target_entity_id,
                    desired_latched=True,
                    actual_latched=False,
                    device_id=expected_device_id,
                    counter=0,
                    observed_at=self.clock.now(),
                    evidence_observation_id="level:error",
                ),
                f"LEVEL_READER_ERROR:{type(error).__name__}",
            )

    def _m3a_envelopes(
        self,
        envelope: MessageEnvelope,
        *,
        level: LocalM3aLevelEvidence,
        decision: LocalM3aDecision,
        held_event: MessageEnvelope | None,
    ) -> tuple[MessageEnvelope, MessageEnvelope, MessageEnvelope | None]:
        level_envelope = self.factory.make(
            "m3a.spatial.level",
            self.field_id,
            envelope.correlation_id,
            _m3a_wire_level(level),
            causation_id=envelope.message_id,
            message_id=_m3a_uuid(
                decision.operation_id,
                f"level-envelope:{decision.intent_revision}",
            ),
        )
        decision_envelope = self.factory.make(
            "m3a.spatial.decision",
            self.field_id,
            envelope.correlation_id,
            _m3a_wire_decision(decision),
            causation_id=level_envelope.message_id,
            message_id=_m3a_uuid(
                decision.operation_id,
                f"decision-envelope:{decision.intent_revision}",
            ),
        )
        return level_envelope, decision_envelope, held_event

    def _m3a_unverified_effect_envelope(
        self,
        envelope: MessageEnvelope,
        *,
        context: LocalM3aContext,
        command_digest: str,
        observation: ExternalEffectObservation,
        terminal_event: MessageEnvelope,
        diagnostic: str,
    ) -> MessageEnvelope:
        """Build an explicit UNKNOWN diagnostic when the adapter digest is unusable.

        The durable command digest is copied as the expected value so the
        evidence remains joinable to the dispatch.  ``command_digest_verified``
        and the diagnostic make clear that this value was not reported by the
        adapter; all physical contact and level fields stay unknown.
        """

        proof = TwoButtonEffectEvidence(
            operation_id=context.operation_id,
            intent_revision=context.intent_revision,
            contract_id=context.contract_id,
            contract_revision=context.contract_revision,
            semantic_effect_id=context.semantic_effect_id,
            target_entity_id=context.target_entity_id,
            effect_key=observation.effect_key,
            command_digest=command_digest,
            device_id=observation.device_id,
            observation_id=observation.observation_id,
            observed_at=observation.observed_at,
            outcome=ExternalOutcome.UNKNOWN.value,
            command_digest_verified=False,
            command_digest_diagnostic=diagnostic,
            physical_contact=None,
            command_executed=None,
            semantic_goal_attained=None,
            a_counter=None,
            b_counter=None,
            a_latched=None,
            b_latched=None,
        )
        return self.factory.make(
            "m3a.spatial.effect",
            self.field_id,
            envelope.correlation_id,
            proof,
            causation_id=terminal_event.message_id,
            message_id=_m3a_uuid(
                context.operation_id,
                f"effect-evidence-envelope:{context.contract_revision}",
            ),
            created_at=observation.observed_at,
        )

    def _m3a_effect_envelope(
        self,
        envelope: MessageEnvelope,
        *,
        context: LocalM3aContext,
        command_digest: str,
        observation: ExternalEffectObservation,
        terminal_event: MessageEnvelope,
    ) -> MessageEnvelope:
        details = dict(observation.details)
        reported_digest = details.get("command_digest")
        if reported_digest != command_digest:
            raise RecordConflictError("M3a effect proof command digest differs from command")

        contact = details.get("contact")
        if contact not in {"A", "B", "NONE"}:
            contact = None

        def nonnegative_int(name: str) -> int | None:
            value = details.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value

        def optional_bool(name: str) -> bool | None:
            value = details.get(name)
            return value if isinstance(value, bool) else None

        command_executed = optional_bool("command_executed")
        if command_executed is None:
            command_executed = observation.outcome is ExternalOutcome.APPLIED
        semantic_goal_attained = optional_bool("semantic_goal_attained")
        if semantic_goal_attained is None:
            semantic_goal_attained = (
                observation.outcome is ExternalOutcome.APPLIED
                and contact == context.target_entity_id
            ) if contact is not None else None
        proof = TwoButtonEffectEvidence(
            operation_id=context.operation_id,
            intent_revision=context.intent_revision,
            contract_id=context.contract_id,
            contract_revision=context.contract_revision,
            semantic_effect_id=context.semantic_effect_id,
            target_entity_id=context.target_entity_id,
            effect_key=observation.effect_key,
            command_digest=command_digest,
            device_id=observation.device_id,
            observation_id=observation.observation_id,
            observed_at=observation.observed_at,
            outcome=observation.outcome.value,
            command_digest_verified=True,
            command_digest_diagnostic=None,
            physical_contact=contact,
            command_executed=command_executed,
            semantic_goal_attained=semantic_goal_attained,
            a_counter=nonnegative_int("a_counter"),
            b_counter=nonnegative_int("b_counter"),
            a_latched=optional_bool("a_latched"),
            b_latched=optional_bool("b_latched"),
        )
        return self.factory.make(
            "m3a.spatial.effect",
            self.field_id,
            envelope.correlation_id,
            proof,
            causation_id=terminal_event.message_id,
            message_id=_m3a_uuid(
                context.operation_id,
                f"effect-evidence-envelope:{context.contract_revision}",
            ),
            created_at=observation.observed_at,
        )

    def _m3a_held_event(
        self,
        envelope: MessageEnvelope,
        *,
        previous_state: ContractState = ContractState.RECEIVED,
    ) -> MessageEnvelope:
        return self._transition(
            envelope,
            previous_state,
            ContractState.HELD,
            ordinal=1 if previous_state is ContractState.RECEIVED else 4,
            occurred_at=self.clock.now(),
        )

    def _m3a_record_hold(
        self,
        envelope: MessageEnvelope,
        *,
        level: LocalM3aLevelEvidence,
        decision: LocalM3aDecision,
        business_result: str,
    ) -> None:
        journal = self.store.find_execution_journal(
            envelope.payload.contract_id,
            envelope.payload.contract_revision,
        )
        previous_state = (
            ContractState.ACCEPTED
            if journal is not None and journal["state"] == ContractState.ACCEPTED.value
            else ContractState.RECEIVED
        )
        held_event = self._m3a_held_event(
            envelope,
            previous_state=previous_state,
        )
        level_envelope, decision_envelope, _ = self._m3a_envelopes(
            envelope,
            level=level,
            decision=decision,
            held_event=held_event,
        )
        if previous_state is ContractState.ACCEPTED:
            self.store.complete_budget_scope_denial(
                envelope.payload.contract_id,
                envelope.payload.contract_revision,
                operation_id=envelope.payload.operation_id,
                reason=business_result,
                first_envelope=envelope,
                held_event=held_event,
                inbox_message_id=envelope.message_id,
                processed_at=self.clock.now(),
                m3a_decision_envelope=decision_envelope,
                m3a_level_envelope=level_envelope,
                m3a_business_result=business_result,
            )
            return
        self.store.record_m3a_decision(
            contract_id=envelope.payload.contract_id,
            contract_revision=envelope.payload.contract_revision,
            operation_id=envelope.payload.operation_id,
            decision_envelope=decision_envelope,
            level_envelope=level_envelope,
            held_event=held_event,
            business_result=business_result,
            recorded_at=self.clock.now(),
            outgoing=(level_envelope, decision_envelope, held_event),
        )
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=self.clock.now(),
            handler_result_reference=f"m3a-held:{business_result}",
        )

    def _m3a_record_context_payload_hold(
        self,
        envelope: MessageEnvelope,
        context_payload: M3aSpatialExecutionContext,
        *,
        reason: str,
    ) -> None:
        """Persist a refusal when a delivered context cannot be decoded locally."""

        expected_device_id = getattr(context_payload, "expected_device_id", None)
        if not isinstance(expected_device_id, str) or not expected_device_id.strip():
            expected_device_id = "unknown-device"
        target_entity_id = getattr(context_payload, "target_entity_id", None)
        if not isinstance(target_entity_id, str) or not target_entity_id.strip():
            target_entity_id = "unknown-target"
        level, _ = self._m3a_level_from_adapter(
            target_entity_id,
            expected_device_id=expected_device_id,
        )
        semantic_effect_id = getattr(context_payload, "semantic_effect_id", None)
        if not isinstance(semantic_effect_id, str) or not semantic_effect_id.strip():
            semantic_effect_id = "context-invalid"
        reference_observation_id = getattr(context_payload, "reference_observation_id", None)
        if not isinstance(reference_observation_id, str) or not reference_observation_id.strip():
            reference_observation_id = "context-invalid-reference"
        current_observation = getattr(context_payload, "current_observation", None)
        current_observation_id = getattr(current_observation, "observation_id", None)
        if not isinstance(current_observation_id, str) or not current_observation_id.strip():
            current_observation_id = "context-invalid-current"
        decision = LocalM3aDecision(
            operation_id=envelope.payload.operation_id,
            intent_revision=1,
            semantic_effect_id=semantic_effect_id,
            reference_observation_id=reference_observation_id,
            current_observation_id=current_observation_id,
            action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
            reason=reason,
            budget_state="NOT_ADMITTED",
        )
        self._m3a_record_hold(
            envelope,
            level=level,
            decision=decision,
            business_result="HELD_CONTEXT_PAYLOAD_MISMATCH",
        )

    def _m3a_command_from_outbox(
        self, operation_id: UUID, command_digest: str | None
    ) -> SpatialPressCommand | None:
        candidates = tuple(
            message.payload
            for message in self.store.outbox_messages()
            if isinstance(message.payload, SpatialPressCommand)
            and (
                command_digest is None
                or message.payload.command_digest == command_digest
            )
            and any(
                isinstance(candidate.payload, LocalTwoButtonDecision)
                and candidate.payload.operation_id == operation_id
                for candidate in self.store.outbox_messages()
            )
        )
        return max(candidates, key=lambda value: value.command_id, default=None)

    def _m3a_assert_binding(
        self,
        *,
        contract: ExecutionContract,
        effect_key: str,
        expected_device_id: str,
        command: SpatialPressCommand,
    ) -> None:
        budget = self.store.find_autonomy_budget(contract.contract_id, contract.contract_revision)
        if budget is None:
            raise RecordConflictError("M3a execution has no durable command-bound budget")
        if budget.get("command_digest") != command.command_digest:
            raise RecordConflictError("M3a budget command digest differs from command")
        binding_reader = getattr(self.external_effect_adapter, "binding", None)
        if not callable(binding_reader):
            raise RecordConflictError("M3a adapter cannot prove its immutable command binding")
        receipt = binding_reader(effect_key)
        if (
            getattr(receipt, "device_id", None) != expected_device_id
            or getattr(receipt, "effect_key", None) != effect_key
            or getattr(receipt, "command_digest", None) != command.command_digest
        ):
            raise RecordConflictError("M3a adapter binding receipt differs from durable command")

    async def _m3a_replay_existing(
        self,
        envelope: MessageEnvelope,
        context: LocalM3aContext,
        row: Mapping[str, Any],
    ) -> bool:
        decision_envelope = row["decision_envelope"]
        level_envelope = row["level_envelope"]
        held_event = row["held_event"]
        if not isinstance(decision_envelope, MessageEnvelope) or not isinstance(
            level_envelope, MessageEnvelope
        ):
            raise RecordConflictError("persisted M3a decision envelopes are malformed")
        decision = decision_envelope.payload
        if not isinstance(decision, LocalTwoButtonDecision):
            raise RecordConflictError("persisted M3a decision payload is malformed")
        outgoing: list[MessageEnvelope] = [level_envelope]
        stored_effect_evidence = next(
            (
                message
                for message in self.store.outbox_messages()
                if isinstance(message.payload, TwoButtonEffectEvidence)
                and message.payload.operation_id == context.operation_id
                and message.payload.contract_id == envelope.payload.contract_id
                and message.payload.contract_revision == envelope.payload.contract_revision
            ),
            None,
        )
        if stored_effect_evidence is not None:
            outgoing.append(stored_effect_evidence)
        stored_command_envelope = row.get("command_envelope")
        if stored_command_envelope is not None and not isinstance(
            stored_command_envelope, MessageEnvelope
        ):
            raise RecordConflictError("persisted M3a command envelope is malformed")
        stored_command = (
            stored_command_envelope.payload
            if isinstance(stored_command_envelope, MessageEnvelope)
            else None
        )
        if stored_command is not None and not isinstance(stored_command, SpatialPressCommand):
            raise RecordConflictError("persisted M3a command payload is malformed")
        command = (
            stored_command
            if isinstance(stored_command, SpatialPressCommand)
            else self._m3a_command_from_outbox(context.operation_id, decision.command_digest)
        )
        effect_key = f"press:{context.operation_id}:{context.intent_revision}"
        if decision.action in {M3aAction.EXECUTE, M3aAction.REANCHOR_EXECUTE}:
            if command is None:
                raise RecordConflictError("persisted M3a decision has no command envelope")
            expected_contact = self._m3a_expected_contact(context.target_entity_id)
            if expected_contact is None:
                raise RecordConflictError(
                    "M3a replay target has no supported physical contact proof"
                )
            expected_device_id = context.expected_device_id or self._external_device_id()
            if expected_device_id is None:
                raise RecordConflictError("M3a replay has no expected device identity")
            self._m3a_assert_binding(
                contract=envelope.payload,
                effect_key=effect_key,
                expected_device_id=expected_device_id,
                command=command,
            )
            command_envelope = stored_command_envelope
            if command_envelope is None:
                command_envelope = next(
                    message
                    for message in self.store.outbox_messages()
                    if isinstance(message.payload, SpatialPressCommand)
                    and message.payload.command_digest == command.command_digest
                )
            outgoing.append(command_envelope)
            journal = self.store.find_execution_journal(
                envelope.payload.contract_id, envelope.payload.contract_revision
            )
            outgoing.append(decision_envelope)
            if held_event is not None:
                outgoing.append(held_event)
            if journal is not None and journal["state"] == ContractState.DISPATCH_RECORDED.value:
                for replay in outgoing:
                    self._enqueue_if_absent(replay)
                await self._process_external_contract(
                    envelope,
                    effect_key=effect_key,
                    dispatch_recorded_now=False,
                    expected_device_id=expected_device_id,
                    expected_contact=expected_contact,
                    m3a_context=context,
                    command_digest=command.command_digest,
                )
                return True
        else:
            outgoing.append(decision_envelope)
            if held_event is not None:
                outgoing.append(held_event)
        self.store.complete_inbox(
            envelope.message_id,
            processed_at=self.clock.now(),
            handler_result_reference=f"m3a-decision-replay:{decision.action.value}",
            outgoing=tuple(outgoing),
        )
        return True

    async def _process_m3a_contract(self, envelope: MessageEnvelope) -> None:
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
        context_payload = self._m3a_context_for(
            contract,
            correlation_id=envelope.correlation_id,
        )
        if assignment is None or context_payload is None:
            self.store.fail_inbox(
                envelope.message_id,
                {"reason": "m3a-assignment-or-context-not-yet-persisted", "retryable": True},
            )
            return
        existing = self.store.find_m3a_decision(
            contract.contract_id, contract.contract_revision
        )
        context_conflict = self._m3a_context_has_conflict(
            contract,
            correlation_id=envelope.correlation_id,
        )
        try:
            local_context = _m3a_local_context(context_payload)
        except (TypeError, ValueError) as error:
            self._m3a_record_context_payload_hold(
                envelope,
                context_payload,
                reason=f"CONTEXT_PAYLOAD_INVALID:{type(error).__name__}",
            )
            return
        if context_conflict and existing is None:
            self._m3a_record_context_payload_hold(
                envelope,
                context_payload,
                reason="CONTEXT_CONTENT_CONFLICT",
            )
            return
        if (
            assignment.executor_id != self.factory.node_id
            or context_payload.operation_id != contract.operation_id
            or context_payload.task_id != assignment.task_id
            or context_payload.contract_id != contract.contract_id
        ):
            # The context is present but inconsistent; retain an auditable
            # hold with no budget or adapter activity.
            intent = self._m3a_intent_from_context(local_context)
            level, _ = self._m3a_level_from_adapter(
                intent.target_entity_id,
                expected_device_id=context_payload.expected_device_id or "unknown-device",
            )
            decision = LocalM3aDecision(
                operation_id=contract.operation_id,
                intent_revision=1,
                semantic_effect_id=intent.semantic_effect_id,
                reference_observation_id=intent.reference_observation_id,
                current_observation_id=local_context.current_observation.observation_id,
                action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
                reason="CONTRACT_CONTEXT_ID_MISMATCH",
                budget_state="NOT_ADMITTED",
            )
            self._m3a_record_hold(
                envelope,
                level=level,
                decision=decision,
                business_result="HELD_CONTRACT_CONTEXT_MISMATCH",
            )
            return

        if local_context.reference_observation is None:
            level, _ = self._m3a_level_from_adapter(
                context_payload.target_entity_id,
                expected_device_id=context_payload.expected_device_id or "unknown-device",
            )
            decision = LocalM3aDecision(
                operation_id=contract.operation_id,
                intent_revision=1,
                semantic_effect_id=context_payload.semantic_effect_id,
                reference_observation_id=context_payload.reference_observation_id,
                current_observation_id=context_payload.current_observation.observation_id,
                action=TwoButtonAction.HOLD_REFERENCE_MISMATCH,
                reason="REFERENCE_OBSERVATION_NOT_EMBEDDED",
                budget_state="NOT_ADMITTED",
            )
            self._m3a_record_hold(
                envelope,
                level=level,
                decision=decision,
                business_result="HELD_REFERENCE_MISMATCH",
            )
            return
        if existing is not None:
            await self._m3a_replay_existing(envelope, local_context, existing)
            return

        intent = self._m3a_intent_from_context(local_context)
        expected_contact = self._m3a_expected_contact(intent.target_entity_id)
        if expected_contact is None:
            level, _ = self._m3a_level_from_adapter(
                intent.target_entity_id,
                expected_device_id=context_payload.expected_device_id or "unknown-device",
            )
            decision = LocalTwoButtonDecision(
                operation_id=intent.operation_id,
                intent_revision=1,
                semantic_effect_id=intent.semantic_effect_id,
                reference_observation_id=intent.reference_observation_id,
                current_observation_id=local_context.current_observation.observation_id,
                action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
                reason="UNSUPPORTED_TARGET_CONTACT",
                budget_state="NOT_ADMITTED",
            )
            self._m3a_record_hold(
                envelope,
                level=level,
                decision=decision,
                business_result="HELD_UNSUPPORTED_TARGET_CONTACT",
            )
            return
        expected_device_id = context_payload.expected_device_id or self._external_device_id()
        if expected_device_id is None:
            level, _ = self._m3a_level_from_adapter(
                intent.target_entity_id, expected_device_id="unknown-device"
            )
            decision = LocalM3aDecision(
                operation_id=intent.operation_id,
                intent_revision=1,
                semantic_effect_id=intent.semantic_effect_id,
                reference_observation_id=intent.reference_observation_id,
                current_observation_id=local_context.current_observation.observation_id,
                action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
                reason="ADAPTER_UNAVAILABLE",
                budget_state="NOT_ADMITTED",
            )
            self._m3a_record_hold(
                envelope,
                level=level,
                decision=decision,
                business_result="HELD_ADAPTER_UNAVAILABLE",
            )
            return
        level, level_error = self._m3a_level_from_adapter(
            intent.target_entity_id,
            expected_device_id=expected_device_id,
        )
        decision = decide_two_button(
            intent,
            local_context.reference_observation,
            local_context.current_observation,
            level,
            expected_source_id=local_context.reference_observation.source_id,
            expected_device_id=expected_device_id,
            budget_state="NOT_ADMITTED",
        )
        if level_error is not None:
            decision = replace(
                decision,
                action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
                reason=level_error,
            )
        if decision.action not in {M3aAction.EXECUTE, M3aAction.REANCHOR_EXECUTE}:
            business_result = (
                "RECOGNIZED_ALREADY_EFFECTIVE"
                if decision.action is M3aAction.RECOGNIZE_EFFECT
                else f"HELD_{decision.reason}"
            )
            self._m3a_record_hold(
                envelope,
                level=level,
                decision=decision,
                business_result=business_result,
            )
            return

        effect_key = f"press:{contract.operation_id}:{contract.contract_revision}"
        command_local = derive_spatial_press_command(
            decision,
            effect_key=effect_key,
            command_id=f"command:{contract.operation_id}:{contract.contract_revision}",
            reference_observation=local_context.reference_observation,
            current_observation=local_context.current_observation,
        )
        command = _m3a_wire_command(command_local)
        decision = replace(decision, command_digest=command.command_digest)
        binder = getattr(self.external_effect_adapter, "bind", None)
        if not callable(binder):
            decision = replace(
                decision,
                action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
                reason="ADAPTER_BIND_UNAVAILABLE",
            )
            self._m3a_record_hold(
                envelope,
                level=level,
                decision=decision,
                business_result="HELD_ADAPTER_BIND_UNAVAILABLE",
            )
            return
        try:
            # The adapter is a local capability boundary; it receives the
            # immutable local command object, while the identical wire command
            # is persisted/transmitted as evidence below.
            receipt = binder(effect_key, command_local)
            if (
                getattr(receipt, "device_id", None) != expected_device_id
                or getattr(receipt, "effect_key", None) != effect_key
                or getattr(receipt, "command_digest", None) != command.command_digest
            ):
                raise RecordConflictError("M3a adapter binding receipt mismatch")
        except (TypeError, ValueError, RuntimeError, RecordConflictError) as error:
            decision = replace(
                decision,
                action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
                reason=f"ADAPTER_BIND_REJECTED:{type(error).__name__}",
            )
            self._m3a_record_hold(
                envelope,
                level=level,
                decision=decision,
                business_result="HELD_ADAPTER_BIND_REJECTED",
            )
            return

        try:
            self.store.admit_external_budget_contract(
                contract_id=contract.contract_id,
                contract_revision=contract.contract_revision,
                operation_id=contract.operation_id,
                task_id=assignment.task_id,
                effect_key=effect_key,
                accepted_at=self.clock.now(),
                max_elapsed_seconds=self.max_elapsed_seconds,
                command_digest=command.command_digest,
            )
        except (
            BudgetScopeConflictError,
            BudgetPolicyConflictError,
            BudgetDeadlineError,
            BudgetLimitError,
            RecordConflictError,
        ) as error:
            decision = replace(
                decision,
                action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
                reason=f"BUDGET_ADMISSION_REJECTED:{type(error).__name__}",
            )
            self._m3a_record_hold(
                envelope,
                level=level,
                decision=decision,
                business_result="HELD_BUDGET_ADMISSION_REJECTED",
            )
            return
        journal = self._journal(contract)
        if journal["state"] == ContractState.ACCEPTED.value:
            try:
                dispatch_now = self.store.reserve_external_dispatch_with_budget(
                    contract.contract_id,
                    contract.contract_revision,
                    recorded_at=self.clock.now(),
                    device_id=expected_device_id,
                    max_elapsed_seconds=self.max_elapsed_seconds,
                    command_digest=command.command_digest,
                )
            except (
                BudgetPolicyConflictError,
                BudgetDeadlineError,
                BudgetLimitError,
                RecordConflictError,
            ) as error:
                decision = replace(
                    decision,
                    action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
                    reason=f"BUDGET_RESERVATION_REJECTED:{type(error).__name__}",
                )
                self._m3a_record_hold(
                    envelope,
                    level=level,
                    decision=decision,
                    business_result="HELD_BUDGET_RESERVATION_REJECTED",
                )
                return
        else:
            dispatch_now = False
        decision = replace(decision, budget_state="DISPATCH_RECORDED")
        level_envelope, decision_envelope, _ = self._m3a_envelopes(
            envelope,
            level=level,
            decision=decision,
            held_event=None,
        )
        command_envelope = self.factory.make(
            "m3a.spatial.command",
            self.field_id,
            envelope.correlation_id,
            command,
            causation_id=decision_envelope.message_id,
            message_id=_m3a_uuid(contract.operation_id, "command-envelope"),
        )
        self.store.record_m3a_decision(
            contract_id=contract.contract_id,
            contract_revision=contract.contract_revision,
            operation_id=contract.operation_id,
            decision_envelope=decision_envelope,
            level_envelope=level_envelope,
            held_event=None,
            business_result="EXECUTION_DECISION",
            recorded_at=self.clock.now(),
            command_envelope=command_envelope,
            outgoing=(level_envelope, decision_envelope, command_envelope),
        )
        journal = self._journal(contract)
        self._assert_external_dispatch_configuration(journal, expected_device_id)
        self._m3a_assert_binding(
            contract=contract,
            effect_key=effect_key,
            expected_device_id=expected_device_id,
            command=command,
        )
        await self._process_external_contract(
            envelope,
            effect_key=effect_key,
            dispatch_recorded_now=dispatch_now,
            expected_device_id=expected_device_id,
            expected_contact=expected_contact,
            m3a_context=local_context,
            command_digest=command.command_digest,
        )

    async def process_claimed(self, envelope: MessageEnvelope) -> None:
        if isinstance(envelope.payload, M3aSpatialExecutionContext):
            try:
                self.store.bind_m3a_context(
                    envelope,
                    bound_at=self.clock.now(),
                )
            except RecordConflictError as error:
                self.store.complete_inbox(
                    envelope.message_id,
                    processed_at=self.clock.now(),
                    handler_result_reference=f"m3a-context-conflict:{error}",
                )
                return
            self.store.complete_inbox(
                envelope.message_id,
                processed_at=self.clock.now(),
                handler_result_reference=(
                    f"m3a-context:{envelope.payload.contract_id}:{envelope.payload.contract_revision}"
                ),
            )
            return
        if isinstance(envelope.payload, ExecutionContract):
            context = self._m3a_context_for(
                envelope.payload,
                correlation_id=envelope.correlation_id,
            )
            if self._m3a_only or context is not None:
                await self._process_m3a_contract(envelope)
                return
        await super().process_claimed(envelope)


class M3aRobotService(_M3aRobotMixin, DummyRobotService):
    """Robot service for the M3a spatial adapter and command-bound budget."""

    _m3a_only = True


# Compatibility aliases for integration harnesses that describe the physical
# role before the M3a slice name.
TwoButtonMissionService = M3aMissionService
TwoButtonFieldService = M3aFieldService
TwoButtonRobotService = M3aRobotService
SpatialMissionService = M3aMissionService
SpatialFieldService = M3aFieldService
SpatialRobotService = M3aRobotService
