"""Tests for the independent matrix oracle and its committed fixtures."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest
from deferred_teleop.robot_model import kinematics_reference as reference

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = REPOSITORY_ROOT / "robots/so101/generated/so101.kinematics.json"
FIXTURE_PATH = REPOSITORY_ROOT / "fixtures/m2/kinematics/so101-fk.json"


def _assert_matrix_close(
    expected: list[list[float]],
    actual: reference.Matrix4,
    *,
    position_tolerance: float,
    rotation_tolerance: float,
) -> None:
    assert len(expected) == 4 and all(len(row) == 4 for row in expected)
    for row in range(3):
        for column in range(3):
            assert actual[row][column] == pytest.approx(
                expected[row][column], abs=rotation_tolerance, rel=0.0
            )
        assert actual[row][3] == pytest.approx(
            expected[row][3], abs=position_tolerance, rel=0.0
        )
    assert actual[3] == pytest.approx(expected[3], abs=rotation_tolerance, rel=0.0)


def _assert_flat_matrix_close(
    expected: tuple[tuple[float, ...], ...], actual: reference.Matrix4
) -> None:
    expected_values = [component for row in expected for component in row]
    actual_values = [component for row in actual for component in row]
    assert actual_values == pytest.approx(expected_values, abs=1.0e-15, rel=0.0)


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_cases_match_independent_matrix_evaluator() -> None:
    model = reference.load_model(MODEL_PATH)
    fixture = _load_fixture()
    expected_case_ids = {
        "zero",
        "shoulder_pan_only",
        "shoulder_and_elbow",
        "multi_joint_nonsymmetric",
        "joint_limits_lower",
        "joint_limits_upper",
        "tool_fixed",
        "root_transform_noncommuting",
        "reordered_joint_positions",
    }
    assert fixture["schema_version"] == "dtt.kinematics-fixtures/0"
    case_ids = [case["id"] for case in fixture["cases"]]
    assert len(fixture["cases"]) == len(expected_case_ids)
    assert len(case_ids) == len(set(case_ids))
    assert set(case_ids) == expected_case_ids
    tolerances = fixture["tolerances"]
    expected_links = [link["name"] for link in model["links"]]
    expected_tools = [tool["name"] for tool in model["tool_frames"]]

    for case in fixture["cases"]:
        result = reference.evaluate_forward_kinematics(
            model,
            case["root_pose"],
            case["joint_positions_rad"],
        )
        for kind in ("links", "tools"):
            expected = case["expected"][kind]
            expected_names = expected_links if kind == "links" else expected_tools
            assert len(expected) == len(expected_names)
            assert len({entry["name"] for entry in expected}) == len(expected)
            assert [entry["name"] for entry in expected] == expected_names
            assert list(result[kind]) == [entry["name"] for entry in expected]
            for entry in expected:
                _assert_matrix_close(
                    entry["matrix"],
                    result[kind][entry["name"]],
                    position_tolerance=tolerances["position_m"],
                    rotation_tolerance=tolerances["rotation"],
                )


def test_matrix_oracle_has_known_quarter_turn_and_homogeneous_identity() -> None:
    quarter_turn = reference.matrix_from_axis_angle(
        (0.0, 0.0, 1.0), math.pi / 2.0, field="known_axis"
    )
    _assert_flat_matrix_close(
        (
            (0.0, -1.0, 0.0, 0.0),
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        quarter_turn,
    )
    _assert_flat_matrix_close(
        quarter_turn,
        reference.multiply_matrices(reference.identity_matrix(), quarter_turn),
    )


def test_fixture_metadata_binds_model_and_generator_sources() -> None:
    fixture = _load_fixture()
    model = reference.load_model(MODEL_PATH)
    assert fixture["model"]["model_id"] == model["model_id"]
    assert fixture["model"]["model_revision"] == model["model_revision"]
    assert fixture["model"]["source_git_blob_sha1"] == model["source"]["git_blob_sha1"]
    assert fixture["model"]["description_sha256"] == hashlib.sha256(
        MODEL_PATH.read_bytes()
    ).hexdigest()
    assert fixture["generator"]["name"] == reference.GENERATOR_NAME
    assert fixture["generator"]["version"] == reference.GENERATOR_VERSION
    assert fixture["generator"]["source_sha256"] == hashlib.sha256(
        Path(reference.__file__).read_bytes()
    ).hexdigest()


def test_reordered_named_positions_have_the_same_meaning() -> None:
    model = reference.load_model(MODEL_PATH)
    fixture = _load_fixture()
    cases = {case["id"]: case for case in fixture["cases"]}
    canonical = reference.evaluate_forward_kinematics(
        model,
        cases["multi_joint_nonsymmetric"]["root_pose"],
        cases["multi_joint_nonsymmetric"]["joint_positions_rad"],
    )
    reordered = reference.evaluate_forward_kinematics(
        model,
        cases["reordered_joint_positions"]["root_pose"],
        cases["reordered_joint_positions"]["joint_positions_rad"],
    )
    assert reordered == canonical


def test_invalid_named_states_are_rejected_explicitly() -> None:
    model = reference.load_model(MODEL_PATH)
    valid = _load_fixture()["cases"][0]

    unknown = [*valid["joint_positions_rad"], {"name": "unknown", "position_rad": 0.0}]
    with pytest.raises(reference.ReferenceKinematicsError, match="unknown joint input"):
        reference.evaluate_forward_kinematics(model, valid["root_pose"], unknown)

    duplicate = [
        *valid["joint_positions_rad"],
        {"name": "shoulder_pan", "position_rad": 0.1},
    ]
    with pytest.raises(reference.ReferenceKinematicsError, match="duplicate joint input"):
        reference.evaluate_forward_kinematics(model, valid["root_pose"], duplicate)

    missing = valid["joint_positions_rad"][:-1]
    with pytest.raises(reference.ReferenceKinematicsError, match="missing joint input"):
        reference.evaluate_forward_kinematics(model, valid["root_pose"], missing)


def test_check_fails_for_fixture_model_or_generator_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied_fixture = tmp_path / "so101-fk.json"
    copied_fixture.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    changed_fixture = json.loads(copied_fixture.read_text(encoding="utf-8"))
    changed_fixture["cases"][0]["id"] = "tampered"
    copied_fixture.write_text(json.dumps(changed_fixture, indent=2) + "\n", encoding="utf-8")
    current, message = reference.check_fixture(copied_fixture, MODEL_PATH)
    assert not current
    assert "drift" in message

    copied_model = tmp_path / "so101.kinematics.json"
    changed_model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    changed_model["joints"][0]["parent_to_joint"]["translation_m"][0] += 0.001
    copied_model.write_text(json.dumps(changed_model, indent=2) + "\n", encoding="utf-8")
    current, message = reference.check_fixture(FIXTURE_PATH, copied_model)
    assert not current
    assert "drift" in message

    copied_generator = tmp_path / "kinematics_reference.py"
    copied_generator.write_bytes(Path(reference.__file__).read_bytes() + b"\n# drift")
    monkeypatch.setattr(reference, "__file__", str(copied_generator))
    current, message = reference.check_fixture(FIXTURE_PATH, MODEL_PATH)
    assert not current
    assert "drift" in message
