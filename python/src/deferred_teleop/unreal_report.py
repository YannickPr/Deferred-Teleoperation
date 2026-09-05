"""Inspect Unreal Automation report contents, not build or headset evidence.

Keep native build/editor exits and compiled-source hashes separately. Approved
contextual warning tests must be named explicitly; required tests never inherit
that allowance. A green state cannot hide nonzero error counts or error entries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _names(value: object, *, allow_empty: bool) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(name, str) and bool(name.strip()) for name in value)
        and len(set(value)) == len(value)
    )


def verify_report(
    report: object, required: object, allowed_context_warnings: object = None
) -> dict[str, Any]:
    """Require exact names/states and reject contradictory rows and summaries."""
    errors: list[str] = []
    if not _names(required, allow_empty=False):
        return {"passed": False, "errors": ["required tests must be unique non-empty names"]}
    if allowed_context_warnings is None:
        allowed_context_warnings = []
    if not _names(allowed_context_warnings, allow_empty=True):
        return {"passed": False, "errors": ["warning allowance must be unique non-empty names"]}
    if set(required) & set(allowed_context_warnings):
        return {"passed": False, "errors": ["required tests cannot be granted warning allowances"]}
    if not isinstance(report, dict) or not isinstance(report.get("tests"), list):
        return {"passed": False, "errors": ["report must contain a tests array"]}
    by_name: dict[str, str] = {}
    contextual_warnings: list[str] = []
    state_counts = {"Success": 0, "SuccessWithWarnings": 0}
    for index, test in enumerate(report["tests"]):
        if not isinstance(test, dict):
            errors.append(f"invalid test record {index}")
            continue
        name, state = test.get("fullTestPath"), test.get("state")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"missing exact fullTestPath at {index}")
            continue
        if name in by_name:
            errors.append(f"duplicate test record: {name}")
        if not isinstance(state, str):
            errors.append(f"invalid state for {name}")
            state = "INVALID"
        by_name[name] = state
        if state not in state_counts:
            errors.append(f"non-successful test: {name}: {state}")
        else:
            state_counts[state] += 1
        has_warning = state == "SuccessWithWarnings"
        for key in ("errors", "warnings"):
            if key not in test:
                continue
            count = test[key]
            if type(count) is not int or count < 0:
                errors.append(f"invalid {key} count for {name}")
            elif key == "errors" and count:
                errors.append(f"nonzero errors for {name}")
            elif key == "warnings" and count:
                has_warning = True
        if "entries" in test:
            entries = test["entries"]
            if not isinstance(entries, list):
                errors.append(f"invalid entries for {name}")
            else:
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("event"), dict):
                        errors.append(f"invalid event entry for {name}")
                        continue
                    kind = entry["event"].get("type")
                    if kind == "Error":
                        errors.append(f"error event for {name}")
                    elif kind == "Warning":
                        has_warning = True
                    elif kind != "Info":
                        errors.append(f"unsupported event type for {name}: {kind}")
        if has_warning:
            if state != "SuccessWithWarnings":
                errors.append(f"warning evidence contradicts state for {name}")
            if name in required or name not in allowed_context_warnings:
                errors.append(f"unapproved warning test: {name}")
            if name not in required:
                contextual_warnings.append(name)
    for name in required:
        if name not in by_name:
            errors.append(f"required test missing: {name}")
        elif by_name[name] != "Success":
            errors.append(f"required test must be Success without warnings: {name}")
    # Optional fields differ across report versions, but any supplied summary is binding.
    summaries = {
        "failed": 0, "notRun": 0, "inProcess": 0,
        "succeeded": state_counts["Success"],
        "succeededWithWarnings": state_counts["SuccessWithWarnings"],
    }
    for key, expected in summaries.items():
        if key in report and (type(report[key]) is not int or report[key] != expected):
            errors.append(f"report summary {key} must equal {expected}")
    return {
        "passed": not errors,
        "required_test_count": len(required),
        "observed_test_count": len(by_name),
        "required_tests": {name: by_name.get(name, "MISSING") for name in required},
        "contextual_warning_tests": contextual_warnings,
        "allowed_context_warning_tests": allowed_context_warnings,
        "errors": errors,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate report JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonstandard report JSON constant: {value}")


def _load_json(raw: bytes) -> object:
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="Automation index.json")
    parser.add_argument("--required", type=Path, required=True, help="Exact test names (JSON)")
    parser.add_argument("--allow-context-warnings", type=Path, help="Reviewed warning names (JSON)")
    args = parser.parse_args(argv)
    try:
        raw = args.report.read_bytes()
        allowance = (
            _load_json(args.allow_context_warnings.read_bytes())
            if args.allow_context_warnings else []
        )
        result = verify_report(_load_json(raw), _load_json(args.required.read_bytes()), allowance)
        result["report_sha256"] = hashlib.sha256(raw).hexdigest()
        result["scope"] = "automation-report-inspection-only"
    except (OSError, ValueError) as error:
        result = {"passed": False, "errors": [str(error)]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
