from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from deferred_teleop.inspect import _causal_history


@pytest.mark.parametrize(
    ("profile", "restart_mission"),
    (
        ("short-visible-delay", False),
        ("short-visible-fault", True),
    ),
)
def test_delayed_dummy_runs_as_four_processes_and_reconciles(
    tmp_path: Path,
    profile: str,
    restart_mission: bool,
) -> None:
    data_dir = tmp_path / profile
    command = [
        sys.executable,
        "-m",
        "deferred_teleop.demo",
        "delayed-dummy",
        "--profile",
        profile,
        "--data-dir",
        str(data_dir),
        "--phase-duration",
        "0.01",
        "--retry-interval",
        "0.01",
        "--timeout",
        "10",
        "--quiet",
    ]
    if restart_mission:
        command.append("--restart-mission-after-admission")

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    ready = next(item for item in events if item["event"] == "demo.processes_ready")
    result = next(item for item in events if item["event"] == "demo.completed")
    assert len(set(ready["pids"].values())) == 4
    assert len(set(ready["stores"].values())) == 3
    assert all((data_dir / f"{node}.db").is_file() for node in ("mission", "field", "robot"))
    assert result["terminal_state"] == "SUCCEEDED"
    assert result["effect_counter"] == 1
    assert result["phases"] == [
        "VALIDATING",
        "APPROACHING",
        "CONTACTING",
        "VERIFYING_EFFECT",
        "RETRACTING",
        "SUCCEEDED",
    ]
    assert result["confirmed_provenance"] == "MEASURED"
    assert result["arrival_provenance"] == "PREDICTED"
    assert result["target_provenance"] == "OPERATOR_ASSERTED"
    assert result["mission_restarted"] is restart_mission

    history = _causal_history(data_dir, UUID(result["operation_id"]))
    message_types = {item["payload_type"] for item in history}
    assert {
        "operation.intent",
        "operation.grounded",
        "operation.plan",
        "task.assignment",
        "execution.contract",
        "execution.event",
        "robot.state",
        "robot.forecast",
        "site.snapshot",
    } <= message_types
