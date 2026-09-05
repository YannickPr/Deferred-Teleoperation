"""Strict local Mission view models consumed by the Unreal M1 client."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from deferred_teleop.protocol import (
    ArticulatedRobotState,
    ContractState,
    EvidenceMetadata,
    LocalTwoButtonDecision,
    Pose,
    PredictionManifest,
    ProvenanceKind,
    SpatialPressCommand,
    TwoButtonEffectEvidence,
    TwoButtonLevelEvidence,
    TwoButtonObservation,
    WireModel,
)


class MissionConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"


class TimestampBasis(StrEnum):
    WALL_CLOCK_UTC = "WALL_CLOCK_UTC"


class TrajectorySampleSource(StrEnum):
    CONFIRMED_STATE = "CONFIRMED_STATE"
    ARRIVAL_BELIEF = "ARRIVAL_BELIEF"


class MissionConnectionStatus(WireModel):
    mission_to_field: MissionConnectionState
    changed_at: datetime
    detail: Literal["delayed-link"] = "delayed-link"


class ConfirmedStateView(WireModel):
    site_id: str
    robot_id: str
    pose: Pose
    evidence: EvidenceMetadata


class ArrivalBeliefView(WireModel):
    robot_id: str
    pose: Pose
    predicted_for: datetime
    estimated_intent_arrival_at: datetime | None
    link_one_way_delay_seconds: Annotated[float, Field(ge=0.0)]
    evidence: EvidenceMetadata

    @model_validator(mode="after")
    def validate_provenance(self) -> ArrivalBeliefView:
        if self.evidence.provenance is not ProvenanceKind.PREDICTED:
            raise ValueError("arrival belief evidence must be PREDICTED")
        return self


class TargetBranchView(WireModel):
    entity_id: str
    requested_state: Literal["PRESSED"] = "PRESSED"
    condition: Literal["button effect succeeds"] = "button effect succeeds"
    pose: Pose
    evidence: EvidenceMetadata

    @model_validator(mode="after")
    def validate_provenance(self) -> TargetBranchView:
        if self.evidence.provenance is not ProvenanceKind.OPERATOR_ASSERTED:
            raise ValueError("target branch evidence must be OPERATOR_ASSERTED")
        return self


class TimedTrajectorySample(WireModel):
    sample_time: datetime
    timestamp_basis: Literal[TimestampBasis.WALL_CLOCK_UTC] = TimestampBasis.WALL_CLOCK_UTC
    pose: Pose
    source: TrajectorySampleSource
    provenance: ProvenanceKind


class MissionViewStatus(WireModel):
    operation_id: UUID | None
    correlation_id: UUID | None
    terminal_state: ContractState | None
    terminal_contract_id: UUID | None
    received_message_count: Annotated[int, Field(ge=0)]


class MissionViewState(WireModel):
    protocol_version: Literal["dtt/0"] = "dtt/0"
    message_type: Literal["mission.view_state"] = "mission.view_state"
    source_id: str
    source_sequence: Annotated[int, Field(ge=1)]
    produced_at: datetime
    connection: MissionConnectionStatus
    confirmed_state: ConfirmedStateView | None
    arrival_belief: ArrivalBeliefView | None
    target_branch: TargetBranchView | None
    trajectory_forecasts: tuple[TimedTrajectorySample, ...]
    prediction_manifests: tuple[PredictionManifest, ...]
    status: MissionViewStatus


class M3aMissionViewState(WireModel):
    """Dedicated read model for the observed spatial two-button slice.

    The historical ``mission.view_state`` remains unchanged.  This model keeps
    the M3a evidence and physical proof together so a client can distinguish a
    semantic decision, a dispatched command, and the device's contact result.
    """

    protocol_version: Literal["dtt/0"] = "dtt/0"
    message_type: Literal["m3a.view"] = "m3a.view"
    source_id: Annotated[str, Field(min_length=1)]
    source_sequence: Annotated[int, Field(ge=1)]
    produced_at: datetime
    operation_id: UUID | None
    correlation_id: UUID | None
    configured_one_way_delay_seconds: Annotated[float, Field(ge=0.0)]
    reference_observation: TwoButtonObservation | None
    current_observation: TwoButtonObservation | None
    level_evidence: TwoButtonLevelEvidence | None
    decision: LocalTwoButtonDecision | None
    command: SpatialPressCommand | None
    effect_evidence: TwoButtonEffectEvidence | None
    contract_id: UUID | None
    contract_state: ContractState | None
    terminal_result: dict[str, object] | None
    business_result: str | None
    physical_contact: Literal["A", "B", "NONE"] | None
    a_counter: Annotated[int | None, Field(ge=0)]
    b_counter: Annotated[int | None, Field(ge=0)]
    a_latched: bool | None
    b_latched: bool | None

    @model_validator(mode="after")
    def validate_identity(self) -> M3aMissionViewState:
        if not self.source_id.strip():
            raise ValueError("source_id must not be blank")
        if self.business_result is not None and not self.business_result.strip():
            raise ValueError("business_result must not be blank")
        return self


# A short alias keeps call sites readable while retaining the explicit model
# name for schema and documentation consumers.
M3aViewState = M3aMissionViewState
M3ATwoButtonViewState = M3aMissionViewState


class ArticulatedArrivalRobotState(WireModel):
    """Predicted articulated state and the timing assumptions behind it."""

    robot_state: ArticulatedRobotState
    predicted_for: datetime
    estimated_intent_arrival_at: datetime | None
    link_one_way_delay_seconds: Annotated[float, Field(ge=0.0, le=86_400.0)]

    @model_validator(mode="after")
    def validate_prediction(self) -> ArticulatedArrivalRobotState:
        if self.robot_state.evidence.provenance is not ProvenanceKind.PREDICTED:
            raise ValueError("arrival articulated state evidence must be PREDICTED")
        if self.predicted_for <= self.robot_state.evidence.produced_at:
            raise ValueError("predicted_for must be after produced_at")
        if not math.isfinite(self.link_one_way_delay_seconds):
            raise ValueError("link_one_way_delay_seconds must be finite")
        return self


class ArticulatedMissionViewState(WireModel):
    """Strict M2 Mission frame; its three articulated layers are independent nullable keys."""

    protocol_version: Literal["dtt/0"] = "dtt/0"
    message_type: Literal["mission.articulated_view_state"] = "mission.articulated_view_state"
    source_id: Annotated[str, Field(min_length=1)]
    source_sequence: Annotated[int, Field(ge=1)]
    produced_at: datetime
    connection: MissionConnectionStatus
    status: MissionViewStatus
    confirmed_robot_state: ArticulatedRobotState | None
    arrival_robot_state: ArticulatedArrivalRobotState | None
    target_robot_state: ArticulatedRobotState | None

    @model_validator(mode="after")
    def validate_layer_provenance(self) -> ArticulatedMissionViewState:
        if not self.source_id.strip():
            raise ValueError("source_id must not be blank")
        confirmed_provenance = (
            self.confirmed_robot_state.evidence.provenance
            if self.confirmed_robot_state is not None
            else None
        )
        if confirmed_provenance not in {
            ProvenanceKind.MEASURED,
            ProvenanceKind.FUSED,
        }:
            if self.confirmed_robot_state is not None:
                raise ValueError("confirmed articulated state evidence must be MEASURED or FUSED")
        target_provenance = (
            self.target_robot_state.evidence.provenance
            if self.target_robot_state is not None
            else None
        )
        if (
            target_provenance is not None
            and target_provenance is not ProvenanceKind.OPERATOR_ASSERTED
        ):
            raise ValueError("target articulated state evidence must be OPERATOR_ASSERTED")
        return self
