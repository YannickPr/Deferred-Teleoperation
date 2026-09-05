"""Small immutable value types used by the M3a two-button slice.

The M3a messages deliberately live beside the historical ``dtt/0`` protocol
models while the service integration is being reviewed.  They are plain local
types: no existing wire payload or golden fixture is changed by this module.

The canonical encoders in this file are also used by the spatial fixture.  A
digest is always computed over the payload without its digest field; this
avoids a self-referential value and makes the bytes persisted by the device
journal independently reproducible.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from deferred_teleop.protocol import Pose


class M3aTypeError(ValueError):
    """Raised when an M3a value cannot satisfy its immutable contract."""


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M3aTypeError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise M3aTypeError(f"{field_name} must be a non-empty string")
    return value


def _require_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise M3aTypeError(f"{field_name} must be timezone-aware")
    return value


def _require_uuid(value: object, *, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as error:
            raise M3aTypeError(f"{field_name} must be a UUID") from error
    raise M3aTypeError(f"{field_name} must be a UUID")


def _require_int(value: object, *, field_name: str, minimum: int = 0) -> int:
    # bool is an int subclass but has no place in a revision or counter.
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise M3aTypeError(f"{field_name} must be an integer >= {minimum}")
    return value


def _require_finite(value: object, *, field_name: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise M3aTypeError(f"{field_name} must be a finite number >= {minimum}")
    converted = float(value)
    if not math.isfinite(converted) or converted < minimum:
        raise M3aTypeError(f"{field_name} must be a finite number >= {minimum}")
    return converted


def _require_pose(value: object, *, field_name: str) -> Pose:
    if not isinstance(value, Pose):
        raise M3aTypeError(f"{field_name} must be a protocol Pose")
    return value


def _require_bool(value: object, *, field_name: str) -> bool:
    if type(value) is not bool:
        raise M3aTypeError(f"{field_name} must be a boolean")
    return value


def _canonicalize(value: object) -> object:
    """Convert supported values to a JSON-safe, deterministic tree."""

    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _canonicalize(
            {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
        )
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _canonicalize(model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise M3aTypeError("canonical payload cannot contain a non-finite number")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise M3aTypeError(f"unsupported canonical payload value: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    """Return canonical UTF-8 JSON bytes for an M3a value or payload tree."""

    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_digest(value: object) -> str:
    """Return the repository's ``sha256:<hex>`` digest spelling."""

    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _validate_digest(value: object, *, expected: str, field_name: str) -> str:
    digest = _require_text(value, field_name=field_name)
    if digest != expected:
        raise M3aTypeError(f"{field_name} does not match canonical payload")
    if len(digest) != 71 or not digest.startswith("sha256:"):
        raise M3aTypeError(f"{field_name} must be sha256:<64 lowercase hexadecimal digits>")
    if any(character not in "0123456789abcdef" for character in digest[7:]):
        raise M3aTypeError(f"{field_name} must be sha256:<64 lowercase hexadecimal digits>")
    return digest


def _pose_dump(pose: Pose, *, mode: str = "python") -> dict[str, Any]:
    return pose.model_dump(mode=mode)  # type: ignore[return-value]


def _normalise_candidate_ids(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise M3aTypeError("candidate_entity_ids must be a sequence of IDs")
    candidates = tuple(_require_text(item, field_name="candidate_entity_id") for item in value)
    if not candidates:
        raise M3aTypeError("candidate_entity_ids must not be empty")
    if len(set(candidates)) != len(candidates):
        raise M3aTypeError("candidate_entity_ids must not contain duplicates")
    return candidates


@dataclasses.dataclass(frozen=True, slots=True)
class EntityDetection:
    """One observed candidate set and its measured pose."""

    detection_id: str
    candidate_entity_ids: tuple[str, ...]
    pose: Pose
    visibility: bool
    source_evidence_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "detection_id", _require_text(self.detection_id, field_name="detection_id")
        )
        object.__setattr__(
            self,
            "candidate_entity_ids",
            _normalise_candidate_ids(self.candidate_entity_ids),
        )
        object.__setattr__(self, "pose", _require_pose(self.pose, field_name="pose"))
        object.__setattr__(
            self, "visibility", _require_bool(self.visibility, field_name="visibility")
        )
        object.__setattr__(
            self,
            "source_evidence_id",
            _require_text(self.source_evidence_id, field_name="source_evidence_id"),
        )

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "detection_id": self.detection_id,
            "candidate_entity_ids": list(self.candidate_entity_ids),
            "pose": _pose_dump(self.pose, mode=mode),
            "visibility": self.visibility,
            "source_evidence_id": self.source_evidence_id,
        }

    @property
    def is_unique(self) -> bool:
        return len(self.candidate_entity_ids) == 1


@dataclasses.dataclass(frozen=True, slots=True)
class TwoButtonObservation:
    """Persisted observer output used for both authoring and execution."""

    observation_id: str
    source_id: str
    world_revision: int
    observed_at: datetime
    produced_at: datetime
    frame_id: str
    calibration_version: str
    detections: tuple[EntityDetection, ...]
    canonical_payload_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _require_text(self.observation_id, field_name="observation_id")
        )
        object.__setattr__(self, "source_id", _require_text(self.source_id, field_name="source_id"))
        object.__setattr__(
            self,
            "world_revision",
            _require_int(self.world_revision, field_name="world_revision", minimum=1),
        )
        observed_at = _require_datetime(self.observed_at, field_name="observed_at")
        produced_at = _require_datetime(self.produced_at, field_name="produced_at")
        if produced_at < observed_at:
            raise M3aTypeError("produced_at cannot precede observed_at")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "produced_at", produced_at)
        object.__setattr__(self, "frame_id", _require_text(self.frame_id, field_name="frame_id"))
        object.__setattr__(
            self,
            "calibration_version",
            _require_text(self.calibration_version, field_name="calibration_version"),
        )
        if isinstance(self.detections, (str, bytes)) or not isinstance(
            self.detections, (tuple, list)
        ):
            raise M3aTypeError("detections must be a sequence")
        detections = tuple(self.detections)
        if any(not isinstance(detection, EntityDetection) for detection in detections):
            raise M3aTypeError("detections must contain EntityDetection values")
        for detection in detections:
            if detection.pose.frame.frame_id != self.frame_id:
                raise M3aTypeError("detection pose frame does not match observation frame_id")
            if detection.pose.frame.calibration_version != self.calibration_version:
                raise M3aTypeError(
                    "detection pose calibration does not match observation calibration_version"
                )
        if len({detection.detection_id for detection in detections}) != len(detections):
            raise M3aTypeError("detections must not contain duplicate detection_id values")
        object.__setattr__(self, "detections", detections)
        expected = canonical_digest(self._payload_without_digest())
        supplied = self.canonical_payload_digest
        if supplied:
            object.__setattr__(
                self,
                "canonical_payload_digest",
                _validate_digest(
                    supplied,
                    expected=expected,
                    field_name="canonical_payload_digest",
                ),
            )
        else:
            object.__setattr__(self, "canonical_payload_digest", expected)

    def _payload_without_digest(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_id": self.source_id,
            "world_revision": self.world_revision,
            "observed_at": self.observed_at,
            "produced_at": self.produced_at,
            "frame_id": self.frame_id,
            "calibration_version": self.calibration_version,
            "detections": self.detections,
        }

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            **self._payload_without_digest(),
            "observed_at": _utc_text(self.observed_at) if mode == "json" else self.observed_at,
            "produced_at": _utc_text(self.produced_at) if mode == "json" else self.produced_at,
            "detections": [detection.model_dump(mode=mode) for detection in self.detections],
            "canonical_payload_digest": self.canonical_payload_digest,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self._payload_without_digest())


@dataclasses.dataclass(frozen=True, slots=True)
class M3aEnsureLatchedIntent:
    """The one immutable revision-1 spatial intent admitted by M3a."""

    operation_id: UUID
    intent_revision: int
    semantic_effect_id: str
    target_entity_id: str
    desired_latched: bool
    reference_observation_id: str
    reference_detection_id: str
    reference_digest: str
    reference_pose: Pose
    reference_frame_id: str
    reference_calibration_version: str
    reference_world_revision: int
    reference_observed_at: datetime
    same_identity_only: bool = True
    max_displacement_m: float = 0.0
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation_id", _require_uuid(self.operation_id, field_name="operation_id")
        )
        if self.intent_revision != 1:
            raise M3aTypeError("M3aEnsureLatchedIntent supports intent_revision 1 only")
        object.__setattr__(
            self,
            "semantic_effect_id",
            _require_text(self.semantic_effect_id, field_name="semantic_effect_id"),
        )
        object.__setattr__(
            self,
            "target_entity_id",
            _require_text(self.target_entity_id, field_name="target_entity_id"),
        )
        if self.desired_latched is not True:
            raise M3aTypeError("M3aEnsureLatchedIntent desired_latched must be true")
        object.__setattr__(self, "desired_latched", True)
        for field_name in (
            "reference_observation_id",
            "reference_detection_id",
            "reference_digest",
            "reference_frame_id",
            "reference_calibration_version",
        ):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name=field_name)
            )
        object.__setattr__(
            self, "reference_pose", _require_pose(self.reference_pose, field_name="reference_pose")
        )
        if self.reference_pose.frame.frame_id != self.reference_frame_id:
            raise M3aTypeError("reference_pose frame does not match reference_frame_id")
        if self.reference_pose.frame.calibration_version != self.reference_calibration_version:
            raise M3aTypeError(
                "reference_pose calibration does not match reference_calibration_version"
            )
        object.__setattr__(
            self,
            "reference_world_revision",
            _require_int(
                self.reference_world_revision, field_name="reference_world_revision", minimum=1
            ),
        )
        object.__setattr__(
            self,
            "reference_observed_at",
            _require_datetime(self.reference_observed_at, field_name="reference_observed_at"),
        )
        object.__setattr__(
            self,
            "same_identity_only",
            _require_bool(self.same_identity_only, field_name="same_identity_only"),
        )
        if not self.same_identity_only:
            raise M3aTypeError("M3aEnsureLatchedIntent requires same_identity_only=true")
        object.__setattr__(
            self,
            "max_displacement_m",
            _require_finite(self.max_displacement_m, field_name="max_displacement_m"),
        )
        if self.expires_at is not None:
            object.__setattr__(
                self, "expires_at", _require_datetime(self.expires_at, field_name="expires_at")
            )

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        result: dict[str, Any] = {
            "operation_id": self.operation_id,
            "intent_revision": self.intent_revision,
            "semantic_effect_id": self.semantic_effect_id,
            "target_entity_id": self.target_entity_id,
            "desired_latched": self.desired_latched,
            "reference_observation_id": self.reference_observation_id,
            "reference_detection_id": self.reference_detection_id,
            "reference_digest": self.reference_digest,
            "reference_pose": _pose_dump(self.reference_pose, mode=mode),
            "reference_frame_id": self.reference_frame_id,
            "reference_calibration_version": self.reference_calibration_version,
            "reference_world_revision": self.reference_world_revision,
            "reference_observed_at": self.reference_observed_at,
            "same_identity_only": self.same_identity_only,
            "max_displacement_m": self.max_displacement_m,
            "expires_at": self.expires_at,
        }
        if mode == "json":
            result["operation_id"] = str(self.operation_id)
            result["reference_observed_at"] = _utc_text(self.reference_observed_at)
            result["expires_at"] = _utc_text(self.expires_at) if self.expires_at else None
        return result

    @property
    def canonical_intent_digest(self) -> str:
        return canonical_digest(self.model_dump(mode="python"))


@dataclasses.dataclass(frozen=True, slots=True)
class M3aSpatialExecutionContext:
    """Field's verified contract/context binding delivered to Robot."""

    operation_id: UUID
    intent_revision: int
    contract_id: UUID
    contract_revision: int
    task_id: UUID
    semantic_effect_id: str
    target_entity_id: str
    reference_observation_id: str
    reference_detection_id: str
    reference_digest: str
    reference_pose: Pose
    reference_frame_id: str
    reference_calibration_version: str
    reference_world_revision: int
    reference_observed_at: datetime
    current_observation_envelope_id: str
    current_observation: TwoButtonObservation
    reference_observation: TwoButtonObservation | None = None
    same_identity_only: bool = True
    max_displacement_m: float = 0.0
    expires_at: datetime | None = None
    expected_device_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation_id", _require_uuid(self.operation_id, field_name="operation_id")
        )
        object.__setattr__(
            self, "contract_id", _require_uuid(self.contract_id, field_name="contract_id")
        )
        object.__setattr__(self, "task_id", _require_uuid(self.task_id, field_name="task_id"))
        if self.intent_revision != 1 or self.contract_revision != 1:
            raise M3aTypeError("M3a spatial context supports revision 1 only")
        object.__setattr__(
            self,
            "semantic_effect_id",
            _require_text(self.semantic_effect_id, field_name="semantic_effect_id"),
        )
        object.__setattr__(
            self,
            "target_entity_id",
            _require_text(self.target_entity_id, field_name="target_entity_id"),
        )
        for field_name in (
            "reference_observation_id",
            "reference_detection_id",
            "reference_digest",
            "reference_frame_id",
            "reference_calibration_version",
            "current_observation_envelope_id",
        ):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name=field_name)
            )
        object.__setattr__(
            self, "reference_pose", _require_pose(self.reference_pose, field_name="reference_pose")
        )
        if self.reference_pose.frame.frame_id != self.reference_frame_id:
            raise M3aTypeError("reference_pose frame does not match reference_frame_id")
        if self.reference_pose.frame.calibration_version != self.reference_calibration_version:
            raise M3aTypeError(
                "reference_pose calibration does not match reference_calibration_version"
            )
        object.__setattr__(
            self,
            "reference_world_revision",
            _require_int(
                self.reference_world_revision, field_name="reference_world_revision", minimum=1
            ),
        )
        object.__setattr__(
            self,
            "reference_observed_at",
            _require_datetime(self.reference_observed_at, field_name="reference_observed_at"),
        )
        object.__setattr__(self, "current_observation", self.current_observation)
        if not isinstance(self.current_observation, TwoButtonObservation):
            raise M3aTypeError("current_observation must be TwoButtonObservation")
        if self.reference_observation is not None:
            if not isinstance(self.reference_observation, TwoButtonObservation):
                raise M3aTypeError("reference_observation must be TwoButtonObservation")
            if self.reference_observation.observation_id != self.reference_observation_id:
                raise M3aTypeError("reference_observation ID differs from context binding")
            if self.reference_observation.canonical_payload_digest != self.reference_digest:
                raise M3aTypeError("reference_observation digest differs from context binding")
        object.__setattr__(
            self,
            "same_identity_only",
            _require_bool(self.same_identity_only, field_name="same_identity_only"),
        )
        if not self.same_identity_only:
            raise M3aTypeError("M3a spatial context requires same_identity_only=true")
        object.__setattr__(
            self,
            "max_displacement_m",
            _require_finite(self.max_displacement_m, field_name="max_displacement_m"),
        )
        if self.expires_at is not None:
            object.__setattr__(
                self, "expires_at", _require_datetime(self.expires_at, field_name="expires_at")
            )
        if self.expected_device_id is not None:
            object.__setattr__(
                self,
                "expected_device_id",
                _require_text(self.expected_device_id, field_name="expected_device_id"),
            )

    @property
    def current_observation_canonical_payload_digest(self) -> str:
        return self.current_observation.canonical_payload_digest

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        result: dict[str, Any] = {
            "operation_id": self.operation_id,
            "intent_revision": self.intent_revision,
            "contract_id": self.contract_id,
            "contract_revision": self.contract_revision,
            "task_id": self.task_id,
            "semantic_effect_id": self.semantic_effect_id,
            "target_entity_id": self.target_entity_id,
            "reference_observation_id": self.reference_observation_id,
            "reference_detection_id": self.reference_detection_id,
            "reference_digest": self.reference_digest,
            "reference_pose": _pose_dump(self.reference_pose, mode=mode),
            "reference_frame_id": self.reference_frame_id,
            "reference_calibration_version": self.reference_calibration_version,
            "reference_world_revision": self.reference_world_revision,
            "reference_observed_at": self.reference_observed_at,
            "current_observation_envelope_id": self.current_observation_envelope_id,
            "current_observation": self.current_observation.model_dump(mode=mode),
            "reference_observation": (
                self.reference_observation.model_dump(mode=mode)
                if self.reference_observation is not None
                else None
            ),
            "same_identity_only": self.same_identity_only,
            "max_displacement_m": self.max_displacement_m,
            "expires_at": self.expires_at,
            "expected_device_id": self.expected_device_id,
        }
        if mode == "json":
            result["operation_id"] = str(self.operation_id)
            result["contract_id"] = str(self.contract_id)
            result["task_id"] = str(self.task_id)
            result["reference_observed_at"] = _utc_text(self.reference_observed_at)
            result["expires_at"] = _utc_text(self.expires_at) if self.expires_at else None
        return result


@dataclasses.dataclass(frozen=True, slots=True)
class SpatialPressCommand:
    """The only physical command accepted by the spatial fixture."""

    command_id: str
    effect_key: str
    position_m: tuple[float, float, float]
    frame_id: str
    calibration_version: str
    source_observation_id: str
    source_detection_id: str
    command_digest: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "command_id",
            "effect_key",
            "frame_id",
            "calibration_version",
            "source_observation_id",
            "source_detection_id",
        ):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name=field_name)
            )
        if isinstance(self.position_m, (str, bytes)) or not isinstance(
            self.position_m, (tuple, list)
        ):
            raise M3aTypeError("position_m must contain exactly three numbers")
        if len(self.position_m) != 3:
            raise M3aTypeError("position_m must contain exactly three numbers")
        position = tuple(
            _require_finite(component, field_name="position_m component", minimum=-math.inf)
            for component in self.position_m
        )
        object.__setattr__(self, "position_m", position)
        expected = canonical_digest(self._payload_without_digest())
        if self.command_digest:
            object.__setattr__(
                self,
                "command_digest",
                _validate_digest(
                    self.command_digest, expected=expected, field_name="command_digest"
                ),
            )
        else:
            object.__setattr__(self, "command_digest", expected)

    def _payload_without_digest(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "effect_key": self.effect_key,
            "position_m": self.position_m,
            "frame_id": self.frame_id,
            "calibration_version": self.calibration_version,
            "source_observation_id": self.source_observation_id,
            "source_detection_id": self.source_detection_id,
        }

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {**self._payload_without_digest(), "command_digest": self.command_digest}

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self._payload_without_digest())

    @classmethod
    def from_pose(
        cls,
        *,
        command_id: str,
        effect_key: str,
        pose: Pose,
        source_observation_id: str,
        source_detection_id: str,
    ) -> SpatialPressCommand:
        return cls(
            command_id=command_id,
            effect_key=effect_key,
            position_m=(pose.position.x, pose.position.y, pose.position.z),
            frame_id=pose.frame.frame_id,
            calibration_version=pose.frame.calibration_version,
            source_observation_id=source_observation_id,
            source_detection_id=source_detection_id,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class SpatialBindingReceipt:
    """Durable device receipt returned by ``bind``."""

    device_id: str
    effect_key: str
    command_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", _require_text(self.device_id, field_name="device_id"))
        object.__setattr__(
            self, "effect_key", _require_text(self.effect_key, field_name="effect_key")
        )
        object.__setattr__(
            self, "command_digest", _require_text(self.command_digest, field_name="command_digest")
        )

    def model_dump(self, *, mode: str = "python") -> dict[str, str]:
        return {
            "device_id": self.device_id,
            "effect_key": self.effect_key,
            "command_digest": self.command_digest,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class TwoButtonLevelEvidence:
    """Independent level sensor evidence for one named button."""

    target_entity_id: str
    desired_latched: bool
    actual_latched: bool
    device_id: str
    counter: int
    observed_at: datetime
    evidence_observation_id: str

    def __post_init__(self) -> None:
        for field_name in ("target_entity_id", "device_id", "evidence_observation_id"):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name=field_name)
            )
        object.__setattr__(
            self,
            "desired_latched",
            _require_bool(self.desired_latched, field_name="desired_latched"),
        )
        object.__setattr__(
            self, "actual_latched", _require_bool(self.actual_latched, field_name="actual_latched")
        )
        object.__setattr__(
            self, "counter", _require_int(self.counter, field_name="counter", minimum=0)
        )
        object.__setattr__(
            self, "observed_at", _require_datetime(self.observed_at, field_name="observed_at")
        )

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "target_entity_id": self.target_entity_id,
            "desired_latched": self.desired_latched,
            "actual_latched": self.actual_latched,
            "device_id": self.device_id,
            "counter": self.counter,
            "observed_at": _utc_text(self.observed_at) if mode == "json" else self.observed_at,
            "evidence_observation_id": self.evidence_observation_id,
        }


class TwoButtonAction(StrEnum):
    EXECUTE = "EXECUTE"
    REANCHOR_EXECUTE = "REANCHOR_EXECUTE"
    HOLD_AMBIGUOUS = "HOLD_AMBIGUOUS"
    HOLD_REFERENCE_MISMATCH = "HOLD_REFERENCE_MISMATCH"
    HOLD_CONTEXT_MISMATCH = "HOLD_CONTEXT_MISMATCH"
    RECOGNIZE_EFFECT = "RECOGNIZE_EFFECT"


# Names used in early design notes are kept as aliases so callers do not need
# to duplicate enum conversion logic while the service layer is integrated.
LocalTwoButtonAction = TwoButtonAction


@dataclasses.dataclass(frozen=True, slots=True)
class LocalTwoButtonDecision:
    """Pure Robot decision, including the reason for every hold."""

    operation_id: UUID
    intent_revision: int
    semantic_effect_id: str
    reference_observation_id: str
    current_observation_id: str
    action: TwoButtonAction
    reason: str
    selected_detection_id: str | None = None
    displacement_m: float | None = None
    budget_state: str = "NOT_ADMITTED"
    command_digest: str | None = None
    level_evidence_observation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation_id", _require_uuid(self.operation_id, field_name="operation_id")
        )
        if self.intent_revision != 1:
            raise M3aTypeError("LocalTwoButtonDecision supports intent_revision 1 only")
        for field_name in (
            "semantic_effect_id",
            "reference_observation_id",
            "current_observation_id",
            "reason",
            "budget_state",
        ):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name=field_name)
            )
        if not isinstance(self.action, TwoButtonAction):
            try:
                object.__setattr__(self, "action", TwoButtonAction(self.action))
            except (TypeError, ValueError) as error:
                raise M3aTypeError("action is not a supported M3a two-button action") from error
        if self.selected_detection_id is not None:
            object.__setattr__(
                self,
                "selected_detection_id",
                _require_text(self.selected_detection_id, field_name="selected_detection_id"),
            )
        if self.displacement_m is not None:
            object.__setattr__(
                self,
                "displacement_m",
                _require_finite(self.displacement_m, field_name="displacement_m"),
            )
        if self.command_digest is not None:
            object.__setattr__(
                self,
                "command_digest",
                _require_text(self.command_digest, field_name="command_digest"),
            )
        if self.level_evidence_observation_id is not None:
            object.__setattr__(
                self,
                "level_evidence_observation_id",
                _require_text(
                    self.level_evidence_observation_id, field_name="level_evidence_observation_id"
                ),
            )

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        result: dict[str, Any] = {
            "operation_id": self.operation_id,
            "intent_revision": self.intent_revision,
            "semantic_effect_id": self.semantic_effect_id,
            "reference_observation_id": self.reference_observation_id,
            "current_observation_id": self.current_observation_id,
            "action": self.action.value,
            "reason": self.reason,
            "selected_detection_id": self.selected_detection_id,
            "displacement_m": self.displacement_m,
            "budget_state": self.budget_state,
            "command_digest": self.command_digest,
            "level_evidence_observation_id": self.level_evidence_observation_id,
        }
        if mode == "json":
            result["operation_id"] = str(self.operation_id)
        return result


__all__ = [
    "EntityDetection",
    "LocalTwoButtonAction",
    "LocalTwoButtonDecision",
    "M3aEnsureLatchedIntent",
    "M3aSpatialExecutionContext",
    "M3aTypeError",
    "SpatialBindingReceipt",
    "SpatialPressCommand",
    "TwoButtonAction",
    "TwoButtonLevelEvidence",
    "TwoButtonObservation",
    "canonical_bytes",
    "canonical_digest",
]
