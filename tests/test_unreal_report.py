from __future__ import annotations

import json

import pytest
from deferred_teleop.unreal_report import main, verify_report

REQUIRED = ["DeferredTeleop.M2.GoalAuthoring.Example"]


def report(state="Success", name=REQUIRED[0]):
    return {"failed": 0, "notRun": 0, "tests": [{"fullTestPath": name, "state": state}]}


def test_exact_success_and_contextual_warning_are_distinguished():
    data = report()
    data["tests"].append({"fullTestPath": "Other.ExpectedNegative", "state": "SuccessWithWarnings"})
    result = verify_report(data, REQUIRED)
    assert result["passed"]
    assert result["contextual_warning_tests"] == ["Other.ExpectedNegative"]


@pytest.mark.parametrize(
    "state", ["Fail", "NotRun", "InProcess", "SuccessWithWarnings", "success", None]
)
def test_required_scope_never_accepts_incomplete_or_warning_states(state):
    assert not verify_report(report(state), REQUIRED)["passed"]


def test_missing_duplicate_or_case_changed_test_is_not_success():
    assert not verify_report({"tests": []}, REQUIRED)["passed"]
    assert not verify_report(report(name=REQUIRED[0].upper()), REQUIRED)["passed"]
    data = report()
    data["tests"] *= 2
    assert not verify_report(data, REQUIRED)["passed"]


def test_unrelated_failure_and_summary_conflict_are_fatal():
    data = report()
    data["tests"].append({"fullTestPath": "Other.Failed", "state": "Fail"})
    assert not verify_report(data, REQUIRED)["passed"]
    data = report()
    data["failed"] = 1
    assert not verify_report(data, REQUIRED)["passed"]
    data["failed"] = False
    assert not verify_report(data, REQUIRED)["passed"]


@pytest.mark.parametrize("required", [[], [""], ["x", "x"], "not-an-array", [None]])
def test_invalid_required_set_fails_closed(required):
    assert not verify_report(report(), required)["passed"]


def test_malformed_report_is_rejected():
    for value in (None, [], {}, {"tests": [None]}, {"tests": [{"state": "Success"}]}):
        assert not verify_report(value, REQUIRED)["passed"]


def test_cli_reports_hash_and_fails_on_missing_file(tmp_path, capsys):
    required = tmp_path / "required.json"
    required.write_text(json.dumps(REQUIRED), encoding="utf-8")
    source = tmp_path / "index.json"
    source.write_text(json.dumps(report()), encoding="utf-8")
    assert main(["--report", str(source), "--required", str(required)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["report_sha256"]) == 64
    assert result["scope"] == "automation-report-inspection-only"
    assert main(["--report", str(tmp_path / "absent"), "--required", str(required)]) == 1


@pytest.mark.parametrize("raw", [
    '{"failed": 1, "failed": 0, "tests": []}',
    '{"tests": [], "extra": NaN}',
    '{"tests": [], "extra": Infinity}',
])
def test_cli_rejects_ambiguous_json(tmp_path, capsys, raw):
    required = tmp_path / "required.json"
    required.write_text(json.dumps(REQUIRED), encoding="utf-8")
    source = tmp_path / "index.json"
    source.write_text(raw, encoding="utf-8")
    assert main(["--report", str(source), "--required", str(required)]) == 1
    assert not json.loads(capsys.readouterr().out)["passed"]
