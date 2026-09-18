"""Portable tests: python -m unittest discover -s . -p 'test_*.py' -v."""
from __future__ import annotations

import math
import random
import unittest
from dataclasses import replace

from core import (
    Action, Contract, Limits, Observation, accept_slow_plan, guard, one_hot, proposal,
)


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.c = Contract()
        self.o = Observation(10, 1201, 0, 0, 0.015)

    def check_hold(self, observation=None, contract=None, probabilities=None,
                   source=None, now=1201, limits=Limits(), **changes) -> None:
        o = observation or self.o
        p = proposal(source or self.o, self.c, probabilities or one_hot(Action.PRESS), 1201)
        p = replace(p, **changes)
        self.assertEqual(guard(p, o, contract or self.c, now, limits).action, Action.HOLD)

    def test_nominal_press(self):
        p = proposal(self.o, self.c, one_hot(Action.PRESS), 1201)
        self.assertEqual(guard(p, self.o, self.c, 1201).action, Action.PRESS)

    def test_cancel(self):
        self.check_hold(observation=replace(self.o, cancelled=True))

    def test_estop(self):
        self.check_hold(observation=replace(self.o, estop=True))

    def test_stale_observation(self):
        self.check_hold(now=1201.101, produced_s=1201.101)

    def test_future_observation(self):
        self.check_hold(source=replace(self.o, captured_s=1202))

    def test_inference_timestamp_cannot_refresh_snapshot(self):
        self.check_hold(now=1203, produced_s=1203)

    def test_stale_proposal(self):
        self.check_hold(produced_s=1200.8)

    def test_future_proposal(self):
        self.check_hold(produced_s=1202)

    def test_contract_revision(self):
        self.check_hold(revision=2)

    def test_contract_operation(self):
        self.check_hold(operation="other")

    def test_contract_expiry(self):
        self.check_hold(contract=replace(self.c, expires_s=1201))

    def test_not_admitted(self):
        self.check_hold(contract=replace(self.c, admitted_s=1202))

    def test_other_target(self):
        self.check_hold(observation=replace(self.o, target="B"))

    def test_other_epoch(self):
        self.check_hold(observation=replace(self.o, epoch=2))

    def test_frame(self):
        self.check_hold(observation=replace(self.o, frame="other"))

    def test_calibration(self):
        self.check_hold(observation=replace(self.o, calibration="cal-2"))

    def test_robot_reboot(self):
        self.check_hold(observation=replace(self.o, session="boot-2"))

    def test_future_sequence(self):
        self.check_hold(source=replace(self.o, sequence=11))

    def test_pose_changed_during_inference(self):
        self.check_hold(observation=replace(self.o, target_x_m=0.002))

    def test_ambiguous_target(self):
        self.check_hold(observation=replace(self.o, unique=False))

    def test_occlusion(self):
        self.check_hold(observation=replace(self.o, visible=False))

    def test_displacement_limit(self):
        o = replace(self.o, target_x_m=0.051, tool_x_m=0.051)
        self.check_hold(observation=o, source=o)

    def test_alignment(self):
        self.check_hold(observation=replace(self.o, tool_x_m=0.0031))

    def test_hover(self):
        self.check_hold(observation=replace(self.o, tool_z_m=0.03))

    def test_spent_budget(self):
        self.check_hold(observation=replace(self.o, budget_available=False))

    def test_latched_never_repress(self):
        self.check_hold(observation=replace(self.o, latched=True))

    def test_verify_requires_independent_evidence(self):
        self.check_hold(probabilities=one_hot(Action.VERIFY))

    def test_verify_does_not_consume_new_effect(self):
        o = replace(self.o, latched=True, budget_available=False)
        p = proposal(o, self.c, one_hot(Action.VERIFY), 1201)
        self.assertEqual(guard(p, o, self.c, 1201).action, Action.VERIFY)

    def test_confidence_not_permission(self):
        self.check_hold(observation=replace(self.o, estop=True),
                        probabilities=(0, 0, 1, 0))

    def test_low_confidence(self):
        self.check_hold(probabilities=(0.1, 0.1, 0.7, 0.1))

    def test_bad_probabilities(self):
        for ps in ((0, 1), (0, 0, math.nan, 0), (0, 0, 2, -1),
                   (0, 0, math.inf, 0), (0, 0, 0.8, 0), (0, 0, "1", 0)):
            with self.subTest(ps=ps):
                self.check_hold(probabilities=ps)

    def test_nan_clock_and_pose(self):
        self.check_hold(now=math.nan)
        self.check_hold(observation=replace(self.o, tool_x_m=math.nan))

    def test_zero_limit(self):
        self.check_hold(limits=replace(Limits(), observation_ttl_s=0))

    def test_malformed_boolean(self):
        self.check_hold(observation=replace(self.o, estop="false"))

    def test_random_model_cannot_bypass_known_forbidden_states(self):
        rng = random.Random(42)
        for _ in range(2000):
            values = [rng.random() for _ in Action]
            ps = tuple(v / sum(values) for v in values)
            o = replace(self.o, **rng.choice([
                {"estop": True}, {"cancelled": True}, {"unique": False},
                {"visible": False}, {"budget_available": False},
                {"calibration": "wrong"},
            ]))
            self.check_hold(observation=o, probabilities=ps,
                            limits=replace(Limits(), confidence=0))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.c = Contract()
        self.o = Observation(0, 1201, 0, 0, 0.015)
        self.plan = {"request_id": "r1", "operation": "op-1", "revision": 1,
                     "target": "A", "epoch": 1, "frame": "bench",
                     "calibration": "cal-1", "skill": "ensure_button_latched"}

    def test_accept_exact_plan(self):
        self.assertTrue(accept_slow_plan(self.plan, self.c, self.o, "r1", 1201))

    def test_reject_stale_request(self):
        self.assertFalse(accept_slow_plan(self.plan, self.c, self.o, "r2", 1201))

    def test_no_model_coordinates(self):
        self.assertFalse(accept_slow_plan(dict(self.plan, x=0.02), self.c, self.o, "r1", 1201))

    def test_no_retarget_or_revision(self):
        for key, value in (("target", "B"), ("revision", 2), ("epoch", 2),
                           ("calibration", "cal-2"), ("revision", True)):
            self.assertFalse(accept_slow_plan(dict(self.plan, **{key: value}),
                                             self.c, self.o, "r1", 1201))

    def test_cancellation_overrides_late_plan(self):
        self.assertFalse(accept_slow_plan(self.plan, self.c,
                                         replace(self.o, cancelled=True), "r1", 1201))


if __name__ == "__main__":
    unittest.main()
