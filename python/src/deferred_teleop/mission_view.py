"""Strict local Mission view models consumed by the Unreal M1 client."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from deferred_teleop.protocol import (
    ContractState,
    EvidenceMetadata,
    Pose,
    PredictionManifest,
    ProvenanceKind,
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
