"""Python half of the shared exact JSON field-name conformance matrix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from deferred_teleop.mission_view import ArticulatedMissionViewState, MissionViewState
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fixtures" / "m2" / "json-field-exactness.json"
PYTHON_PARSERS = {
    "mission_view": MissionViewState.model_validate_json,
    "articulated_view": ArticulatedMissionViewState.model_validate_json,
}


def _cases() -> list[dict[str, Any]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "dtt.json-field-exactness/0"
    return manifest["cases"]


def _mutated_json(case: dict[str, Any]) -> str:
    fixture = ROOT / case["fixture"]
    raw = fixture.read_text(encoding="utf-8")
    before = case["before"]
    assert raw.count(before) == 1, case["name"]
    return raw.replace(before, case["after"], 1)


def _value_at(model: Any, path: str) -> Any:
    for name in path.split("."):
        model = getattr(model, name)
    return model


def test_shared_matrix_has_one_compact_case_set_for_each_unreal_parser() -> None:
    cases = _cases()
    assert len({case["name"] for case in cases}) == len(cases)
    assert {case["parser"] for case in cases} == {
        "mission_view",
        "robot_model",
        "articulated_view",
    }
    assert {case["expected"] for case in cases} == {"ACCEPT", "REJECT"}


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in _cases()
        if case["parser"] in PYTHON_PARSERS
    ],
    ids=lambda case: case["name"],
)
def test_python_matches_shared_exact_field_name_matrix(case: dict[str, Any]) -> None:
    parse = PYTHON_PARSERS[case["parser"]]
    mutated = _mutated_json(case)

    if case["expected"] == "REJECT":
        with pytest.raises(ValidationError):
            parse(mutated)
        return

    parsed = parse(mutated)
    assert _value_at(parsed, case["value_path"]) == case["value"]
