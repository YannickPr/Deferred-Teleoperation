"""Explicit boundary for effects whose durable commit is external to Robot.

The M1.8a fixture is intentionally small.  Its append-only JSON-lines journal is
owned by the fixture, not by :class:`~deferred_teleop.storage.NodeStore`, so a
test can close and reopen the two stores independently.  Calling ``press`` is
therefore never idempotent: every call records one impulse.  Idempotency is a
property of the Robot recovery algorithm, which must observe after a dispatch
has been recorded instead of calling ``press`` again.

The types in this module are local Python test/runtime types.  They are not
protocol messages and do not change the ``dtt/0`` wire schema.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4


class ExternalEffectError(RuntimeError):
    """Base class for an external-effect adapter failure."""


class InvalidExternalProofError(ExternalEffectError, ValueError):
    """An adapter returned an observation without attributable evidence."""


class ExternalOutcome(StrEnum):
    """What an adapter can establish about one addressed effect."""

    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidExternalProofError(f"{field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidExternalProofError(f"{field_name} is not a valid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidExternalProofError(f"{field_name} must be timezone-aware")
    return parsed


@dataclass(frozen=True)
class ExternalEffectObservation:
    """Attributable proof returned by an external adapter observation.

    ``effect_key`` and ``device_id`` are mandatory by design.  A bare boolean,
    a count, or an unaddressed status cannot close an execution contract.  The
    optional ``details`` object is retained as forensic adapter metadata and is
    never used as the source of truth for whether an effect was pressed.
    """

    effect_key: str
    device_id: str
    outcome: ExternalOutcome
    observed_at: datetime
    observation_id: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.effect_key, str) or not self.effect_key.strip():
            raise ValueError("effect_key must not be empty")
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise ValueError("device_id must not be empty")
        if not isinstance(self.observation_id, str) or not self.observation_id.strip():
            raise ValueError("observation_id must not be empty")
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("observed_at must be timezone-aware")
        if not isinstance(self.outcome, ExternalOutcome):
            try:
                object.__setattr__(self, "outcome", ExternalOutcome(self.outcome))
            except (TypeError, ValueError) as error:
                raise ValueError("outcome must be APPLIED, NOT_APPLIED, or UNKNOWN") from error
        if not isinstance(self.details, Mapping):
            raise ValueError("details must be a mapping")

    def model_dump(self) -> dict[str, Any]:
        """Return a JSON-compatible proof object for storage and audit logs."""

        return {
            "effect_key": self.effect_key,
            "device_id": self.device_id,
            "outcome": self.outcome.value,
            "observed_at": _utc_text(self.observed_at),
            "observation_id": self.observation_id,
            "details": dict(self.details),
        }


class ExternalEffectAdapter(Protocol):
    """The injected capability needed by ``DummyRobotService``.

    Implementations must persist the external action before returning from
    ``press`` when they can.  A crash after the action and before the Robot
    terminal commit is expected; recovery will call ``observe`` using the same
    effect key and must not call ``press`` again.
    """

    device_id: str

    def press(self, effect_key: str) -> ExternalEffectObservation | None:
        """Issue one external impulse for ``effect_key``."""

    def observe(self, effect_key: str) -> ExternalEffectObservation:
        """Return attributable evidence for ``effect_key``."""


def coerce_observation(
    value: ExternalEffectObservation | Mapping[str, Any] | object,
    *,
    expected_effect_key: str,
    expected_device_id: str | None = None,
) -> ExternalEffectObservation:
    """Validate and normalize adapter output at the storage boundary.

    In particular, ``True``/``False`` and an unaddressed ``{"outcome": ...}``
    mapping are rejected.  Accepting mappings keeps tiny test adapters easy to
    write while requiring the same proof fields as the typed dataclass.
    """

    if isinstance(value, ExternalEffectObservation):
        observation = value
    elif isinstance(value, Mapping):
        required = {"effect_key", "device_id", "outcome", "observed_at", "observation_id"}
        missing = required.difference(value)
        if missing:
            raise InvalidExternalProofError(
                "external observation is missing attributable fields: "
                + ", ".join(sorted(missing))
            )
        try:
            for field_name in ("effect_key", "device_id", "observation_id"):
                field_value = value[field_name]
                if not isinstance(field_value, str) or not field_value.strip():
                    raise InvalidExternalProofError(
                        f"external observation {field_name} must be a non-empty string"
                    )
            outcome = ExternalOutcome(value["outcome"])
            observed_at = value["observed_at"]
            if not isinstance(observed_at, datetime):
                observed_at = _parse_datetime(observed_at, field_name="observed_at")
            observation = ExternalEffectObservation(
                effect_key=value["effect_key"],
                device_id=value["device_id"],
                outcome=outcome,
                observed_at=observed_at,
                observation_id=value["observation_id"],
                details=value.get("details", {}),
            )
        except InvalidExternalProofError:
            raise
        except (TypeError, ValueError) as error:
            raise InvalidExternalProofError("external observation has invalid fields") from error
    else:
        raise InvalidExternalProofError(
            "external adapter must return attributable observation proof, not a boolean"
        )

    if observation.effect_key != expected_effect_key:
        raise InvalidExternalProofError(
            "external observation effect_key does not match dispatched effect"
        )
    if expected_device_id is not None and observation.device_id != expected_device_id:
        raise InvalidExternalProofError(
            "external observation device_id does not match addressed device"
        )
    return observation


class PersistentDummyExternalEffect:
    """A deliberately non-idempotent, file-backed button fixture.

    The fixture is intentionally independent from the Robot SQLite database.
    ``press`` appends a record on every invocation, including repeated keys.  A
    caller can force the observation result with ``observation_outcome`` to
    model a missing or ambiguous physical sensor; the persistent press records
    remain available in ``records`` for forensic assertions.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        device_id: str = "dummy-external-button-1",
        observation_outcome: ExternalOutcome | str | None = None,
        clock: object | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id must not be empty")
        self.device_id = device_id
        self._clock = clock
        self.observation_outcome = (
            ExternalOutcome(observation_outcome) if observation_outcome is not None else None
        )
        # Validate existing rows at construction, but never rewrite them.  A
        # malformed fixture is evidence corruption and must fail loudly.
        self._read_records()

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._read_records())

    @property
    def press_count(self) -> int:
        return len(self.records)

    def set_observation_outcome(self, outcome: ExternalOutcome | str | None) -> None:
        self.observation_outcome = ExternalOutcome(outcome) if outcome is not None else None

    def press(self, effect_key: str) -> ExternalEffectObservation:
        if not effect_key.strip():
            raise ValueError("effect_key must not be empty")
        record = {
            "press_id": str(uuid4()),
            "effect_key": effect_key,
            "device_id": self.device_id,
            "pressed_at": _utc_text(self._now()),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            # fsync is the point of this fixture: a test crash after ``press``
            # must leave an observable record after reopening the file.
            import os

            os.fsync(handle.fileno())
        return self.observe(effect_key)

    def observe(self, effect_key: str) -> ExternalEffectObservation:
        if not effect_key.strip():
            raise ValueError("effect_key must not be empty")
        records = [record for record in self.records if record["effect_key"] == effect_key]
        if self.observation_outcome is not None:
            selected = self.observation_outcome
        elif records:
            selected = ExternalOutcome.APPLIED
        else:
            selected = ExternalOutcome.NOT_APPLIED
        latest_at = (
            _parse_datetime(records[-1]["pressed_at"], field_name="pressed_at")
            if records
            else self._now()
        )
        return ExternalEffectObservation(
            effect_key=effect_key,
            device_id=self.device_id,
            outcome=selected,
            observed_at=self._now(),
            observation_id=str(uuid4()),
            details={
                "fixture": "persistent-dummy-external-effect-v1",
                "press_count_for_effect": len(records),
                "latest_press_at": _utc_text(latest_at) if records else None,
            },
        )

    def _read_records(self) -> list[dict[str, Any]]:
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
                    raise ExternalEffectError(
                        f"invalid external fixture record at line {line_number}"
                    ) from error
                if not isinstance(record, dict):
                    raise ExternalEffectError(
                        f"external fixture record at line {line_number} is not an object"
                    )
                required = {"press_id", "effect_key", "device_id", "pressed_at"}
                if not required.issubset(record):
                    raise ExternalEffectError(
                        f"external fixture record at line {line_number} is incomplete"
                    )
                if any(
                    not isinstance(record[key], str) or not record[key].strip()
                    for key in ("press_id", "effect_key", "device_id")
                ):
                    raise ExternalEffectError(
                        f"external fixture record at line {line_number} has invalid identity"
                    )
                if record["press_id"] in seen_press_ids:
                    raise ExternalEffectError(
                        f"duplicate external fixture press_id at line {line_number}"
                    )
                seen_press_ids.add(record["press_id"])
                if record["device_id"] != self.device_id:
                    raise ExternalEffectError(
                        f"external fixture device mismatch at line {line_number}"
                    )
                _parse_datetime(record["pressed_at"], field_name="pressed_at")
                records.append(record)
        return records

    def _now(self) -> datetime:
        if self._clock is not None:
            now = getattr(self._clock, "now", None)
            if not callable(now):
                raise ValueError("external fixture clock must expose callable now()")
            value = now()
        else:
            value = datetime.now(UTC)
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("external fixture clock must return timezone-aware datetime")
        return value
