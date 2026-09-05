"""Pure spatial policy for the M3a two-button slice.

This module intentionally has no fixture import, scenario switch, position map,
counter, or device access.  It receives only the persisted reference/current
observations and optional independent level evidence.  Physical contact is
decided by :mod:`deferred_teleop.two_button_fixture` after Robot has derived a
command from the returned decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from deferred_teleop.m3a_types import (
    EntityDetection,
    LocalTwoButtonDecision,
    M3aEnsureLatchedIntent,
    SpatialPressCommand,
    TwoButtonAction,
    TwoButtonLevelEvidence,
    TwoButtonObservation,
    canonical_digest,
)


def _position(pose: Any) -> tuple[float, float, float]:
    position = pose.position
    return (float(position.x), float(position.y), float(position.z))


def _distance(first: Any, second: Any) -> float:
    left = _position(first)
    right = _position(second)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))


def _same_instant(first: object, second: object) -> bool:
    return first == second


@dataclass(frozen=True, slots=True)
class ReferenceVerification:
    """Result of checking the exact persisted authoring observation."""

    valid: bool
    action: TwoButtonAction | None = None
    reason: str = ""


def _decision(
    intent: M3aEnsureLatchedIntent,
    current_observation: TwoButtonObservation,
    *,
    action: TwoButtonAction,
    reason: str,
    selected_detection_id: str | None = None,
    displacement_m: float | None = None,
    budget_state: str = "NOT_ADMITTED",
    level_evidence: TwoButtonLevelEvidence | None = None,
) -> LocalTwoButtonDecision:
    return LocalTwoButtonDecision(
        operation_id=intent.operation_id,
        intent_revision=intent.intent_revision,
        semantic_effect_id=intent.semantic_effect_id,
        reference_observation_id=intent.reference_observation_id,
        current_observation_id=current_observation.observation_id,
        action=action,
        reason=reason,
        selected_detection_id=selected_detection_id,
        displacement_m=displacement_m,
        budget_state=budget_state,
        level_evidence_observation_id=(
            level_evidence.evidence_observation_id if level_evidence is not None else None
        ),
    )


def _hold_reference(
    intent: M3aEnsureLatchedIntent,
    current_observation: TwoButtonObservation,
    reason: str,
    *,
    level_evidence: TwoButtonLevelEvidence | None = None,
) -> LocalTwoButtonDecision:
    return _decision(
        intent,
        current_observation,
        action=TwoButtonAction.HOLD_REFERENCE_MISMATCH,
        reason=reason,
        level_evidence=level_evidence,
    )


def _hold_context(
    intent: M3aEnsureLatchedIntent,
    current_observation: TwoButtonObservation,
    reason: str,
    *,
    level_evidence: TwoButtonLevelEvidence | None = None,
) -> LocalTwoButtonDecision:
    return _decision(
        intent,
        current_observation,
        action=TwoButtonAction.HOLD_CONTEXT_MISMATCH,
        reason=reason,
        level_evidence=level_evidence,
    )


def _hold_ambiguous(
    intent: M3aEnsureLatchedIntent,
    current_observation: TwoButtonObservation,
    reason: str,
    *,
    level_evidence: TwoButtonLevelEvidence | None = None,
    displacement_m: float | None = None,
    budget_state: str = "NOT_ADMITTED",
) -> LocalTwoButtonDecision:
    return _decision(
        intent,
        current_observation,
        action=TwoButtonAction.HOLD_AMBIGUOUS,
        reason=reason,
        displacement_m=displacement_m,
        budget_state=budget_state,
        level_evidence=level_evidence,
    )


def verify_reference(
    intent: M3aEnsureLatchedIntent,
    reference_observation: TwoButtonObservation,
) -> ReferenceVerification:
    """Check every reference field against the persisted observation.

    A changed pose or payload under the same observation ID is a reference
    mismatch.  Frame and calibration mismatches are context mismatches, so the
    caller can surface those separately.  This function never accepts an
    embedded pose by itself: the observation ID and its recomputed digest are
    mandatory.
    """

    if reference_observation.observation_id != intent.reference_observation_id:
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_REFERENCE_MISMATCH, "REFERENCE_OBSERVATION_ID_MISMATCH"
        )
    if reference_observation.canonical_payload_digest != canonical_digest(
        reference_observation._payload_without_digest()
    ):
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_REFERENCE_MISMATCH, "REFERENCE_OBSERVATION_DIGEST_INVALID"
        )
    if reference_observation.frame_id != intent.reference_frame_id:
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_CONTEXT_MISMATCH, "REFERENCE_FRAME_MISMATCH"
        )
    if reference_observation.calibration_version != intent.reference_calibration_version:
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_CONTEXT_MISMATCH, "REFERENCE_CALIBRATION_MISMATCH"
        )
    if reference_observation.canonical_payload_digest != intent.reference_digest:
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_REFERENCE_MISMATCH, "REFERENCE_OBSERVATION_DIGEST_MISMATCH"
        )
    if reference_observation.world_revision != intent.reference_world_revision:
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_REFERENCE_MISMATCH, "REFERENCE_WORLD_REVISION_MISMATCH"
        )
    if not _same_instant(reference_observation.observed_at, intent.reference_observed_at):
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_REFERENCE_MISMATCH, "REFERENCE_OBSERVED_AT_MISMATCH"
        )

    detections = tuple(
        detection
        for detection in reference_observation.detections
        if detection.detection_id == intent.reference_detection_id
    )
    if len(detections) != 1:
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_REFERENCE_MISMATCH, "REFERENCE_DETECTION_ID_MISMATCH"
        )
    detection = detections[0]
    if detection.candidate_entity_ids != (intent.target_entity_id,):
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_AMBIGUOUS, "REFERENCE_DETECTION_AMBIGUOUS"
        )
    if detection.pose.frame.frame_id != intent.reference_frame_id:
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_CONTEXT_MISMATCH, "REFERENCE_DETECTION_FRAME_MISMATCH"
        )
    if detection.pose.frame.calibration_version != intent.reference_calibration_version:
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_CONTEXT_MISMATCH, "REFERENCE_DETECTION_CALIBRATION_MISMATCH"
        )
    if detection.pose != intent.reference_pose:
        return ReferenceVerification(
            False, TwoButtonAction.HOLD_REFERENCE_MISMATCH, "REFERENCE_POSE_MISMATCH"
        )
    return ReferenceVerification(True)


def _verify_current_context(
    intent: M3aEnsureLatchedIntent,
    reference_observation: TwoButtonObservation,
    current_observation: TwoButtonObservation,
    *,
    expected_source_id: str | None,
) -> tuple[TwoButtonAction | None, str]:
    if expected_source_id is not None and current_observation.source_id != expected_source_id:
        return TwoButtonAction.HOLD_CONTEXT_MISMATCH, "CURRENT_SOURCE_MISMATCH"
    if current_observation.source_id != reference_observation.source_id:
        return TwoButtonAction.HOLD_CONTEXT_MISMATCH, "CURRENT_SOURCE_MISMATCH"
    if current_observation.frame_id != intent.reference_frame_id:
        return TwoButtonAction.HOLD_CONTEXT_MISMATCH, "CURRENT_FRAME_MISMATCH"
    if current_observation.calibration_version != intent.reference_calibration_version:
        return TwoButtonAction.HOLD_CONTEXT_MISMATCH, "CURRENT_CALIBRATION_MISMATCH"
    if current_observation.canonical_payload_digest != canonical_digest(
        current_observation._payload_without_digest()
    ):
        return TwoButtonAction.HOLD_CONTEXT_MISMATCH, "CURRENT_OBSERVATION_DIGEST_INVALID"
    return None, ""


def _matching_unique_detection(
    observation: TwoButtonObservation,
    target_entity_id: str,
) -> tuple[EntityDetection | None, str | None]:
    matching = tuple(
        detection
        for detection in observation.detections
        if target_entity_id in detection.candidate_entity_ids
    )
    if not matching:
        return None, "TARGET_ABSENT"
    if len(matching) != 1:
        return None, "TARGET_NONUNIQUE"
    detection = matching[0]
    if detection.candidate_entity_ids != (target_entity_id,):
        return None, "TARGET_CANDIDATE_SET_AMBIGUOUS"
    if not detection.visibility:
        return None, "TARGET_NOT_VISIBLE"
    return detection, None


def decide_two_button(
    intent: M3aEnsureLatchedIntent,
    reference_observation: TwoButtonObservation,
    current_observation: TwoButtonObservation,
    level_evidence: TwoButtonLevelEvidence | None = None,
    *,
    expected_source_id: str | None = None,
    expected_device_id: str | None = None,
    budget_state: str = "NOT_ADMITTED",
) -> LocalTwoButtonDecision:
    """Return one deterministic action for the supplied immutable evidence.

    The order is deliberate: exact authoring integrity and frame/calibration
    context are checked before a level shortcut.  A trusted already-latched
    level can therefore avoid spatial selection and all physical work, but it
    cannot bypass a stale or mismatched reference contract.
    """

    reference_result = verify_reference(intent, reference_observation)
    if not reference_result.valid:
        if reference_result.action is TwoButtonAction.HOLD_CONTEXT_MISMATCH:
            return _hold_context(
                intent, current_observation, reference_result.reason, level_evidence=level_evidence
            )
        if reference_result.action is TwoButtonAction.HOLD_AMBIGUOUS:
            return _hold_ambiguous(
                intent, current_observation, reference_result.reason, level_evidence=level_evidence
            )
        return _hold_reference(
            intent, current_observation, reference_result.reason, level_evidence=level_evidence
        )

    context_action, context_reason = _verify_current_context(
        intent,
        reference_observation,
        current_observation,
        expected_source_id=expected_source_id,
    )
    if context_action is not None:
        return _decision(
            intent,
            current_observation,
            action=context_action,
            reason=context_reason,
            budget_state=budget_state,
            level_evidence=level_evidence,
        )

    if level_evidence is not None:
        if level_evidence.target_entity_id != intent.target_entity_id:
            return _hold_context(
                intent, current_observation, "LEVEL_TARGET_MISMATCH", level_evidence=level_evidence
            )
        if expected_device_id is not None and level_evidence.device_id != expected_device_id:
            return _hold_context(
                intent, current_observation, "LEVEL_DEVICE_MISMATCH", level_evidence=level_evidence
            )
        if level_evidence.desired_latched is not True:
            return _hold_reference(
                intent,
                current_observation,
                "LEVEL_DESIRED_STATE_MISMATCH",
                level_evidence=level_evidence,
            )
        if level_evidence.actual_latched:
            return _decision(
                intent,
                current_observation,
                action=TwoButtonAction.RECOGNIZE_EFFECT,
                reason="ALREADY_LATCHED",
                budget_state="ZERO_RESERVATION_REQUIRED",
                level_evidence=level_evidence,
            )

    current_detection, ambiguity = _matching_unique_detection(
        current_observation,
        intent.target_entity_id,
    )
    if current_detection is None:
        return _hold_ambiguous(
            intent,
            current_observation,
            ambiguity or "TARGET_AMBIGUOUS",
            budget_state=budget_state,
            level_evidence=level_evidence,
        )

    reference_detection = next(
        detection
        for detection in reference_observation.detections
        if detection.detection_id == intent.reference_detection_id
    )
    displacement = _distance(reference_detection.pose, current_detection.pose)
    if displacement > intent.max_displacement_m:
        return _hold_ambiguous(
            intent,
            current_observation,
            "DISPLACEMENT_EXCEEDS_TOLERANCE",
            displacement_m=displacement,
            budget_state=budget_state,
            level_evidence=level_evidence,
        )
    if displacement == 0.0:
        action = TwoButtonAction.EXECUTE
        reason = "REFERENCE_STILL_VALID"
        selected_detection_id = reference_detection.detection_id
    else:
        action = TwoButtonAction.REANCHOR_EXECUTE
        reason = "SAME_ID_REANCHORED"
        selected_detection_id = current_detection.detection_id
    return _decision(
        intent,
        current_observation,
        action=action,
        reason=reason,
        selected_detection_id=selected_detection_id,
        displacement_m=displacement,
        budget_state=budget_state,
        level_evidence=level_evidence,
    )


def derive_spatial_press_command(
    decision: LocalTwoButtonDecision,
    *,
    effect_key: str,
    command_id: str,
    reference_observation: TwoButtonObservation,
    current_observation: TwoButtonObservation,
) -> SpatialPressCommand:
    """Derive the immutable physical command from a prior policy decision."""

    if decision.action not in {TwoButtonAction.EXECUTE, TwoButtonAction.REANCHOR_EXECUTE}:
        raise ValueError("a hold or recognition decision cannot produce a press command")
    if decision.selected_detection_id is None:
        raise ValueError("execute decision has no selected detection")
    source_observation = (
        reference_observation if decision.action is TwoButtonAction.EXECUTE else current_observation
    )
    detections = tuple(
        detection
        for detection in source_observation.detections
        if detection.detection_id == decision.selected_detection_id
    )
    if len(detections) != 1:
        raise ValueError("selected detection is not present exactly once")
    command = SpatialPressCommand.from_pose(
        command_id=command_id,
        effect_key=effect_key,
        pose=detections[0].pose,
        source_observation_id=source_observation.observation_id,
        source_detection_id=detections[0].detection_id,
    )
    # Keep the decision useful as a durable audit record without mutating its
    # frozen value.  Callers can persist the returned command digest alongside
    # the original decision.
    return command


class TwoButtonPolicy:
    """Stateless object façade for callers that prefer dependency injection."""

    def decide(
        self,
        intent: M3aEnsureLatchedIntent,
        reference_observation: TwoButtonObservation,
        current_observation: TwoButtonObservation,
        level_evidence: TwoButtonLevelEvidence | None = None,
        *,
        expected_source_id: str | None = None,
        expected_device_id: str | None = None,
        budget_state: str = "NOT_ADMITTED",
    ) -> LocalTwoButtonDecision:
        return decide_two_button(
            intent,
            reference_observation,
            current_observation,
            level_evidence,
            expected_source_id=expected_source_id,
            expected_device_id=expected_device_id,
            budget_state=budget_state,
        )


decide = decide_two_button
command_from_decision = derive_spatial_press_command


__all__ = [
    "ReferenceVerification",
    "TwoButtonPolicy",
    "command_from_decision",
    "decide",
    "decide_two_button",
    "derive_spatial_press_command",
    "verify_reference",
]
