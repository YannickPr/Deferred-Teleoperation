from __future__ import annotations

import json

import pytest
from deferred_teleop.unreal_report import main, verify_report

REQUIRED = ["DeferredTeleop.M2.GoalAuthoring.Example"]
CONTEXT = "Other.ExpectedNegative"


def report(state="Success", name=REQUIRED[0]):
    return {"failed": 0, "notRun": 0, "tests": [{"fullTestPath": name, "state": state}]}


def test_contextual_warning_requires_explicit_reviewed_allowance():
    data = report()
    data["tests"].append({"fullTestPath": CONTEXT, "state": "SuccessWithWarnings"})
    assert not verify_report(data, REQUIRED)["passed"]
    result = verify_report(data, REQUIRED, [CONTEXT])
    assert result["passed"]
    assert result["contextual_warning_tests"] == [CONTEXT]
    assert not verify_report(data, REQUIRED, [CONTEXT.upper()])["passed"]


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


@pytest.mark.parametrize("required", [[], [""], ["x", "x"], "not-an-array", [None], [{}]])
def test_invalid_required_set_fails_closed(required):
    assert not verify_report(report(), required)["passed"]


@pytest.mark.parametrize("allowance", [[REQUIRED[0]], [None], [""], ["x", "x"], "all"])
def test_invalid_or_overlapping_warning_allowance_is_rejected(allowance):
    assert not verify_report(report(), REQUIRED, allowance)["passed"]


def test_malformed_report_is_rejected():
    for value in (None, [], {}, {"tests": [None]}, {"tests": [{"state": "Success"}]}):
        assert not verify_report(value, REQUIRED)["passed"]


@pytest.mark.parametrize("key,value", [
    ("errors", 1), ("errors", False), ("errors", -1), ("errors", "0"),
    ("warnings", 1), ("warnings", None), ("warnings", 0.0),
])
def test_success_cannot_hide_bad_counts(key, value):
    data = report()
    data["tests"][0][key] = value
    assert not verify_report(data, REQUIRED)["passed"]


@pytest.mark.parametrize("entries", [
    [{"event": {"type": "Error"}}], [{"event": {"type": "Warning"}}],
    [{"event": {"type": "garbage"}}], [{"event": {}}], [None], None, {},
])
def test_success_cannot_hide_bad_entries(entries):
    data = report()
    data["tests"][0]["entries"] = entries
    assert not verify_report(data, REQUIRED)["passed"]


def test_warning_allowance_never_suppresses_an_error_event():
    data = report()
    data["tests"].append({
        "fullTestPath": CONTEXT, "state": "SuccessWithWarnings", "errors": 0,
        "entries": [{"event": {"type": "Error", "message": "bad"}}],
    })
    assert not verify_report(data, REQUIRED, [CONTEXT])["passed"]


@pytest.mark.parametrize("key,value", [
    ("succeeded", 2), ("succeeded", True), ("succeededWithWarnings", 1),
    ("failed", 1), ("notRun", 2), ("inProcess", 1),
])
def test_summary_must_agree_with_rows(key, value):
    data = report()
    data[key] = value
    assert not verify_report(data, REQUIRED)["passed"]


def test_consistent_summary_and_info_entries_pass():
    data = report()
    data.update(succeeded=1, succeededWithWarnings=0, inProcess=0)
    data["tests"][0].update(errors=0, warnings=0, entries=[{"event": {"type": "Info"}}])
    assert verify_report(data, REQUIRED)["passed"]


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


def test_cli_warning_allowance(tmp_path, capsys):
    required, source, allowed = (tmp_path / n for n in ("req.json", "index.json", "allow.json"))
    required.write_text(json.dumps(REQUIRED), encoding="utf-8")
    data = report()
    data["tests"].append({"fullTestPath": CONTEXT, "state": "SuccessWithWarnings"})
    source.write_text(json.dumps(data), encoding="utf-8")
    allowed.write_text(json.dumps([CONTEXT]), encoding="utf-8")
    args = ["--report", str(source), "--required", str(required)]
    assert main(args) == 1
    assert main(args + ["--allow-context-warnings", str(allowed)]) == 0
    capsys.readouterr()


@pytest.mark.parametrize("raw", [
    '{"failed": 1, "failed": 0, "tests": []}',
    '{"tests": [], "extra": NaN}', '{"tests": [], "extra": Infinity}',
])
def test_cli_rejects_ambiguous_json(tmp_path, capsys, raw):
    required = tmp_path / "required.json"
    required.write_text(json.dumps(REQUIRED), encoding="utf-8")
    source = tmp_path / "index.json"
    source.write_text(raw, encoding="utf-8")
    assert main(["--report", str(source), "--required", str(required)]) == 1
    assert not json.loads(capsys.readouterr().out)["passed"]
