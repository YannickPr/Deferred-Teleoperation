"""Independent matrix reference for the cross-language SO-101 FK fixtures.

The Unreal implementation is the system under test.  This module deliberately
uses only the Python standard library and explicit homogeneous 4x4 matrices so
that the committed fixtures do not come from serialising Unreal output or from
reusing its quaternion implementation.

Running the module without ``--check`` regenerates the committed fixture file.
CI uses ``--check`` to make a changed model, generator, or fixture fail loudly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

REFERENCE_FIXTURE_SCHEMA = "dtt.kinematics-fixtures/0"
GENERATOR_NAME = "deferred_teleop.robot_model.kinematics_reference"
GENERATOR_VERSION = "2"
POSITION_TOLERANCE_METRES = 1.0e-9
ROTATION_TOLERANCE = 1.0e-9

Matrix4 = tuple[tuple[float, float, float, float], ...]
Vector3 = tuple[float, float, float]


class ReferenceKinematicsError(ValueError):
    """Raised when a model or named state violates the FK input contract."""


def _finite(value: float, *, field: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ReferenceKinematicsError(f"{field} must be finite")
    return value


def _matrix(values: Sequence[Sequence[float]], *, field: str) -> Matrix4:
    if len(values) != 4 or any(len(row) != 4 for row in values):
        raise ReferenceKinematicsError(f"{field} must be a 4x4 matrix")
    result = tuple(
        tuple(
            _finite(component, field=f"{field}[{row}][{column}]")
            for column, component in enumerate(values[row])
        )
        for row in range(4)
    )
    return result  # type: ignore[return-value]


def identity_matrix() -> Matrix4:
    """Return the identity homogeneous transform."""

    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _ordered_sum(values: Iterable[float]) -> float:
    """Keep the reference operation order stable across Python versions.

    Python 3.12 changed float ``sum`` to compensated summation. Explicit
    left-to-right addition preserves the already validated fixture values.
    """

    total = 0.0
    for value in values:
        total += value
    return total


def multiply_matrices(left: Matrix4, right: Matrix4) -> Matrix4:
    """Compose two column-vector homogeneous transforms."""

    return tuple(
        tuple(
            _ordered_sum(left[row][index] * right[index][column] for index in range(4))
            for column in range(4)
        )
        for row in range(4)
    )  # type: ignore[return-value]


def _normalise_quaternion(values: Sequence[float], *, field: str) -> tuple[float, ...]:
    if len(values) != 4:
        raise ReferenceKinematicsError(f"{field} must contain four components")
    quaternion = tuple(
        _finite(value, field=f"{field}[{index}]")
        for index, value in enumerate(values)
    )
    norm = math.sqrt(_ordered_sum(value * value for value in quaternion))
    if not math.isfinite(norm) or norm <= 1.0e-15:
        raise ReferenceKinematicsError(f"{field} must be non-zero")
    return tuple(value / norm for value in quaternion)


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _rotate_vector_by_quaternion(
    quaternion: Sequence[float], vector: Vector3
) -> Vector3:
    """Rotate one basis vector using cross products, without a quaternion library."""

    q_vector = (quaternion[0], quaternion[1], quaternion[2])
    twice_cross = tuple(2.0 * component for component in _cross(q_vector, vector))
    correction = _cross(q_vector, twice_cross)  # type: ignore[arg-type]
    return tuple(
        vector[index] + quaternion[3] * twice_cross[index] + correction[index]
        for index in range(3)
    )  # type: ignore[return-value]


def matrix_from_translation_quaternion(
    translation: Sequence[float],
    rotation_xyzw: Sequence[float],
    *,
    field: str,
) -> Matrix4:
    """Build a matrix directly from XYZW quaternion components.

    This is kept separate from the fixture generator's case definitions and
    does not call any package or Unreal quaternion helper.
    """

    if len(translation) != 3:
        raise ReferenceKinematicsError(f"{field}.translation_m must contain three components")
    x_translation, y_translation, z_translation = (
        _finite(value, field=f"{field}.translation_m[{index}]")
        for index, value in enumerate(translation)
    )
    quaternion = _normalise_quaternion(
        rotation_xyzw, field=f"{field}.rotation_xyzw"
    )
    # Columns are the images of the canonical basis vectors.  Keeping the
    # matrix in this form makes the oracle's composition rule explicit and
    # independent from the production quaternion-to-matrix implementation.
    columns = tuple(
        _rotate_vector_by_quaternion(quaternion, basis)
        for basis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    )
    return (
        (columns[0][0], columns[1][0], columns[2][0], x_translation),
        (columns[0][1], columns[1][1], columns[2][1], y_translation),
        (columns[0][2], columns[1][2], columns[2][2], z_translation),
        (0.0, 0.0, 0.0, 1.0),
    )


def matrix_from_axis_angle(axis: Sequence[float], angle_radians: float, *, field: str) -> Matrix4:
    """Build a pure rotation matrix with Rodrigues' formula."""

    if len(axis) != 3:
        raise ReferenceKinematicsError(f"{field} must contain three components")
    ax, ay, az = (
        _finite(value, field=f"{field}[{index}]") for index, value in enumerate(axis)
    )
    norm = math.sqrt(ax * ax + ay * ay + az * az)
    if not math.isfinite(norm) or norm <= 1.0e-15:
        raise ReferenceKinematicsError(f"{field} must be non-zero")
    ax, ay, az = ax / norm, ay / norm, az / norm
    angle_radians = _finite(angle_radians, field=f"{field}.angle_radians")
    cosine = math.cos(angle_radians)
    sine = math.sin(angle_radians)
    one_minus_cosine = 1.0 - cosine
    return (
        (
            cosine + ax * ax * one_minus_cosine,
            ax * ay * one_minus_cosine - az * sine,
            ax * az * one_minus_cosine + ay * sine,
            0.0,
        ),
        (
            ay * ax * one_minus_cosine + az * sine,
            cosine + ay * ay * one_minus_cosine,
            ay * az * one_minus_cosine - ax * sine,
            0.0,
        ),
        (
            az * ax * one_minus_cosine - ay * sine,
            az * ay * one_minus_cosine + ax * sine,
            cosine + az * az * one_minus_cosine,
            0.0,
        ),
        (0.0, 0.0, 0.0, 1.0),
    )


def _transform_from_json(value: dict[str, Any], *, field: str) -> Matrix4:
    try:
        translation = value["translation_m"]
        rotation = value["rotation_xyzw"]
    except KeyError as error:
        raise ReferenceKinematicsError(f"{field} is missing {error.args[0]!r}") from error
    return matrix_from_translation_quaternion(translation, rotation, field=field)


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_model(model: dict[str, Any]) -> None:
    if model.get("schema_version") != "dtt.robot-description/0":
        raise ReferenceKinematicsError("unsupported robot description schema")
    if not isinstance(model.get("model_id"), str) or not model["model_id"]:
        raise ReferenceKinematicsError("model_id is required")
    if not isinstance(model.get("model_revision"), str) or not model["model_revision"]:
        raise ReferenceKinematicsError("model_revision is required")
    links = model.get("links")
    joints = model.get("joints")
    if not isinstance(links, list) or not links:
        raise ReferenceKinematicsError("links must be a non-empty array")
    if not isinstance(joints, list):
        raise ReferenceKinematicsError("joints must be an array")
    link_names = [entry.get("name") for entry in links]
    if any(not isinstance(name, str) or not name for name in link_names):
        raise ReferenceKinematicsError("every link needs a name")
    if len(set(link_names)) != len(link_names):
        raise ReferenceKinematicsError("duplicate link name")
    if model.get("root_link") not in link_names:
        raise ReferenceKinematicsError("root link does not exist")
    joint_names: list[str] = []
    child_names: set[str] = set()
    for joint in joints:
        name = joint.get("name")
        if not isinstance(name, str) or not name:
            raise ReferenceKinematicsError("every joint needs a name")
        if name in joint_names:
            raise ReferenceKinematicsError(f"duplicate joint name: {name}")
        joint_names.append(name)
        if joint.get("parent_link") not in link_names or joint.get("child_link") not in link_names:
            raise ReferenceKinematicsError(f"joint {name} references an unknown link")
        child = joint["child_link"]
        if child in child_names:
            raise ReferenceKinematicsError(f"link {child} has multiple parent joints")
        child_names.add(child)
        _transform_from_json(joint["parent_to_joint"], field=f"joint {name}.parent_to_joint")
        joint_type = joint.get("type")
        if joint_type == "revolute":
            axis = joint.get("axis_joint_frame")
            if not isinstance(axis, list) or len(axis) != 3:
                raise ReferenceKinematicsError(f"joint {name} axis is required")
            norm = math.sqrt(_ordered_sum(float(component) ** 2 for component in axis))
            if not math.isfinite(norm) or norm <= 1.0e-15:
                raise ReferenceKinematicsError(f"joint {name} axis is invalid")
        elif joint_type != "fixed":
            raise ReferenceKinematicsError(f"joint {name} has an unsupported type")
    if len(link_names) - len(child_names) != 1:
        raise ReferenceKinematicsError("robot description must have exactly one root")


def load_model(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        model = json.load(stream)
    if not isinstance(model, dict):
        raise ReferenceKinematicsError("robot description must be an object")
    _validate_model(model)
    return model


def _named_positions(values: Sequence[dict[str, Any]]) -> dict[str, float]:
    positions: dict[str, float] = {}
    for entry in values:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ReferenceKinematicsError("joint input name must be non-empty")
        if name in positions:
            raise ReferenceKinematicsError(f"duplicate joint input: {name}")
        raw_value = entry.get("position_rad")
        if raw_value is None:
            raise ReferenceKinematicsError(f"joint input is missing position: {name}")
        positions[name] = _finite(raw_value, field=f"joint {name}.position_rad")
    return positions


def evaluate_forward_kinematics(
    model: dict[str, Any],
    root_pose: dict[str, Any],
    joint_positions_rad: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Matrix4]]:
    """Evaluate generic fixed/revolute tree FK from a named state."""

    _validate_model(model)
    root_matrix = _transform_from_json(root_pose, field="root_pose")
    positions = _named_positions(joint_positions_rad)
    links = [entry["name"] for entry in model["links"]]
    revolute_names = {joint["name"] for joint in model["joints"] if joint["type"] == "revolute"}
    fixed_names = {joint["name"] for joint in model["joints"] if joint["type"] == "fixed"}
    unknown = sorted(set(positions) - revolute_names - fixed_names)
    if unknown:
        raise ReferenceKinematicsError(f"unknown joint input: {unknown[0]}")
    fixed_input = sorted(set(positions) & fixed_names)
    if fixed_input:
        raise ReferenceKinematicsError(
            f"joint input is not allowed for fixed joint: {fixed_input[0]}"
        )
    missing = sorted(revolute_names - set(positions))
    if missing:
        raise ReferenceKinematicsError(f"missing joint input: {missing[0]}")

    by_parent: dict[str, list[dict[str, Any]]] = {name: [] for name in links}
    for joint in model["joints"]:
        by_parent[joint["parent_link"]].append(joint)
    for children in by_parent.values():
        children.sort(key=lambda joint: joint["name"])

    link_matrices: dict[str, Matrix4] = {}
    root_name = model["root_link"]

    def visit(parent_link: str, parent_matrix: Matrix4) -> None:
        link_matrices[parent_link] = parent_matrix
        for joint in by_parent[parent_link]:
            fixed_transform = _transform_from_json(
                joint["parent_to_joint"], field=f"joint {joint['name']}.parent_to_joint"
            )
            motion = identity_matrix()
            if joint["type"] == "revolute":
                motion = matrix_from_axis_angle(
                    joint["axis_joint_frame"],
                    positions[joint["name"]],
                    field=f"joint {joint['name']}.axis_joint_frame",
                )
            child_matrix = multiply_matrices(
                multiply_matrices(parent_matrix, fixed_transform), motion
            )
            visit(joint["child_link"], child_matrix)

    visit(root_name, root_matrix)
    if set(link_matrices) != set(links):
        missing_links = sorted(set(links) - set(link_matrices))
        raise ReferenceKinematicsError(f"disconnected link: {missing_links[0]}")

    tool_matrices: dict[str, Matrix4] = {}
    for tool in model.get("tool_frames", []):
        name = tool["name"]
        link = tool["link"]
        tool_matrices[name] = link_matrices[link]
    return {"links": link_matrices, "tools": tool_matrices}


def _case_definitions(model: dict[str, Any]) -> list[dict[str, Any]]:
    zero = [
        {"name": name, "position_rad": 0.0}
        for name in (
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll",
            "gripper",
        )
    ]
    nonsymmetric = [
        {"name": "shoulder_pan", "position_rad": 0.37},
        {"name": "shoulder_lift", "position_rad": -0.61},
        {"name": "elbow_flex", "position_rad": 0.83},
        {"name": "wrist_flex", "position_rad": -0.29},
        {"name": "wrist_roll", "position_rad": 0.47},
        {"name": "gripper", "position_rad": 0.19},
    ]
    # A quaternion deliberately combines rotations around all three axes.  Its
    # non-identity translation also proves root composition is applied first.
    root_noncommuting = {
        "translation_m": [0.31, -0.22, 0.17],
        "rotation_xyzw": [
            0.21483446221182984,
            -0.3273667995608836,
            0.13299276232160898,
            0.9104889112787075,
        ],
    }
    revolute_limits = {
        joint["name"]: joint["position_limits_rad"]
        for joint in model["joints"]
        if joint["type"] == "revolute"
    }
    if any(limits is None for limits in revolute_limits.values()):
        raise ReferenceKinematicsError("SO-101 fixture cases require revolute position limits")
    lower = [
        {"name": name, "position_rad": revolute_limits[name]["lower"]}
        for name in (
            "shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"
        )
    ]
    upper = [
        {"name": name, "position_rad": revolute_limits[name]["upper"]}
        for name in (
            "shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"
        )
    ]
    return [
        {
            "id": "zero",
            "root_pose": {"translation_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "joint_positions_rad": zero,
        },
        {
            "id": "shoulder_pan_only",
            "root_pose": {"translation_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "joint_positions_rad": [
                {"name": "shoulder_pan", "position_rad": 0.5},
                *zero[1:],
            ],
        },
        {
            "id": "shoulder_and_elbow",
            "root_pose": {"translation_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "joint_positions_rad": [
                {"name": "shoulder_pan", "position_rad": 0.0},
                {"name": "shoulder_lift", "position_rad": 0.4},
                {"name": "elbow_flex", "position_rad": -0.6},
                *zero[3:],
            ],
        },
        {
            "id": "multi_joint_nonsymmetric",
            "root_pose": {"translation_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "joint_positions_rad": nonsymmetric,
        },
        {
            "id": "joint_limits_lower",
            "root_pose": {"translation_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "joint_positions_rad": lower,
        },
        {
            "id": "joint_limits_upper",
            "root_pose": {"translation_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "joint_positions_rad": upper,
        },
        {
            "id": "tool_fixed",
            "root_pose": {"translation_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "joint_positions_rad": [
                *zero[:-1],
                {"name": "gripper", "position_rad": 0.37},
            ],
        },
        {
            "id": "root_transform_noncommuting",
            "root_pose": root_noncommuting,
            "joint_positions_rad": nonsymmetric,
        },
        {
            "id": "reordered_joint_positions",
            "root_pose": {"translation_m": [0.0, 0.0, 0.0], "rotation_xyzw": [0.0, 0.0, 0.0, 1.0]},
            "joint_positions_rad": list(reversed(nonsymmetric)),
        },
    ]


def _serialise_matrix(matrix: Matrix4) -> list[list[float]]:
    return [[float(component) for component in row] for row in matrix]


def build_fixture(model_path: Path, *, repository_root: Path | None = None) -> dict[str, Any]:
    model = load_model(model_path)
    if repository_root is None:
        repository_root = Path(__file__).resolve().parents[4]
    cases: list[dict[str, Any]] = []
    for definition in _case_definitions(model):
        result = evaluate_forward_kinematics(
            model,
            definition["root_pose"],
            definition["joint_positions_rad"],
        )
        cases.append(
            {
                **definition,
                "expected": {
                    "links": [
                        {"name": name, "matrix": _serialise_matrix(matrix)}
                        for name, matrix in result["links"].items()
                    ],
                    "tools": [
                        {"name": name, "matrix": _serialise_matrix(matrix)}
                        for name, matrix in result["tools"].items()
                    ],
                },
            }
        )
    return {
        "schema_version": REFERENCE_FIXTURE_SCHEMA,
        "model": {
            "path": _relative_path(model_path, repository_root),
            "model_id": model["model_id"],
            "model_revision": model["model_revision"],
            "description_sha256": _sha256(model_path),
            "source_git_blob_sha1": model["source"]["git_blob_sha1"],
        },
        "generator": {
            "name": GENERATOR_NAME,
            "version": GENERATOR_VERSION,
            "source_sha256": _sha256(Path(__file__)),
        },
        "tolerances": {
            "position_m": POSITION_TOLERANCE_METRES,
            "rotation": ROTATION_TOLERANCE,
            "rationale": (
                "Separate metre translation and unitless rotation-entry tolerances; "
                "both are below the M2 fixture precision budget."
            ),
        },
        "cases": cases,
    }


def serialise_fixture(fixture: dict[str, Any]) -> str:
    return json.dumps(fixture, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def render_fixture(model_path: Path) -> str:
    return serialise_fixture(build_fixture(model_path))


def check_fixture(output_path: Path, model_path: Path) -> tuple[bool, str]:
    if not output_path.exists():
        return False, f"fixture does not exist: {output_path}"
    expected = render_fixture(model_path)
    actual = output_path.read_text(encoding="utf-8")
    if actual != expected:
        return False, f"fixture drift detected: {output_path}"
    return True, f"fixture is current: {output_path}"


def _default_repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main(argv: list[str] | None = None) -> int:
    repository_root = _default_repository_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        type=Path,
        default=repository_root / "robots/so101/generated/so101.kinematics.json",
        help="canonical generated robot description",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repository_root / "fixtures/m2/kinematics/so101-fk.json",
        help="human-reviewable cross-language fixture file",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed fixture differs from a fresh independent generation",
    )
    args = parser.parse_args(argv)
    if args.check:
        current, message = check_fixture(args.output, args.model)
        print(message)
        return 0 if current else 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_fixture(args.model), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
