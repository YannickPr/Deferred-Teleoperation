from __future__ import annotations

import math
from pathlib import Path

import pytest
from deferred_teleop.robot_model.so101 import (
    generate_so101_description,
    git_blob_sha1,
    serialize_description,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "robots" / "so101" / "upstream" / "so101_new_calib.urdf"
SOURCE_LOCK = ROOT / "robots" / "so101" / "source-lock.toml"
GENERATED = ROOT / "robots" / "so101" / "generated" / "so101.kinematics.json"
EXPECTED_BLOB_SHA1 = "9552a231d8b23bed68ec15779eba620c5d875ec4"

EXPECTED_LINKS = {
    "base_link",
    "shoulder_link",
    "upper_arm_link",
    "lower_arm_link",
    "wrist_link",
    "gripper_link",
    "gripper_frame_link",
    "moving_jaw_so101_v1_link",
}
EXPECTED_ARM_JOINTS = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
]


def _description() -> dict[str, object]:
    return generate_so101_description(SOURCE, SOURCE_LOCK)


def test_vendored_source_matches_pinned_git_blob() -> None:
    assert git_blob_sha1(SOURCE.read_bytes()) == EXPECTED_BLOB_SHA1


def test_generator_emits_expected_so101_structure() -> None:
    description = _description()

    assert description["schema_version"] == "dtt.robot-description/0"
    assert description["model_id"] == "so101_new_calib"
    assert description["root_link"] == "base_link"
    assert {link["name"] for link in description["links"]} == EXPECTED_LINKS

    joints = {joint["name"]: joint for joint in description["joints"]}
    assert set(joints) == {
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
        "gripper",
        "gripper_frame_joint",
    }
    assert joints["gripper_frame_joint"]["type"] == "fixed"
    assert joints["gripper_frame_joint"]["axis_joint_frame"] is None
    assert joints["gripper"]["type"] == "revolute"

    groups = {group["name"]: group["joints"] for group in description["joint_groups"]}
    assert groups["arm"] == EXPECTED_ARM_JOINTS
    assert groups["gripper"] == ["gripper"]
    assert description["tool_frames"] == [
        {"name": "gripper_frame_link", "link": "gripper_frame_link"}
    ]


def test_revolute_axes_are_unit_length_and_limits_are_ordered() -> None:
    for joint in _description()["joints"]:
        if joint["type"] != "revolute":
            continue
        axis = joint["axis_joint_frame"]
        assert math.sqrt(sum(component * component for component in axis)) == pytest.approx(1.0)
        limits = joint["position_limits_rad"]
        assert limits["lower"] <= limits["upper"]


def test_generated_quaternions_are_finite_and_normalized() -> None:
    description = _description()
    transforms = [joint["parent_to_joint"] for joint in description["joints"]]
    transforms.extend(
        visual["link_to_visual"]
        for link in description["links"]
        for visual in link["visuals"]
    )

    for transform in transforms:
        quaternion = transform["rotation_xyzw"]
        assert all(math.isfinite(component) for component in quaternion)
        assert sum(component * component for component in quaternion) == pytest.approx(1.0)


def test_generation_is_byte_deterministic() -> None:
    first = serialize_description(_description())
    second = serialize_description(_description())
    assert first == second
    assert first.endswith("\n")


def test_committed_description_matches_fresh_generation() -> None:
    assert GENERATED.read_text(encoding="utf-8") == serialize_description(_description())


def test_tampered_source_fails_before_parsing(tmp_path: Path) -> None:
    tampered_source = tmp_path / "so101.urdf"
    tampered_source.write_bytes(SOURCE.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="source hash mismatch"):
        generate_so101_description(tampered_source, SOURCE_LOCK)
