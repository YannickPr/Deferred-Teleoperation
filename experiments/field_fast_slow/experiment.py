"""Reproducible CPU/CUDA synthetic study, NOT a SO-101 dynamics simulation.

Run from this directory: python experiment.py --out results/local
Torch is optional for core.py but required here. No network or robot I/O.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import torch
from torch import nn

from core import Action, Contract, Observation, features, guard, one_hot, proposal, rule_action

SCENARIOS = ("nominal", "shifted", "over_tolerance", "ambiguous", "dropout",
             "cancelled", "already_latched", "mid_motion_shift")


class FastHead(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(8, 48), nn.ReLU(),
                                    nn.Linear(48, 48), nn.ReLU(), nn.Linear(48, 4))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


def dataset(n: int, seed: int) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """IID synthetic snapshots, independent seeds per split; labels from rules.

    Oversamples alignment and near-contact states so PRESS is learnable.
    No real demonstrations, vision, history, or language generalization claimed.
    """
    rng = random.Random(seed)
    c = Contract()
    xs, ys, rows = [], [], []
    for index in range(n):
        shift = rng.uniform(-0.075, 0.075)
        error = rng.uniform(-0.09, 0.09) if index % 2 else rng.uniform(-0.004, 0.004)
        z = rng.uniform(0.0, 0.06) if index % 3 else rng.uniform(0.0, 0.015)
        o = Observation(index, 1201, shift, shift - error, z,
                        visible=rng.random() > 0.08, unique=rng.random() > 0.08,
                        latched=rng.random() < 0.18, estop=rng.random() < 0.02,
                        cancelled=rng.random() < 0.02, budget_available=rng.random() > 0.04)
        label = rule_action(o, c)
        x = features(o, c)
        xs.append(x)
        ys.append(int(label))
        rows.append({"sample_id": f"synthetic-{seed}-{index}",
                     "session_id": f"synthetic-seed-{seed}",
                     "features": list(x), "label": int(label)})
    return np.asarray(xs, np.float32), np.asarray(ys, np.int64), rows


def probabilities(model: FastHead, x: np.ndarray, temperature: float,
                  device: str) -> np.ndarray:
    with torch.inference_mode():
        logits = model(torch.as_tensor(x, device=device)) / temperature
        return torch.softmax(logits, -1).cpu().numpy()


def metrics(ps: np.ndarray, labels: np.ndarray) -> dict:
    pred = ps.argmax(-1)
    conf = ps.max(-1)
    correct = pred == labels
    truth = np.eye(4)[labels]
    ece = 0.0
    for low in np.arange(0, 1, 0.1):
        mask = (conf >= low) & (conf < low + 0.1 if low < 0.9 else conf <= 1)
        if mask.any():
            ece += mask.mean() * abs(correct[mask].mean() - conf[mask].mean())
    selected = conf >= 0.9
    return {
        "n": len(labels), "accuracy": float(correct.mean()),
        "nll": float(-np.log(ps[np.arange(len(labels)), labels].clip(1e-12)).mean()),
        "brier_multiclass_sum": float(((ps - truth) ** 2).sum(-1).mean()),
        "ece_10_equal_width": float(ece), "coverage_at_0_9": float(selected.mean()),
        "error_given_confidence_ge_0_9": (
            float(1 - correct[selected].mean()) if selected.any() else None),
        "confusion_matrix": [[int(((labels == i) & (pred == j)).sum())
                              for j in range(4)] for i in range(4)],
    }


def train(device: str, seed: int) -> tuple[FastHead, float, dict, dict]:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    x, y, train_rows = dataset(16000, seed)
    xc, yc, cal_rows = dataset(4000, seed + 1)
    xt, yt, test_rows = dataset(4000, seed + 2)
    model = FastHead().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.002)
    tx, ty = torch.tensor(x, device=device), torch.tensor(y, device=device)
    start = time.perf_counter()
    model.train()
    for _ in range(55):
        permutation = torch.randperm(len(y), device=device)
        for indices in permutation.split(256):
            loss = nn.functional.cross_entropy(model(tx[indices]), ty[indices])
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
    model.eval()
    training_s = time.perf_counter() - start
    # Temperature chosen using CALIBRATION ONLY; test is never used for selection.
    with torch.inference_mode():
        logits = model(torch.tensor(xc, device=device)).cpu()
    temperatures = np.geomspace(0.3, 3.0, 61)
    losses = [nn.functional.cross_entropy(logits / float(t), torch.tensor(yc)).item()
              for t in temperatures]
    temperature = float(temperatures[int(np.argmin(losses))])
    summary = {
        "model": "random-init 8-48-48-4 ReLU MLP; rule distillation, not LLM or RLCD",
        "parameters": sum(p.numel() for p in model.parameters()),
        "training_s": training_s, "temperature": temperature,
        "calibration_before": metrics(probabilities(model, xc, 1, device), yc),
        "calibration_after": metrics(probabilities(model, xc, temperature, device), yc),
        "test_before": metrics(probabilities(model, xt, 1, device), yt),
        "test_after": metrics(probabilities(model, xt, temperature, device), yt),
        "split_seeds": {"train": seed, "calibration": seed + 1, "test": seed + 2},
    }
    return model, temperature, summary, {
        "train": train_rows, "calibration": cal_rows, "test": test_rows,
    }


def episode(policy: str, scenario: str, seed: int, model: FastHead, temp: float,
            device: str, slow_delay_s: float = 2.0) -> dict:
    """Independent point-contact world + bounded motion primitive.

    APPROACH/PRESS select a mathematical primitive, not actuator commands.
    Reference x=0 is authored before 1200s virtual transit; observation is local.
    Rule/MLP share perception, primitives, guard, budget, and initial conditions.
    """
    rng = random.Random(seed)
    c = Contract()
    tool_x, tool_z = rng.uniform(-0.09, -0.06), 0.04
    latched = scenario == "already_latched"
    spent = False
    contacts = []
    blocks = 0
    attempted_after_cancel = 0
    slow_pending = None
    slow_next_s = 1200.0
    completed_at = None
    skill_active = False
    dt = 0.02
    for step in range(401):
        now = 1200.0 + step * dt
        elapsed = step * dt
        shift = 0.025 if scenario == "shifted" else 0.0
        if scenario == "over_tolerance":
            shift = 0.065
        if scenario == "mid_motion_shift" and elapsed >= 0.8:
            shift = 0.025
        # The other button occupies the obsolete reference location after a move.
        true_a, true_b = shift, (0.0 if shift else 0.055)
        cancelled = scenario == "cancelled" and elapsed >= 0.5
        visible = not (scenario == "dropout" and 0.6 <= elapsed < 1.6)
        obs = Observation(step, now, true_a, tool_x, tool_z,
                          visible=visible, unique=scenario != "ambiguous",
                          latched=latched, cancelled=cancelled, budget_available=not spent)
        if policy == "stale_reference_unguarded":
            # Negative control, intentionally wrong: never reground delayed intent.
            source = replace(obs, target_x_m=0, unique=True, visible=True,
                             cancelled=False)
            p = proposal(source, c, one_hot(rule_action(source, c)), now)
            action = Action(int(np.argmax(p.probabilities)))
        elif policy == "slow_snapshot_guarded":
            # No real LLM called: exact oracle answer with INJECTED 2s latency.
            if slow_pending is None and now >= slow_next_s:
                slow_pending = (now + slow_delay_s, proposal(
                    obs, c, one_hot(rule_action(obs, c)), now + slow_delay_s))
            if slow_pending is None or now + 1e-9 < slow_pending[0]:
                continue
            _, p = slow_pending
            slow_pending = None
            slow_next_s = now
            verdict = guard(p, obs, c, now)
            action = verdict.action
            blocks += int(action == Action.HOLD)
        else:
            if policy == "fast_rules_guarded" or (
                    policy == "mlp_selects_rule_skill" and skill_active):
                ps = one_hot(rule_action(obs, c))
            else:
                x = np.asarray([features(obs, c)], dtype=np.float32)
                ps = tuple(float(v) for v in probabilities(model, x, temp, device)[0])
            p = proposal(obs, c, ps, now)
            verdict = guard(p, obs, c, now)
            action = verdict.action
            blocks += int(action == Action.HOLD and verdict.reason != "MODEL_HOLD")
            if policy == "mlp_selects_rule_skill" and action in (Action.APPROACH, Action.PRESS):
                skill_active = True  # bounded skill owns its internal deterministic transitions
        if action == Action.VERIFY and latched:
            completed_at = elapsed
            break
        if action == Action.APPROACH:
            # Stop/raise first before lateral motion if below the hover plane.
            if tool_z < 0.015 - 1e-9:
                tool_z = min(0.015, tool_z + 0.02 * dt)
            else:
                target_x = p.source.target_x_m if policy == "stale_reference_unguarded" else true_a
                tool_x += max(-0.04 * dt, min(0.04 * dt, target_x - tool_x))
                tool_z = max(0.015, tool_z - 0.02 * dt)
        elif action == Action.PRESS:
            attempted_after_cancel += int(cancelled)
            tool_z = max(0, tool_z - 0.015 * dt)
            if tool_z <= 1e-9 and not spent:
                # Hidden geometry, NOT the policy's selected name, decides contact.
                contact = "A" if abs(tool_x - true_a) <= 0.004 else (
                    "B" if abs(tool_x - true_b) <= 0.004 else "NONE")
                contacts.append(contact)
                spent = True
                latched = contact == "A"
                if latched:
                    completed_at = elapsed
                    break
    expected_hold = scenario in ("over_tolerance", "ambiguous", "cancelled")
    return {
        "policy": policy, "scenario": scenario, "seed": seed,
        "actionable": not expected_hold, "completed": completed_at is not None,
        "completion_s": completed_at, "contacts": contacts,
        "unauthorized_contacts": len(contacts) if expected_hold else contacts.count("B"),
        "post_cancel_press_ticks": attempted_after_cancel, "guard_blocks": blocks,
    }


def benchmark_latency(model: FastHead, temp: float, device: str) -> dict:
    x = np.ones((1, 8), np.float32)
    for _ in range(100):
        probabilities(model, x, temp, device)
    values = []
    for _ in range(2000):
        start = time.perf_counter_ns()
        probabilities(model, x, temp, device)  # .cpu() synchronizes CUDA before timing ends
        values.append((time.perf_counter_ns() - start) / 1e6)
    return dict(zip(("p50_ms", "p95_ms", "p99_ms", "max_ms"),
                    map(float, np.percentile(values, [50, 95, 99, 100])), strict=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--episodes-per-scenario", type=int, default=20)
    args = parser.parse_args()
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    if args.device == "cuda" and not torch.cuda.is_available():
        parser.error("CUDA unavailable; do not report CPU results as RTX 3090 results")
    if args.episodes_per_scenario < 1:
        parser.error("episodes-per-scenario must be positive")
    args.out.mkdir(parents=True, exist_ok=True)
    model, temp, learning, rows = train(args.device, args.seed)
    torch.save({"state_dict": model.cpu().state_dict(), "temperature": temp},
               args.out / "fast_head.pt")
    model.to(args.device)
    for split, data in rows.items():
        (args.out / f"{split}.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in data), encoding="utf-8")
    trials = [episode(policy, scenario, args.seed + i, model, temp, args.device)
              for policy in ("stale_reference_unguarded", "slow_snapshot_guarded",
                             "fast_rules_guarded", "fast_mlp_guarded", "mlp_selects_rule_skill")
              for scenario in SCENARIOS for i in range(args.episodes_per_scenario)]
    aggregates = {}
    for policy in sorted({row["policy"] for row in trials}):
        subset = [r for r in trials if r["policy"] == policy]
        actionable = [r for r in subset if r["actionable"]]
        aggregates[policy] = {
            "episodes": len(subset), "actionable": len(actionable),
            "completed_actionable": sum(r["completed"] for r in actionable),
            "unauthorized_contacts": sum(r["unauthorized_contacts"] for r in subset),
            "post_cancel_press_ticks": sum(r["post_cancel_press_ticks"] for r in subset),
            "completed_by_scenario": {s: sum(r["completed"] for r in subset if r["scenario"] == s)
                                      for s in SCENARIOS},
        }
    report = {
        "scope": "synthetic state/point-contact + trained MLP; no LLM, Jev, camera or SO101",
        "base_commit": "26783d9cae483dd119179135accdb48829fc4fc6",
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "torch": torch.__version__, "numpy": np.__version__,
                        "device": args.device,
                        "gpu": torch.cuda.get_device_name(0) if args.device == "cuda" else None},
        "seed": args.seed, "learning": learning,
        "study_design": "Exploratory: rule-skill selector added after pilot exposed phase deadlock",
        "single_observation_inference_latency": benchmark_latency(model, temp, args.device),
        "simulated_timing": {"mission_delay_s": 1200, "robot_tick_s": 0.02,
                             "injected_slow_inference_s": 2, "observation_ttl_s": 0.1},
        "aggregates": aggregates,
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(Path(__file__).parent.glob("*.py"))},
        "limitations": ["No rigid-body dynamics, force, friction, camera or servo model",
                        "Perfect synthetic entity identity except explicit ambiguity",
                        "Rule-generated IID snapshots; not demonstrations or open-world reasoning",
                        "Slow latency is injected; does not measure any LLM",
                        "Temperature scaling is not RLCD and is not a safety guarantee",
                        "MLP vs rules comparison tests substitution, not necessity of ML",
                        "Results are not tests of existing M3a services or physical hardware"],
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (args.out / "episodes.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in trials), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
