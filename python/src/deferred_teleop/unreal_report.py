"""Fail-closed inspection of a local Unreal Automation report.

This validates report contents, not compilation, rendered UX, physical safety,
report authenticity, or the identity of compiled sources. Keep the build log,
editor exit code and before/after source hashes alongside the resulting receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def verify_report(report: object, required: object) -> dict[str, Any]:
    """Require every exact test name once, with Success and no failing context."""
    errors: list[str] = []
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(name, str) or not name.strip() for name in required)
        or len(set(required)) != len(required)
    ):
        return {"passed": False, "errors": ["required tests must be unique non-empty names"]}
    if not isinstance(report, dict) or not isinstance(report.get("tests"), list):
        return {"passed": False, "errors": ["report must contain a tests array"]}
    by_name: dict[str, str] = {}
    contextual_warnings: list[str] = []
    for index, test in enumerate(report["tests"]):
        if not isinstance(test, dict):
            errors.append(f"invalid test record {index}")
            continue
        name, state = test.get("fullTestPath"), test.get("state")
        if not isinstance(name, str) or not name:
            errors.append(f"missing exact fullTestPath at {index}")
            continue
        if name in by_name:
            errors.append(f"duplicate test record: {name}")
        if not isinstance(state, str):
            errors.append(f"invalid state for {name}")
            state = "INVALID"
        by_name[name] = state
        if state not in {"Success", "SuccessWithWarnings"}:
            errors.append(f"non-successful test: {name}: {state}")
        if state == "SuccessWithWarnings" and name not in required:
            contextual_warnings.append(name)
    for name in required:
        if name not in by_name:
            errors.append(f"required test missing: {name}")
        elif by_name[name] != "Success":
            errors.append(f"required test must be Success without warnings: {name}")
    # Summary fields vary by engine report version. If present, never ignore
    # a conflicting failure/not-run summary even when individual rows look green.
    for key in ("failed", "notRun", "inProcess"):
        if key in report and (type(report[key]) is not int or report[key] != 0):
            errors.append(f"report summary {key} must be integer zero")
    return {
        "passed": not errors,
        "required_test_count": len(required),
        "observed_test_count": len(by_name),
        "required_tests": {name: by_name.get(name, "MISSING") for name in required},
        "contextual_warning_tests": contextual_warnings,
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
    parser.add_argument(
        "--required", type=Path, required=True, help="JSON array of exact test names"
    )
    args = parser.parse_args(argv)
    try:
        raw = args.report.read_bytes()
        result = verify_report(_load_json(raw), _load_json(args.required.read_bytes()))
        result["report_sha256"] = hashlib.sha256(raw).hexdigest()
        result["scope"] = "automation-report-inspection-only"
    except (OSError, ValueError) as error:
        result = {"passed": False, "errors": [str(error)]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
