"""Independent spatial two-button device fixture for M3a.

The fixture owns the simulated collision truth and an append-only, fsynced
device journal.  Services only see :class:`TwoButtonObservation` and level
evidence; the policy never imports this module and therefore cannot inspect
the hidden positions, collision radius, or counters.
"""

from __future__ import annotations

import base64
import json
import math
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from deferred_teleop.external_effect import ExternalEffectObservation, ExternalOutcome
from deferred_teleop.m3a_types import (
    EntityDetection,
    SpatialBindingReceipt,
    SpatialPressCommand,
    TwoButtonLevelEvidence,
    TwoButtonObservation,
    canonical_bytes,
    canonical_digest,
)
from deferred_teleop.protocol import Pose, Quaternion, SpatialFrame, Vector3

BUTTON_A = "A"
BUTTON_B = "B"
BUTTON_IDS = (BUTTON_A, BUTTON_B)


class SpatialFixtureError(RuntimeError):
    """Base error for malformed or inconsistent device-journal state."""


class SpatialBindingConflictError(SpatialFixtureError, ValueError):
    """An effect key was rebound to a different immutable command."""


class FixtureScenario(StrEnum):
    """Scenario setup is fixture-owned and never imported by the policy."""

    S0_NOMINAL = "S0_NOMINAL"
    S1_BOUNDARY = "S1_BOUNDARY"
    S1_EPSILON = "S1_EPSILON"
    S2_SWAP = "S2_SWAP"
    S4_ALREADY_LATCHED = "S4_ALREADY_LATCHED"


TwoButtonScenario = FixtureScenario


class ButtonContact(StrEnum):
    A = BUTTON_A
    B = BUTTON_B
    NONE = "NONE"


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SpatialFixtureError("fixture timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise SpatialFixtureError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SpatialFixtureError(f"{field_name} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SpatialFixtureError(f"{field_name} must be timezone-aware")
    return parsed


def _text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpatialFixtureError(f"{field_name} must be non-empty")
    return value


def _point(value: object, *, field_name: str) -> tuple[float, float, float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise SpatialFixtureError(f"{field_name} must contain three finite numbers")
    if len(value) != 3:
        raise SpatialFixtureError(f"{field_name} must contain three finite numbers")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise SpatialFixtureError(f"{field_name} must contain three finite numbers")
    return result


def _distance(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second, strict=True)))


def _command_from_journal(record: Mapping[str, Any]) -> SpatialPressCommand:
    command_json = record.get("command_bytes")
    if not isinstance(command_json, str):
        raise SpatialFixtureError("journal command_bytes must be a string")
    try:
        payload = json.loads(command_json)
    except json.JSONDecodeError as error:
        raise SpatialFixtureError("journal command_bytes is invalid JSON") from error
    if not isinstance(payload, dict):
        raise SpatialFixtureError("journal command_bytes must encode an object")
    try:
        command = SpatialPressCommand(
            command_id=payload["command_id"],
            effect_key=payload["effect_key"],
            position_m=payload["position_m"],
            frame_id=payload["frame_id"],
            calibration_version=payload["calibration_version"],
            source_observation_id=payload["source_observation_id"],
            source_detection_id=payload["source_detection_id"],
            command_digest=record["command_digest"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SpatialFixtureError("journal command is malformed") from error
    if command.canonical_bytes().decode("utf-8") != command_json:
        raise SpatialFixtureError("journal command_bytes are not canonical")
    return command


class _FixtureClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SpatialPressResult:
    """Result of one physical fixture command, retained outside the journal."""

    __slots__ = (
        "press_id",
        "effect_key",
        "device_id",
        "contact",
        "a_counter",
        "b_counter",
        "a_latched",
        "b_latched",
        "pressed_at",
        "command_digest",
    )

    def __init__(
        self,
        *,
        press_id: str,
        effect_key: str,
        device_id: str,
        contact: ButtonContact,
        a_counter: int,
        b_counter: int,
        a_latched: bool,
        b_latched: bool,
        pressed_at: datetime,
        command_digest: str,
    ) -> None:
        self.press_id = press_id
        self.effect_key = effect_key
        self.device_id = device_id
        self.contact = contact
        self.a_counter = a_counter
        self.b_counter = b_counter
        self.a_latched = a_latched
        self.b_latched = b_latched
        self.pressed_at = pressed_at
        self.command_digest = command_digest

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "press_id": self.press_id,
            "effect_key": self.effect_key,
            "device_id": self.device_id,
            "contact": self.contact.value,
            "a_counter": self.a_counter,
            "b_counter": self.b_counter,
            "a_latched": self.a_latched,
            "b_latched": self.b_latched,
            "pressed_at": _utc_text(self.pressed_at) if mode == "json" else self.pressed_at,
            "command_digest": self.command_digest,
        }


class TwoButtonFixture:
    """Hidden-position fixture with a durable command/binding journal.

    ``press_at`` is the only method that resolves a point against collision
    truth.  The public observation methods expose measured detections but do
    not expose the private position mapping.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        scenario: FixtureScenario | str = FixtureScenario.S0_NOMINAL,
        device_id: str = "two-button-device-1",
        source_id: str = "two-button-observer-1",
        frame_id: str = "field-world",
        calibration_version: str = "two-button-cal-1",
        max_displacement_m: float = 0.05,
        collision_radius_m: float = 0.025,
        clock: object | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.device_id = _text(device_id, field_name="device_id")
        self.source_id = _text(source_id, field_name="source_id")
        self.frame_id = _text(frame_id, field_name="frame_id")
        self.calibration_version = _text(
            calibration_version,
            field_name="calibration_version",
        )
        try:
            self.scenario = FixtureScenario(scenario)
        except (TypeError, ValueError) as error:
            raise ValueError("unknown two-button fixture scenario") from error
        self.max_displacement_m = float(max_displacement_m)
        self.collision_radius_m = float(collision_radius_m)
        if not math.isfinite(self.max_displacement_m) or self.max_displacement_m < 0:
            raise ValueError("max_displacement_m must be finite and >= 0")
        if not math.isfinite(self.collision_radius_m) or self.collision_radius_m <= 0:
            raise ValueError("collision_radius_m must be finite and > 0")
        self._clock = clock or _FixtureClock()
        self._reference_positions, self._current_positions = self._scenario_positions()
        self._read_journal()  # fail loudly on malformed or corrupted evidence
        if self.scenario is FixtureScenario.S4_ALREADY_LATCHED and not self.press_records:
            self._seed_prior_impulse()

    @classmethod
    def for_scenario(
        cls,
        path: str | Path,
        scenario: FixtureScenario | str,
        **kwargs: Any,
    ) -> TwoButtonFixture:
        return cls(path, scenario=scenario, **kwargs)

    @property
    def journal_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._read_journal())

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        """Return physical press records, excluding immutable bind rows."""

        return tuple(record for record in self._read_journal() if record["record_type"] == "press")

    @property
    def press_records(self) -> tuple[dict[str, Any], ...]:
        return self.records

    @property
    def binding_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            record for record in self._read_journal() if record["record_type"] == "binding"
        )

    @property
    def press_count(self) -> int:
        return len(self.records)

    @property
    def a_counter(self) -> int:
        return int(self._state()[BUTTON_A]["counter"])

    @property
    def b_counter(self) -> int:
        return int(self._state()[BUTTON_B]["counter"])

    @property
    def a_latched(self) -> bool:
        return bool(self._state()[BUTTON_A]["latched"])

    @property
    def b_latched(self) -> bool:
        return bool(self._state()[BUTTON_B]["latched"])

    def reference_observation(
        self,
        *,
        observation_id: str | None = None,
        observed_at: datetime | None = None,
        produced_at: datetime | None = None,
        world_revision: int = 1,
        source_id: str | None = None,
        target_entity_id: str = BUTTON_A,
    ) -> TwoButtonObservation:
        """Emit the persisted authoring observation for one named button."""

        return self._observation(
            stage="reference",
            observation_id=observation_id,
            observed_at=observed_at,
            produced_at=produced_at,
            world_revision=world_revision,
            source_id=source_id,
            target_entity_id=target_entity_id,
        )

    def current_observation(
        self,
        *,
        observation_id: str | None = None,
        observed_at: datetime | None = None,
        produced_at: datetime | None = None,
        world_revision: int = 2,
        source_id: str | None = None,
        target_entity_id: str = BUTTON_A,
    ) -> TwoButtonObservation:
        """Emit the separately scheduled current observation for one button."""

        return self._observation(
            stage="current",
            observation_id=observation_id,
            observed_at=observed_at,
            produced_at=produced_at,
            world_revision=world_revision,
            source_id=source_id,
            target_entity_id=target_entity_id,
        )

    def observe(self, stage: str = "current", **kwargs: Any) -> TwoButtonObservation:
        if stage == "reference":
            return self.reference_observation(**kwargs)
        if stage == "current":
            return self.current_observation(**kwargs)
        raise ValueError("observation stage must be reference or current")

    def level_evidence(
        self,
        target_entity_id: str,
        *,
        observed_at: datetime | None = None,
        evidence_observation_id: str | None = None,
    ) -> TwoButtonLevelEvidence:
        target = _text(target_entity_id, field_name="target_entity_id")
        if target not in BUTTON_IDS:
            raise ValueError(f"unknown two-button target {target}")
        state = self._state()[target]
        now = observed_at or self._now()
        return TwoButtonLevelEvidence(
            target_entity_id=target,
            desired_latched=True,
            actual_latched=bool(state["latched"]),
            device_id=self.device_id,
            counter=int(state["counter"]),
            observed_at=now,
            evidence_observation_id=evidence_observation_id
            or f"level:{target}:{state['counter']}:{int(bool(state['latched']))}",
        )

    def level(self, target_entity_id: str, **kwargs: Any) -> TwoButtonLevelEvidence:
        return self.level_evidence(target_entity_id, **kwargs)

    def bind_command(
        self,
        effect_key: str,
        command: SpatialPressCommand,
    ) -> SpatialBindingReceipt:
        """Persist an immutable effect-key to command binding before dispatch."""

        effect = _text(effect_key, field_name="effect_key")
        if not isinstance(command, SpatialPressCommand):
            raise TypeError("command must be SpatialPressCommand")
        if command.effect_key != effect:
            raise SpatialBindingConflictError("binding effect_key must equal command effect_key")
        self._assert_command_integrity(command)
        existing = self._bindings().get(effect)
        if existing is not None:
            if existing.command_digest != command.command_digest:
                raise SpatialBindingConflictError(
                    f"effect_key {effect} is already bound to a different command"
                )
            if existing.canonical_bytes() != command.canonical_bytes():
                raise SpatialBindingConflictError(
                    f"effect_key {effect} is already bound to different command bytes"
                )
            return SpatialBindingReceipt(
                device_id=self.device_id,
                effect_key=effect,
                command_digest=existing.command_digest,
            )
        record = {
            "record_type": "binding",
            "binding_id": str(uuid5(NAMESPACE_URL, f"dtt-m3a-binding:{self.device_id}:{effect}")),
            "effect_key": effect,
            "device_id": self.device_id,
            "command_bytes": command.canonical_bytes().decode("utf-8"),
            "command_bytes_b64": base64.b64encode(command.canonical_bytes()).decode("ascii"),
            "command_digest": command.command_digest,
            "bound_at": _utc_text(self._now()),
        }
        self._append_record(record)
        return SpatialBindingReceipt(
            device_id=self.device_id,
            effect_key=effect,
            command_digest=command.command_digest,
        )

    def bind(self, effect_key: str, command: SpatialPressCommand) -> SpatialBindingReceipt:
        return self.bind_command(effect_key, command)

    def bound_command(self, effect_key: str) -> SpatialPressCommand:
        effect = _text(effect_key, field_name="effect_key")
        binding = self._bindings().get(effect)
        if binding is None:
            raise SpatialFixtureError(f"no immutable command binding for {effect}")
        return binding

    def press_at(self, command: SpatialPressCommand) -> SpatialPressResult:
        """Resolve and persist one physical press at the supplied point."""

        if not isinstance(command, SpatialPressCommand):
            raise TypeError("press_at accepts SpatialPressCommand only")
        self._assert_command_integrity(command)
        binding = self._bindings().get(command.effect_key)
        if binding is None:
            raise SpatialFixtureError(
                f"effect_key {command.effect_key} has no persisted device binding"
            )
        if (
            binding.command_digest != command.command_digest
            or binding.canonical_bytes() != command.canonical_bytes()
        ):
            raise SpatialBindingConflictError(
                f"effect_key {command.effect_key} is bound to different command bytes"
            )
        return self._press_at_internal(command)

    def _press_at_internal(self, command: SpatialPressCommand) -> SpatialPressResult:
        """Apply a command for fixture-owned setup before normal binding."""

        contact = self._resolve_contact(
            command.position_m,
            frame_id=command.frame_id,
            calibration_version=command.calibration_version,
        )
        state = self._state()
        if contact in {ButtonContact.A, ButtonContact.B}:
            state[contact.value]["counter"] += 1
            state[contact.value]["latched"] = True
        pressed_at = self._now()
        press_id = str(uuid4())
        command_bytes = command.canonical_bytes()
        record = {
            "record_type": "press",
            "press_id": press_id,
            "effect_key": command.effect_key,
            "device_id": self.device_id,
            "command_bytes": command_bytes.decode("utf-8"),
            "command_bytes_b64": base64.b64encode(command_bytes).decode("ascii"),
            "command_digest": command.command_digest,
            "position_m": list(command.position_m),
            "frame_id": command.frame_id,
            "calibration_version": command.calibration_version,
            "contact": contact.value,
            "a_counter": state[BUTTON_A]["counter"],
            "b_counter": state[BUTTON_B]["counter"],
            "a_latched": state[BUTTON_A]["latched"],
            "b_latched": state[BUTTON_B]["latched"],
            "pressed_at": _utc_text(pressed_at),
        }
        self._append_record(record)
        return SpatialPressResult(
            press_id=press_id,
            effect_key=command.effect_key,
            device_id=self.device_id,
            contact=contact,
            a_counter=state[BUTTON_A]["counter"],
            b_counter=state[BUTTON_B]["counter"],
            a_latched=state[BUTTON_A]["latched"],
            b_latched=state[BUTTON_B]["latched"],
            pressed_at=pressed_at,
            command_digest=command.command_digest,
        )

    def device_state(self) -> dict[str, dict[str, int | bool]]:
        return self._state()

    def _observation(
        self,
        *,
        stage: str,
        observation_id: str | None,
        observed_at: datetime | None,
        produced_at: datetime | None,
        world_revision: int,
        source_id: str | None,
        target_entity_id: str,
    ) -> TwoButtonObservation:
        now = observed_at or self._now()
        produced = produced_at or now
        source = source_id or self.source_id
        target = _text(target_entity_id, field_name="target_entity_id")
        if target not in BUTTON_IDS:
            raise ValueError(f"unknown two-button target {target}")
        if stage == "reference":
            positions = self._reference_positions
            default_id = f"{self.scenario.value.lower()}:reference"
            candidates = {target: (target,)}
        else:
            positions = self._current_positions
            default_id = f"{self.scenario.value.lower()}:current"
            candidates = (
                {target: (BUTTON_A, BUTTON_B)}
                if self.scenario is FixtureScenario.S2_SWAP
                else {target: (target,)}
            )
        detections = tuple(
            EntityDetection(
                detection_id=f"detection-{entity.lower()}-{stage}",
                candidate_entity_ids=candidates[entity],
                pose=self._pose(positions[entity]),
                visibility=True,
                source_evidence_id=f"{source}:evidence:{stage}:{world_revision}",
            )
            for entity in (target,)
        )
        return TwoButtonObservation(
            observation_id=observation_id or default_id,
            source_id=source,
            world_revision=world_revision,
            observed_at=now,
            produced_at=produced,
            frame_id=self.frame_id,
            calibration_version=self.calibration_version,
            detections=detections,
        )

    def _pose(self, position: tuple[float, float, float]) -> Pose:
        return Pose(
            position=Vector3(x=position[0], y=position[1], z=position[2]),
            orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
            frame=SpatialFrame(
                frame_id=self.frame_id,
                calibration_version=self.calibration_version,
            ),
        )

    def _scenario_positions(
        self,
    ) -> tuple[dict[str, tuple[float, float, float]], dict[str, tuple[float, float, float]]]:
        reference_a = (0.4, 0.1, 0.2)
        reference_b = (0.6, 0.1, 0.2)
        reference = {BUTTON_A: reference_a, BUTTON_B: reference_b}
        current = dict(reference)
        if self.scenario is FixtureScenario.S1_BOUNDARY:
            current[BUTTON_A] = (
                reference_a[0] + self.max_displacement_m,
                reference_a[1],
                reference_a[2],
            )
        elif self.scenario is FixtureScenario.S1_EPSILON:
            current[BUTTON_A] = (
                reference_a[0] + self.max_displacement_m + 1e-6,
                reference_a[1],
                reference_a[2],
            )
        elif self.scenario is FixtureScenario.S2_SWAP:
            current[BUTTON_A] = (reference_a[0] + 0.2, reference_a[1], reference_a[2])
            current[BUTTON_B] = reference_a
        return reference, current

    def _resolve_contact(
        self,
        position: tuple[float, float, float],
        *,
        frame_id: str,
        calibration_version: str,
    ) -> ButtonContact:
        if frame_id != self.frame_id or calibration_version != self.calibration_version:
            return ButtonContact.NONE
        contacts = tuple(
            entity
            for entity, hidden_position in self._current_positions.items()
            if _distance(position, hidden_position) <= self.collision_radius_m
        )
        return ButtonContact(contacts[0]) if len(contacts) == 1 else ButtonContact.NONE

    def _assert_command_integrity(self, command: SpatialPressCommand) -> None:
        if command.command_digest != canonical_digest(command._payload_without_digest()):
            raise SpatialFixtureError("command digest does not match canonical command bytes")
        if command.effect_key.strip() == "":
            raise SpatialFixtureError("command effect_key must not be empty")

    def _state(self) -> dict[str, dict[str, int | bool]]:
        state: dict[str, dict[str, int | bool]] = {
            BUTTON_A: {"counter": 0, "latched": False},
            BUTTON_B: {"counter": 0, "latched": False},
        }
        for record in self.records:
            contact = record["contact"]
            if contact in BUTTON_IDS:
                state[contact]["counter"] = int(state[contact]["counter"]) + 1
                state[contact]["latched"] = True
        return state

    def _seed_prior_impulse(self) -> None:
        command = SpatialPressCommand.from_pose(
            command_id="seed-prior-a",
            effect_key="unrelated:prior:a",
            pose=self._pose(self._current_positions[BUTTON_A]),
            source_observation_id="unrelated-prior-observation",
            source_detection_id="unrelated-prior-detection",
        )
        self._press_at_internal(command)

    def _bindings(self) -> dict[str, SpatialPressCommand]:
        result: dict[str, SpatialPressCommand] = {}
        for record in self.binding_records:
            command = _command_from_journal(record)
            effect = _text(record.get("effect_key"), field_name="effect_key")
            if record.get("device_id") != self.device_id:
                raise SpatialFixtureError("binding device identity differs from fixture")
            if command.effect_key != effect:
                raise SpatialFixtureError("binding effect_key differs from command")
            prior = result.get(effect)
            if prior is not None and prior.canonical_bytes() != command.canonical_bytes():
                raise SpatialBindingConflictError(
                    f"journal contains conflicting binding for {effect}"
                )
            result[effect] = command
        return result

    def _read_journal(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        seen_press_ids: set[str] = set()
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise SpatialFixtureError(
                        f"invalid device journal JSON at line {line_number}"
                    ) from error
                if not isinstance(record, dict):
                    raise SpatialFixtureError(f"device journal row {line_number} is not an object")
                record_type = record.get("record_type")
                if record_type not in {"binding", "press"}:
                    raise SpatialFixtureError(
                        f"unknown device journal record at line {line_number}"
                    )
                if record.get("device_id") != self.device_id:
                    raise SpatialFixtureError(
                        f"device journal device mismatch at line {line_number}"
                    )
                if record_type == "binding":
                    self._validate_binding_record(record, line_number=line_number)
                else:
                    self._validate_press_record(record, line_number=line_number)
                    press_id = record["press_id"]
                    if press_id in seen_press_ids:
                        raise SpatialFixtureError(f"duplicate press_id at line {line_number}")
                    seen_press_ids.add(press_id)
                records.append(record)
        return records

    def _validate_binding_record(self, record: Mapping[str, Any], *, line_number: int) -> None:
        for field_name in (
            "binding_id",
            "effect_key",
            "device_id",
            "command_bytes",
            "command_digest",
            "bound_at",
        ):
            if field_name not in record:
                raise SpatialFixtureError(f"binding row {line_number} is incomplete")
        _text(record["binding_id"], field_name="binding_id")
        _text(record["effect_key"], field_name="effect_key")
        _parse_datetime(record["bound_at"], field_name="bound_at")
        command = _command_from_journal(record)
        if command.effect_key != record["effect_key"]:
            raise SpatialFixtureError(f"binding row {line_number} effect mismatch")

    def _validate_press_record(self, record: Mapping[str, Any], *, line_number: int) -> None:
        required = {
            "press_id",
            "effect_key",
            "device_id",
            "command_bytes",
            "command_digest",
            "position_m",
            "frame_id",
            "calibration_version",
            "contact",
            "a_counter",
            "b_counter",
            "a_latched",
            "b_latched",
            "pressed_at",
        }
        if not required.issubset(record):
            raise SpatialFixtureError(f"press row {line_number} is incomplete")
        for field_name in (
            "press_id",
            "effect_key",
            "device_id",
            "frame_id",
            "calibration_version",
        ):
            _text(record[field_name], field_name=field_name)
        if record["contact"] not in {BUTTON_A, BUTTON_B, ButtonContact.NONE.value}:
            raise SpatialFixtureError(f"press row {line_number} has invalid contact")
        for field_name in ("a_counter", "b_counter"):
            if (
                isinstance(record[field_name], bool)
                or not isinstance(record[field_name], int)
                or record[field_name] < 0
            ):
                raise SpatialFixtureError(f"press row {line_number} has invalid counter")
        for field_name in ("a_latched", "b_latched"):
            if type(record[field_name]) is not bool:
                raise SpatialFixtureError(f"press row {line_number} has invalid latch")
        _parse_datetime(record["pressed_at"], field_name="pressed_at")
        command = _command_from_journal(record)
        if command.effect_key != record["effect_key"]:
            raise SpatialFixtureError(f"press row {line_number} effect mismatch")
        if list(command.position_m) != record["position_m"]:
            raise SpatialFixtureError(f"press row {line_number} position mismatch")
        if (
            command.frame_id != record["frame_id"]
            or command.calibration_version != record["calibration_version"]
        ):
            raise SpatialFixtureError(f"press row {line_number} context mismatch")

    def _append_record(self, record: Mapping[str, Any]) -> None:
        encoded = canonical_bytes(record).decode("utf-8")
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if directory_flag is None:
            return
        try:
            directory_fd = os.open(self.path.parent, os.O_RDONLY | directory_flag)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _now(self) -> datetime:
        now = getattr(self._clock, "now", None)
        if not callable(now):
            raise ValueError("fixture clock must expose callable now()")
        value = now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fixture clock must return timezone-aware datetime")
        return value


class SpatialExternalEffectAdapter:
    """External adapter that dispatches only a previously bound command."""

    def __init__(self, fixture: TwoButtonFixture) -> None:
        if not isinstance(fixture, TwoButtonFixture):
            raise TypeError("fixture must be TwoButtonFixture")
        self.fixture = fixture
        self.device_id = fixture.device_id

    @property
    def press_count(self) -> int:
        return self.fixture.press_count

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return self.fixture.records

    def bind(
        self,
        effect_key: str,
        command: SpatialPressCommand,
    ) -> SpatialBindingReceipt:
        receipt = self.fixture.bind_command(effect_key, command)
        if receipt.device_id != self.device_id or receipt.effect_key != effect_key:
            raise SpatialFixtureError("device binding receipt is inconsistent")
        if receipt.command_digest != command.command_digest:
            raise SpatialBindingConflictError("device binding receipt digest differs from command")
        return receipt

    def binding(self, effect_key: str) -> SpatialBindingReceipt:
        command = self.fixture.bound_command(effect_key)
        return SpatialBindingReceipt(
            device_id=self.device_id,
            effect_key=effect_key,
            command_digest=command.command_digest,
        )

    def level_evidence(
        self,
        target_entity_id: str,
        *,
        observed_at: datetime | None = None,
        evidence_observation_id: str | None = None,
    ) -> TwoButtonLevelEvidence:
        """Read the independent level sensor without exposing fixture truth."""

        return self.fixture.level_evidence(
            target_entity_id,
            observed_at=observed_at,
            evidence_observation_id=evidence_observation_id,
        )

    def level(self, target_entity_id: str, **kwargs: Any) -> TwoButtonLevelEvidence:
        return self.level_evidence(target_entity_id, **kwargs)

    def press(self, effect_key: str) -> ExternalEffectObservation:
        """Issue one bound command; there is no target-name or point argument."""

        command = self.fixture.bound_command(effect_key)
        result = self.fixture.press_at(command)
        outcome = (
            ExternalOutcome.APPLIED
            if result.contact is not ButtonContact.NONE
            else ExternalOutcome.NOT_APPLIED
        )
        return ExternalEffectObservation(
            effect_key=effect_key,
            device_id=self.device_id,
            outcome=outcome,
            observed_at=result.pressed_at,
            observation_id=f"spatial:{result.press_id}",
            details={
                "fixture": "m3a-two-button-v1",
                "command_digest": result.command_digest,
                "contact": result.contact.value,
                "a_counter": result.a_counter,
                "b_counter": result.b_counter,
                "a_latched": result.a_latched,
                "b_latched": result.b_latched,
            },
        )

    def observe(self, effect_key: str) -> ExternalEffectObservation:
        _text(effect_key, field_name="effect_key")
        matching = [record for record in self.fixture.records if record["effect_key"] == effect_key]
        if not matching:
            try:
                bound_digest = self.fixture.bound_command(effect_key).command_digest
            except SpatialFixtureError:
                bound_digest = None
            details: dict[str, Any] = {"fixture": "m3a-two-button-v1", "press_count_for_effect": 0}
            if bound_digest is not None:
                details["command_digest"] = bound_digest
            return ExternalEffectObservation(
                effect_key=effect_key,
                device_id=self.device_id,
                outcome=ExternalOutcome.NOT_APPLIED,
                observed_at=self.fixture._now(),
                observation_id=f"spatial:none:{effect_key}",
                details=details,
            )
        record = matching[-1]
        outcome = (
            ExternalOutcome.APPLIED
            if record["contact"] != ButtonContact.NONE.value
            else ExternalOutcome.NOT_APPLIED
        )
        return ExternalEffectObservation(
            effect_key=effect_key,
            device_id=self.device_id,
            outcome=outcome,
            observed_at=_parse_datetime(record["pressed_at"], field_name="pressed_at"),
            observation_id=f"spatial:{record['press_id']}",
            details={
                "fixture": "m3a-two-button-v1",
                "command_digest": record["command_digest"],
                "contact": record["contact"],
                "a_counter": record["a_counter"],
                "b_counter": record["b_counter"],
                "a_latched": record["a_latched"],
                "b_latched": record["b_latched"],
                "press_count_for_effect": len(matching),
            },
        )


PersistentSpatialTwoButtonFixture = TwoButtonFixture


__all__ = [
    "BUTTON_A",
    "BUTTON_B",
    "BUTTON_IDS",
    "ButtonContact",
    "FixtureScenario",
    "PersistentSpatialTwoButtonFixture",
    "SpatialBindingConflictError",
    "SpatialExternalEffectAdapter",
    "SpatialFixtureError",
    "SpatialPressResult",
    "TwoButtonFixture",
    "TwoButtonScenario",
]
