"""Strict experimental ``dtt/0`` wire models for M1 and the M2 articulated path."""

from __future__ import annotations

import math
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WireModel(BaseModel):
    """Base for versioned wire DTOs; unknown fields are protocol errors."""

    model_config = ConfigDict(extra="forbid", strict=True)


OpaqueId = Annotated[str, Field(min_length=1)]
Revision = Annotated[int, Field(ge=1)]


class ProvenanceKind(StrEnum):
    MEASURED = "MEASURED"
    FUSED = "FUSED"
    OPERATOR_ASSERTED = "OPERATOR_ASSERTED"
    INFERRED = "INFERRED"
    PREDICTED = "PREDICTED"
    SIMULATED = "SIMULATED"


class EvidenceMetadata(WireModel):
    source_ids: Annotated[tuple[OpaqueId, ...], Field(min_length=1)]
    observed_at: datetime
    produced_at: datetime
    provenance: ProvenanceKind
    world_revision: Revision
    fresh_until: datetime | None = None
    model_version: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_times(self) -> EvidenceMetadata:
        if self.produced_at < self.observed_at:
            raise ValueError("produced_at cannot precede observed_at")
        if self.fresh_until is not None and self.fresh_until < self.observed_at:
            raise ValueError("fresh_until cannot precede observed_at")
        if self.provenance in {ProvenanceKind.PREDICTED, ProvenanceKind.SIMULATED}:
            if self.model_version is None:
                raise ValueError("predicted or simulated evidence requires model_version")
        return self


class SpatialFrame(WireModel):
    frame_id: OpaqueId
    convention: Literal["RIGHT_HANDED_Z_UP"] = "RIGHT_HANDED_Z_UP"
    length_unit: Literal["metre"] = "metre"
    angle_unit: Literal["radian"] = "radian"
    calibration_version: OpaqueId


class Vector3(WireModel):
    x: float
    y: float
    z: float

    @model_validator(mode="after")
    def validate_finite(self) -> Vector3:
        if not all(math.isfinite(component) for component in (self.x, self.y, self.z)):
            raise ValueError("vector components must be finite")
        return self


class Quaternion(WireModel):
    x: float
    y: float
    z: float
    w: float

    @model_validator(mode="after")
    def validate_unit_length(self) -> Quaternion:
        components = (self.x, self.y, self.z, self.w)
        if not all(math.isfinite(component) for component in components):
            raise ValueError("quaternion components must be finite")
        norm_squared = sum(component * component for component in components)
        if not math.isfinite(norm_squared) or abs(norm_squared - 1.0) > 1e-6:
            raise ValueError("quaternion must have unit length")
        return self


class Pose(WireModel):
    position: Vector3
    orientation: Quaternion
    frame: SpatialFrame


class OperationType(StrEnum):
    PRESS_BUTTON = "PRESS_BUTTON"


class ApprovalPolicy(StrEnum):
    AUTO_IF_WHITELISTED = "AUTO_IF_WHITELISTED"


class OperationState(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    RECEIVED_BY_FIELD = "RECEIVED_BY_FIELD"
    ADMITTED = "ADMITTED"
    HELD = "HELD"
    REJECTED = "REJECTED"


class ContractState(StrEnum):
    RECEIVED = "RECEIVED"
    ACCEPTED = "ACCEPTED"
    DISPATCH_RECORDED = "DISPATCH_RECORDED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    HELD = "HELD"
    CANCELLED = "CANCELLED"


class EntitySelector(WireModel):
    entity_id: OpaqueId


class OperationIntent(WireModel):
    operation_id: UUID
    operation_type: Literal[OperationType.PRESS_BUTTON]
    selector: EntitySelector
    preferred_executor: OpaqueId
    approval_policy: Literal[ApprovalPolicy.AUTO_IF_WHITELISTED]
    state: Literal[OperationState.SUBMITTED]


class GroundedOperation(WireModel):
    operation_id: UUID
    target_entity_id: OpaqueId
    target_pose: Pose
    state: Literal[OperationState.ADMITTED]
    evidence: EvidenceMetadata


class TaskNode(WireModel):
    task_id: UUID
    skill: Literal[OperationType.PRESS_BUTTON]
    target_entity_id: OpaqueId


class OperationPlan(WireModel):
    plan_id: UUID
    operation_id: UUID
    tasks: Annotated[tuple[TaskNode, ...], Field(min_length=1, max_length=1)]


class TaskAssignment(WireModel):
    assignment_id: UUID
    plan_id: UUID
    task_id: UUID
    executor_id: OpaqueId


class ExecutionContract(WireModel):
    contract_id: UUID
    contract_revision: Revision
    operation_id: UUID
    assignment_id: UUID
    state: Literal[ContractState.RECEIVED]


CONTRACT_TRANSITIONS: dict[ContractState, frozenset[ContractState]] = {
    ContractState.RECEIVED: frozenset({ContractState.ACCEPTED, ContractState.HELD}),
    ContractState.ACCEPTED: frozenset(
        {ContractState.DISPATCH_RECORDED, ContractState.CANCELLED, ContractState.HELD}
    ),
    ContractState.DISPATCH_RECORDED: frozenset({ContractState.RUNNING}),
    ContractState.RUNNING: frozenset(
        {ContractState.SUCCEEDED, ContractState.FAILED, ContractState.HELD, ContractState.CANCELLED}
    ),
}


class ExecutionEvent(WireModel):
    event_id: UUID
    contract_id: UUID
    contract_revision: Revision
    previous_state: ContractState
    next_state: ContractState
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_transition(self) -> ExecutionEvent:
        if self.next_state not in CONTRACT_TRANSITIONS.get(self.previous_state, frozenset()):
            raise ValueError(
                f"illegal contract transition: {self.previous_state} -> {self.next_state}"
            )
        return self


class RobotState(WireModel):
    robot_id: OpaqueId
    pose: Pose
    evidence: EvidenceMetadata


class RobotModelReference(WireModel):
    """Identity of the structural description used to decode named joints."""

    model_id: Annotated[str, Field(min_length=1)]
    model_revision: Annotated[str, Field(min_length=1)]
    description_hash: Annotated[
        str,
        Field(pattern=r"sha256:[0-9a-f]{64}", min_length=71, max_length=71),
    ]

    @model_validator(mode="after")
    def validate_reference(self) -> RobotModelReference:
        for field_name in ("model_id", "model_revision", "description_hash"):
            value = getattr(self, field_name)
            if not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.description_hash) is None:
            raise ValueError("description_hash must be sha256:<64 lowercase hexadecimal digits>")
        return self


class JointPosition(WireModel):
    """One named canonical joint position, expressed in radians."""

    joint_name: Annotated[str, Field(min_length=1)]
    position_radians: float

    @model_validator(mode="after")
    def validate_position(self) -> JointPosition:
        if not self.joint_name.strip():
            raise ValueError("joint_name must not be blank")
        if not math.isfinite(self.position_radians):
            raise ValueError("position_radians must be finite")
        return self


class ArticulatedRobotState(WireModel):
    """Named articulated robot evidence for the M2 path.

    JSON sequence order is transport-only.  Consumers validate names against the referenced
    description and then use that description's deterministic order.
    """

    robot_id: OpaqueId
    model_reference: RobotModelReference
    root_pose: Pose
    joints: Annotated[tuple[JointPosition, ...], Field(min_length=1)]
    evidence: EvidenceMetadata

    @model_validator(mode="after")
    def validate_joints(self) -> ArticulatedRobotState:
        if not self.robot_id.strip():
            raise ValueError("robot_id must not be blank")
        for field_name in ("frame_id", "calibration_version"):
            if not getattr(self.root_pose.frame, field_name).strip():
                raise ValueError(f"root_pose.frame.{field_name} must not be blank")
        if any(not source_id.strip() for source_id in self.evidence.source_ids):
            raise ValueError("evidence.source_ids must not contain blank values")
        names = [joint.joint_name for joint in self.joints]
        if len(set(names)) != len(names):
            duplicates = sorted({name for name in names if names.count(name) > 1})
            raise ValueError(f"duplicate joint names: {', '.join(duplicates)}")
        return self


class RobotForecast(WireModel):
    robot_id: OpaqueId
    predicted_pose: Pose
    predicted_for: datetime
    evidence: EvidenceMetadata

    @model_validator(mode="after")
    def validate_prediction(self) -> RobotForecast:
        if self.evidence.provenance is not ProvenanceKind.PREDICTED:
            raise ValueError("RobotForecast evidence must be PREDICTED")
        if self.predicted_for <= self.evidence.produced_at:
            raise ValueError("predicted_for must be after produced_at")
        return self


class SiteSnapshot(WireModel):
    site_id: OpaqueId
    entities: Annotated[tuple[OpaqueId, ...], Field(min_length=1)]
    robot_states: tuple[RobotState, ...]
    evidence: EvidenceMetadata


class PredictionManifest(WireModel):
    manifest_id: UUID
    site_id: OpaqueId
    forecast_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]
    generated_for_world_revision: Revision
    evidence: EvidenceMetadata

    @model_validator(mode="after")
    def validate_prediction(self) -> PredictionManifest:
        if self.evidence.provenance is not ProvenanceKind.PREDICTED:
            raise ValueError("PredictionManifest evidence must be PREDICTED")
        return self


class EntityDetection(WireModel):
    """One M3a observed candidate set and measured spatial pose."""

    detection_id: OpaqueId
    candidate_entity_ids: tuple[OpaqueId, ...]
    pose: Pose
    visibility: bool
    source_evidence_id: OpaqueId

    @model_validator(mode="after")
    def validate_candidates(self) -> EntityDetection:
        if not self.candidate_entity_ids:
            raise ValueError("candidate_entity_ids must not be empty")
        if len(set(self.candidate_entity_ids)) != len(self.candidate_entity_ids):
            raise ValueError("candidate_entity_ids must not contain duplicates")
        return self


class TwoButtonObservation(WireModel):
    """Persisted reference/current observation envelope for M3a."""

    observation_id: OpaqueId
    source_id: OpaqueId
    world_revision: Revision
    observed_at: datetime
    produced_at: datetime
    frame_id: OpaqueId
    calibration_version: OpaqueId
    canonical_payload_digest: Annotated[
        str,
        Field(pattern=r"sha256:[0-9a-f]{64}", min_length=71, max_length=71),
    ]
    detections: tuple[EntityDetection, ...]

    @model_validator(mode="after")
    def validate_observation(self) -> TwoButtonObservation:
        if self.produced_at < self.observed_at:
            raise ValueError("produced_at cannot precede observed_at")
        if any(
            detection.pose.frame.frame_id != self.frame_id
            or detection.pose.frame.calibration_version != self.calibration_version
            for detection in self.detections
        ):
            raise ValueError("detection pose context differs from observation context")
        if len({detection.detection_id for detection in self.detections}) != len(self.detections):
            raise ValueError("detections must not contain duplicate detection_id values")
        return self


class M3aEnsureLatchedIntent(WireModel):
    """Revision-1 M3a root intent; only the true level effect is in scope."""

    operation_id: UUID
    intent_revision: Literal[1]
    semantic_effect_id: OpaqueId
    target_entity_id: OpaqueId
    desired_latched: Literal[True]
    reference_observation_id: OpaqueId
    reference_detection_id: OpaqueId
    reference_digest: Annotated[
        str,
        Field(pattern=r"sha256:[0-9a-f]{64}", min_length=71, max_length=71),
    ]
    reference_pose: Pose
    reference_frame_id: OpaqueId
    reference_calibration_version: OpaqueId
    reference_world_revision: Revision
    reference_observed_at: datetime
    same_identity_only: Literal[True] = True
    max_displacement_m: float = Field(ge=0.0)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_reference_context(self) -> M3aEnsureLatchedIntent:
        if self.reference_pose.frame.frame_id != self.reference_frame_id:
            raise ValueError("reference_pose frame differs from reference_frame_id")
        if self.reference_pose.frame.calibration_version != self.reference_calibration_version:
            raise ValueError(
                "reference_pose calibration differs from reference_calibration_version"
            )
        if self.expires_at is not None and self.expires_at < self.reference_observed_at:
            raise ValueError("expires_at cannot precede reference_observed_at")
        if not math.isfinite(self.max_displacement_m):
            raise ValueError("max_displacement_m must be finite")
        return self


class M3aSpatialExecutionContext(WireModel):
    """Field's verified contract/context and full current observation."""

    operation_id: UUID
    intent_revision: Literal[1]
    contract_id: UUID
    contract_revision: Literal[1]
    task_id: UUID
    semantic_effect_id: OpaqueId
    target_entity_id: OpaqueId
    reference_observation_id: OpaqueId
    reference_detection_id: OpaqueId
    reference_digest: Annotated[
        str,
        Field(pattern=r"sha256:[0-9a-f]{64}", min_length=71, max_length=71),
    ]
    reference_pose: Pose
    reference_frame_id: OpaqueId
    reference_calibration_version: OpaqueId
    reference_world_revision: Revision
    reference_observed_at: datetime
    current_observation_envelope_id: OpaqueId
    current_observation: TwoButtonObservation
    reference_observation: TwoButtonObservation | None = None
    same_identity_only: Literal[True] = True
    max_displacement_m: float = Field(ge=0.0)
    expires_at: datetime | None = None
    expected_device_id: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_context(self) -> M3aSpatialExecutionContext:
        if self.reference_pose.frame.frame_id != self.reference_frame_id:
            raise ValueError("reference_pose frame differs from reference_frame_id")
        if self.reference_pose.frame.calibration_version != self.reference_calibration_version:
            raise ValueError(
                "reference_pose calibration differs from reference_calibration_version"
            )
        if self.current_observation.frame_id != self.reference_frame_id:
            raise ValueError("current observation frame differs from reference frame")
        if self.current_observation.calibration_version != self.reference_calibration_version:
            raise ValueError("current observation calibration differs from reference calibration")
        if self.reference_observation is not None:
            if self.reference_observation.observation_id != self.reference_observation_id:
                raise ValueError("reference observation ID differs from context binding")
            if self.reference_observation.canonical_payload_digest != self.reference_digest:
                raise ValueError("reference observation digest differs from context binding")
        if not math.isfinite(self.max_displacement_m):
            raise ValueError("max_displacement_m must be finite")
        return self


class SpatialPressCommand(WireModel):
    """Immutable command addressed to the independent spatial adapter."""

    command_id: OpaqueId
    effect_key: OpaqueId
    position_m: Vector3
    frame_id: OpaqueId
    calibration_version: OpaqueId
    source_observation_id: OpaqueId
    source_detection_id: OpaqueId
    command_digest: Annotated[
        str,
        Field(pattern=r"sha256:[0-9a-f]{64}", min_length=71, max_length=71),
    ]


class TwoButtonLevelEvidence(WireModel):
    """Independent level sensor proof for one named button."""

    target_entity_id: OpaqueId
    desired_latched: Literal[True]
    actual_latched: bool
    device_id: OpaqueId
    counter: Annotated[int, Field(ge=0)]
    observed_at: datetime
    evidence_observation_id: OpaqueId


class TwoButtonEffectEvidence(WireModel):
    """Post-dispatch device proof for one M3a spatial effect."""

    operation_id: UUID
    intent_revision: Literal[1]
    contract_id: UUID
    contract_revision: Literal[1]
    semantic_effect_id: OpaqueId
    target_entity_id: OpaqueId
    effect_key: OpaqueId
    command_digest: Annotated[
        str,
        Field(pattern=r"sha256:[0-9a-f]{64}", min_length=71, max_length=71),
    ]
    device_id: OpaqueId
    observation_id: OpaqueId
    observed_at: datetime
    outcome: Literal["APPLIED", "NOT_APPLIED", "UNKNOWN"]
    # ``False`` means the digest below is the durable command expectation,
    # because the adapter did not report a matching digest.  Such an effect is
    # an explicit UNKNOWN diagnostic and cannot attribute contact or counters.
    command_digest_verified: bool = True
    command_digest_diagnostic: OpaqueId | None = None
    physical_contact: Literal["A", "B", "NONE"] | None = None
    command_executed: bool | None = None
    semantic_goal_attained: bool | None = None
    a_counter: Annotated[int, Field(ge=0)] | None = None
    b_counter: Annotated[int, Field(ge=0)] | None = None
    a_latched: bool | None = None
    b_latched: bool | None = None


class M3aAction(StrEnum):
    EXECUTE = "EXECUTE"
    REANCHOR_EXECUTE = "REANCHOR_EXECUTE"
    HOLD_AMBIGUOUS = "HOLD_AMBIGUOUS"
    HOLD_REFERENCE_MISMATCH = "HOLD_REFERENCE_MISMATCH"
    HOLD_CONTEXT_MISMATCH = "HOLD_CONTEXT_MISMATCH"
    RECOGNIZE_EFFECT = "RECOGNIZE_EFFECT"


class LocalTwoButtonDecision(WireModel):
    """Pure Robot decision with explicit action/reason and budget state."""

    operation_id: UUID
    intent_revision: Literal[1]
    semantic_effect_id: OpaqueId
    reference_observation_id: OpaqueId
    current_observation_id: OpaqueId
    action: M3aAction
    reason: OpaqueId
    selected_detection_id: OpaqueId | None = None
    displacement_m: float | None = Field(default=None, ge=0.0)
    budget_state: OpaqueId
    command_digest: (
        Annotated[
            str,
            Field(pattern=r"sha256:[0-9a-f]{64}", min_length=71, max_length=71),
        ]
        | None
    ) = None
    level_evidence_observation_id: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_displacement(self) -> LocalTwoButtonDecision:
        if self.displacement_m is not None and not math.isfinite(self.displacement_m):
            raise ValueError("displacement_m must be finite")
        return self


Payload = Annotated[
    OperationIntent
    | GroundedOperation
    | OperationPlan
    | TaskAssignment
    | ExecutionContract
    | ExecutionEvent
    | RobotState
    | ArticulatedRobotState
    | RobotForecast
    | SiteSnapshot
    | PredictionManifest
    | EntityDetection
    | TwoButtonObservation
    | M3aEnsureLatchedIntent
    | M3aSpatialExecutionContext
    | SpatialPressCommand
    | TwoButtonLevelEvidence
    | TwoButtonEffectEvidence
    | LocalTwoButtonDecision,
    Field(union_mode="left_to_right"),
]


ROOT_MESSAGE_TYPES = frozenset(
    {
        "operation.intent",
        "robot.state",
        "robot.articulated_state",
        "site.snapshot",
        "m3a.two_button.observation",
        "m3a.ensure_latched.intent",
    }
)
PAYLOAD_TYPES: dict[str, type[WireModel]] = {
    "operation.intent": OperationIntent,
    "operation.grounded": GroundedOperation,
    "operation.plan": OperationPlan,
    "task.assignment": TaskAssignment,
    "execution.contract": ExecutionContract,
    "execution.event": ExecutionEvent,
    "robot.state": RobotState,
    "robot.articulated_state": ArticulatedRobotState,
    "robot.forecast": RobotForecast,
    "site.snapshot": SiteSnapshot,
    "prediction.manifest": PredictionManifest,
    "m3a.two_button.observation": TwoButtonObservation,
    "m3a.ensure_latched.intent": M3aEnsureLatchedIntent,
    "m3a.spatial.context": M3aSpatialExecutionContext,
    "m3a.spatial.command": SpatialPressCommand,
    "m3a.spatial.level": TwoButtonLevelEvidence,
    "m3a.spatial.effect": TwoButtonEffectEvidence,
    "m3a.spatial.decision": LocalTwoButtonDecision,
}


class MessageEnvelope(WireModel):
    protocol_version: Literal["dtt/0"] = "dtt/0"
    message_id: UUID
    message_type: Literal[
        "operation.intent",
        "operation.grounded",
        "operation.plan",
        "task.assignment",
        "execution.contract",
        "execution.event",
        "robot.state",
        "robot.articulated_state",
        "robot.forecast",
        "site.snapshot",
        "prediction.manifest",
        "m3a.two_button.observation",
        "m3a.ensure_latched.intent",
        "m3a.spatial.context",
        "m3a.spatial.command",
        "m3a.spatial.level",
        "m3a.spatial.effect",
        "m3a.spatial.decision",
    ]
    source_id: OpaqueId
    source_boot_id: UUID
    source_sequence: Annotated[int, Field(ge=0)]
    destination_id: OpaqueId
    correlation_id: UUID
    causation_id: UUID | None = None
    created_at: datetime
    not_before: datetime | None = None
    expires_at: datetime | None = None
    payload: Payload

    @model_validator(mode="after")
    def validate_envelope(self) -> MessageEnvelope:
        expected = PAYLOAD_TYPES[self.message_type]
        if type(self.payload) is not expected:
            raise ValueError(f"{self.message_type} requires payload {expected.__name__}")
        if self.message_type not in ROOT_MESSAGE_TYPES and self.causation_id is None:
            raise ValueError("direct-consequence messages require causation_id")
        if self.not_before is not None and self.expires_at is not None:
            if self.expires_at <= self.not_before:
                raise ValueError("expires_at must be after not_before")
        return self
