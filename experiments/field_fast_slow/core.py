"""Experimental decision seam. No I/O, motor driver, or production safety claim.

All times are seconds on ONE simulated/site-local monotonic clock. Never compare
Mission's monotonic timestamp with Robot's. SI units; this is NOT the dtt/0 wire API.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum


class Action(IntEnum):
    HOLD = 0
    APPROACH = 1
    PRESS = 2
    VERIFY = 3


@dataclass(frozen=True)
class Contract:
    operation: str = "op-1"
    revision: int = 1
    target: str = "A"
    epoch: int = 1
    frame: str = "bench"
    calibration: str = "cal-1"
    reference_x_m: float = 0.0
    max_displacement_m: float = 0.05
    admitted_s: float = 1200.0
    expires_s: float = 1220.0


@dataclass(frozen=True)
class Observation:
    sequence: int
    captured_s: float
    target_x_m: float
    tool_x_m: float
    tool_z_m: float
    target: str = "A"
    epoch: int = 1
    frame: str = "bench"
    calibration: str = "cal-1"
    session: str = "robot-boot-1"
    visible: bool = True
    unique: bool = True
    latched: bool = False  # independent simulated device level, NOT a model prediction
    estop: bool = False
    cancelled: bool = False
    budget_available: bool = True


@dataclass(frozen=True)
class Proposal:
    probabilities: tuple[float, ...]
    source: Observation
    operation: str
    revision: int
    produced_s: float


@dataclass(frozen=True)
class Verdict:
    action: Action
    reason: str


@dataclass(frozen=True)
class Limits:
    observation_ttl_s: float = 0.10
    proposal_ttl_s: float = 0.10
    max_target_drift_m: float = 0.001
    alignment_m: float = 0.003
    hover_m: float = 0.015
    confidence: float = 0.90  # experimental operating point, not a safety guarantee


def features(o: Observation, c: Contract) -> tuple[float, ...]:
    """Compact state encoder; no action label, scenario ID, or hidden contact truth."""
    return (
        (o.target_x_m - o.tool_x_m) / 0.05,
        o.tool_z_m / 0.05,
        abs(o.target_x_m - c.reference_x_m) / c.max_displacement_m,
        float(o.visible), float(o.unique), float(o.latched),
        float(o.estop or o.cancelled), float(o.budget_available),
    )


def rule_action(o: Observation, c: Contract, limits: Limits = Limits()) -> Action:
    """Explicit teacher/baseline for this deliberately simple task, not an LLM."""
    if o.estop or o.cancelled:
        return Action.HOLD
    if o.latched:
        return Action.VERIFY
    if (not o.visible or not o.unique or not o.budget_available
            or abs(o.target_x_m - c.reference_x_m) > c.max_displacement_m):
        return Action.HOLD
    if (abs(o.target_x_m - o.tool_x_m) <= limits.alignment_m
            and o.tool_z_m <= limits.hover_m + 1e-9):
        return Action.PRESS
    return Action.APPROACH


def one_hot(action: Action) -> tuple[float, ...]:
    return tuple(float(index == int(action)) for index in range(len(Action)))


def proposal(o: Observation, c: Contract, probabilities: tuple[float, ...],
             now_s: float) -> Proposal:
    return Proposal(probabilities, o, c.operation, c.revision, now_s)


def _finite_observation(o: Observation) -> bool:
    return all(math.isfinite(v) for v in (
        o.captured_s, o.target_x_m, o.tool_x_m, o.tool_z_m,
    ))


def _guard(p: Proposal, current: Observation, c: Contract, now_s: float,
          limits: Limits = Limits()) -> Verdict:
    """Robot-side fail-closed proposal filter; not a collision/safety controller.

    It never turns HOLD into motion. Rechecks CURRENT evidence rather than trusting
    an old model snapshot. Durable budget consumption remains the runtime's job.
    """
    def hold(reason: str) -> Verdict:
        return Verdict(Action.HOLD, reason)

    numbers = (now_s, p.produced_s, c.admitted_s, c.expires_s,
               c.reference_x_m, c.max_displacement_m,
               limits.observation_ttl_s, limits.proposal_ttl_s,
               limits.max_target_drift_m, limits.alignment_m,
               limits.hover_m, limits.confidence)
    if not all(math.isfinite(v) for v in numbers):
        return hold("NONFINITE")
    if (not _finite_observation(current) or not _finite_observation(p.source)
            or c.max_displacement_m <= 0
            or min(limits.observation_ttl_s, limits.proposal_ttl_s,
                   limits.max_target_drift_m, limits.alignment_m, limits.hover_m) <= 0
            or not 0 <= limits.confidence <= 1):
        return hold("INVALID_INPUT")
    if current.estop or current.cancelled:
        return hold("LOCAL_STOP")
    if not c.admitted_s <= now_s < c.expires_s:
        return hold("CONTRACT_TIME")
    if (p.operation, p.revision) != (c.operation, c.revision):
        return hold("CONTRACT_VERSION")
    expected = (c.target, c.epoch, c.frame, c.calibration)
    for observation in (current, p.source):
        if (observation.target, observation.epoch, observation.frame,
                observation.calibration) != expected:
            return hold("CONTEXT_CHANGED")
        if not 0 <= now_s - observation.captured_s <= limits.observation_ttl_s:
            return hold("STALE_OBSERVATION")
    if (p.source.session != current.session
            or p.source.sequence > current.sequence
            or p.source.captured_s > p.produced_s
            or p.source.captured_s > current.captured_s
            or not 0 <= now_s - p.produced_s <= limits.proposal_ttl_s):
        return hold("STALE_PROPOSAL")
    if abs(current.target_x_m - p.source.target_x_m) > limits.max_target_drift_m:
        return hold("TARGET_MOVED_DURING_INFERENCE")
    ps = p.probabilities
    if (len(ps) != len(Action) or any(not math.isfinite(v) or not 0 <= v <= 1 for v in ps)
            or not math.isclose(sum(ps), 1.0, rel_tol=0, abs_tol=1e-5)):
        return hold("INVALID_PROBABILITIES")
    action = Action(max(range(len(ps)), key=ps.__getitem__))
    if action is Action.HOLD:
        return hold("MODEL_HOLD")
    if max(ps) < limits.confidence:
        return hold("LOW_CONFIDENCE")
    if action is Action.VERIFY:
        return Verdict(action, "INDEPENDENT_LEVEL") if current.latched else hold("NO_LEVEL_PROOF")
    if (not current.visible or not current.unique
            or abs(current.target_x_m - c.reference_x_m) > c.max_displacement_m):
        return hold("TARGET_NOT_AUTHORIZED")
    if current.latched or not current.budget_available:
        return hold("NO_NEW_EFFECT")
    if current.tool_z_m < 0 or not -0.15 <= current.tool_x_m <= 0.15:
        return hold("OUTSIDE_SIM_WORKSPACE")
    if action is Action.PRESS and (
        abs(current.target_x_m - current.tool_x_m) > limits.alignment_m
        or current.tool_z_m > limits.hover_m + 1e-9
    ):
        return hold("PRESS_PRECONDITION")
    return Verdict(action, "ALLOWED_IN_SIMULATION")


def accept_slow_plan(value: dict, c: Contract, current: Observation,
                     request_id: str, now_s: float) -> bool:
    """Minimal exact-match plan-proposal admission; never accepts coordinates/code.

    Production must additionally bind authenticated source, evidence digest,
    assignment, durable budget, cancellation and request provenance in dtt/0.
    """
    expected = {
        "request_id": request_id, "operation": c.operation,
        "revision": c.revision, "target": c.target, "epoch": c.epoch,
        "frame": c.frame, "calibration": c.calibration,
        "skill": "ensure_button_latched",
    }
    return (
        type(value) is dict and value == expected
        and all(type(value[key]) is type(expected[key]) for key in expected)
        and math.isfinite(now_s) and c.admitted_s <= now_s < c.expires_s
        and not current.estop and not current.cancelled
        and (current.target, current.epoch, current.frame, current.calibration)
        == (c.target, c.epoch, c.frame, c.calibration)
    )


def guard(p: Proposal, current: Observation, c: Contract, now_s: float,
          limits: Limits = Limits()) -> Verdict:
    """Fail closed on malformed values; transport validation is still mandatory."""
    try:
        for o in (current, p.source):
            if (type(o.sequence) is not int or o.sequence < 0
                    or type(o.epoch) is not int
                    or any(type(v) is not bool for v in (
                        o.visible, o.unique, o.latched, o.estop,
                        o.cancelled, o.budget_available))):
                return Verdict(Action.HOLD, "INVALID_INPUT")
        return _guard(p, current, c, now_s, limits)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return Verdict(Action.HOLD, "INVALID_INPUT")
