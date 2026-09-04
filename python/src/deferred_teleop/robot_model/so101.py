"""Generate the canonical SO-101 structural description from a pinned URDF.

This module is deliberately a development-time tool. Unreal runtime code consumes the
small generated JSON description; it does not parse arbitrary URDF/XML.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

DESCRIPTION_SCHEMA_VERSION = "dtt.robot-description/0"
SOURCE_LOCK_SCHEMA_VERSION = "dtt.source-lock/0"


def git_blob_sha1(data: bytes) -> str:
    """Return the SHA-1 used by Git for a blob containing *data*."""

    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()  # noqa: S324 - Git object identity


def _parse_vector(
    raw: str | None,
    *,
    default: tuple[float, float, float],
    field_name: str,
) -> list[float]:
    if raw is None:
        values = list(default)
    else:
        parts = raw.split()
        if len(parts) != 3:
            raise ValueError(f"{field_name} must contain exactly three values")
        values = [float(part) for part in parts]

    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{field_name} contains a non-finite value")
    return values


def _quaternion_multiply(left: list[float], right: list[float]) -> list[float]:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return [
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    ]


def _rpy_to_quaternion(roll: float, pitch: float, yaw: float) -> list[float]:
    """Convert URDF fixed-axis roll/pitch/yaw to an XYZW quaternion.

    With column vectors this is the active rotation ``Rz(yaw) Ry(pitch) Rx(roll)``.
    """

    half_roll = 0.5 * roll
    half_pitch = 0.5 * pitch
    half_yaw = 0.5 * yaw

    qx = [math.sin(half_roll), 0.0, 0.0, math.cos(half_roll)]
    qy = [0.0, math.sin(half_pitch), 0.0, math.cos(half_pitch)]
    qz = [0.0, 0.0, math.sin(half_yaw), math.cos(half_yaw)]
    quaternion = _quaternion_multiply(qz, _quaternion_multiply(qy, qx))

    norm = math.sqrt(sum(component * component for component in quaternion))
    if not math.isfinite(norm) or norm <= 1e-15:
        raise ValueError("origin rotation produced an invalid quaternion")
    quaternion = [component / norm for component in quaternion]

    # q and -q encode the same rotation. A stable hemisphere keeps generated JSON deterministic.
    if quaternion[3] < 0.0:
        quaternion = [-component for component in quaternion]
    return quaternion


def _parse_origin(element: ET.Element | None, *, field_name: str) -> dict[str, list[float]]:
    if element is None:
        xyz = [0.0, 0.0, 0.0]
        rpy = [0.0, 0.0, 0.0]
    else:
        xyz = _parse_vector(
            element.get("xyz"),
            default=(0.0, 0.0, 0.0),
            field_name=f"{field_name}.xyz",
        )
        rpy = _parse_vector(
            element.get("rpy"),
            default=(0.0, 0.0, 0.0),
            field_name=f"{field_name}.rpy",
        )

    return {
        "translation_m": xyz,
        "rotation_xyzw": _rpy_to_quaternion(*rpy),
    }


def _normalise_axis(raw: str | None, *, joint_name: str) -> list[float]:
    axis = _parse_vector(
        raw,
        default=(1.0, 0.0, 0.0),
        field_name=f"joint {joint_name} axis",
    )
    norm = math.sqrt(sum(component * component for component in axis))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError(f"revolute joint {joint_name} has a zero or invalid axis")
    return [component / norm for component in axis]


def _load_source_lock(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        lock = tomllib.load(stream)

    if lock.get("schema_version") != SOURCE_LOCK_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported source-lock schema: {lock.get('schema_version')!r}"
        )
    for section in ("source", "vendor", "model"):
        if section not in lock or not isinstance(lock[section], dict):
            raise ValueError(f"source lock is missing [{section}]")
    return lock


def _require_unique(values: list[str], *, kind: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"duplicate {kind} names: {', '.join(duplicates)}")


def _parse_visuals(link: ET.Element, *, link_name: str) -> list[dict[str, Any]]:
    visuals: list[dict[str, Any]] = []
    for index, visual in enumerate(link.findall("visual")):
        mesh = visual.find("./geometry/mesh")
        if mesh is None:
            continue
        filename = mesh.get("filename")
        if not filename:
            raise ValueError(f"visual {index} on link {link_name} has no mesh filename")
        material = visual.find("material")
        visuals.append(
            {
                "visual_id": f"{link_name}.visual.{index}",
                "source_mesh": filename,
                "material": material.get("name") if material is not None else None,
                "link_to_visual": _parse_origin(
                    visual.find("origin"),
                    field_name=f"link {link_name} visual {index} origin",
                ),
            }
        )
    return visuals


def _topological_order(
    *,
    link_names: list[str],
    raw_joints: list[dict[str, Any]],
) -> tuple[str, list[str], list[dict[str, Any]]]:
    child_links = [joint["child_link"] for joint in raw_joints]
    roots = sorted(set(link_names) - set(child_links))
    if len(roots) != 1:
        raise ValueError(f"robot description must have exactly one root link, found {roots}")
    root_link = roots[0]

    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for joint in raw_joints:
        by_parent[joint["parent_link"]].append(joint)
    for joints in by_parent.values():
        joints.sort(key=lambda item: item["name"])

    ordered_links: list[str] = []
    ordered_joints: list[dict[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(link_name: str) -> None:
        if link_name in visiting:
            raise ValueError(f"cycle detected at link {link_name}")
        if link_name in visited:
            raise ValueError(f"link {link_name} is reachable through multiple parents")

        visiting.add(link_name)
        ordered_links.append(link_name)
        for joint in by_parent.get(link_name, []):
            ordered_joints.append(joint)
            visit(joint["child_link"])
        visiting.remove(link_name)
        visited.add(link_name)

    visit(root_link)
    missing = sorted(set(link_names) - visited)
    if missing:
        raise ValueError(f"disconnected links: {', '.join(missing)}")
    return root_link, ordered_links, ordered_joints


def generate_so101_description(source_path: Path, lock_path: Path) -> dict[str, Any]:
    """Parse and validate the pinned SO-101 URDF into canonical runtime data."""

    lock = _load_source_lock(lock_path)
    source_bytes = source_path.read_bytes()
    actual_blob_sha1 = git_blob_sha1(source_bytes)
    expected_blob_sha1 = str(lock["source"].get("git_blob_sha1", ""))
    if actual_blob_sha1 != expected_blob_sha1:
        raise ValueError(
            "vendored SO-101 source hash mismatch: "
            f"expected {expected_blob_sha1}, got {actual_blob_sha1}"
        )

    root = ET.fromstring(source_bytes)
    if root.tag != "robot":
        raise ValueError(f"expected <robot> root, got <{root.tag}>")

    link_elements = root.findall("link")
    link_names = [link.get("name", "") for link in link_elements]
    if any(not name for name in link_names):
        raise ValueError("every link requires a non-empty name")
    _require_unique(link_names, kind="link")
    link_name_set = set(link_names)

    raw_joints: list[dict[str, Any]] = []
    joint_names: list[str] = []
    child_to_joint: dict[str, str] = {}

    for joint in root.findall("joint"):
        name = joint.get("name", "")
        joint_type = joint.get("type", "")
        if not name:
            raise ValueError("every joint requires a non-empty name")
        if joint_type not in {"fixed", "revolute"}:
            raise ValueError(f"unsupported joint type {joint_type!r} for {name}")

        parent = joint.find("parent")
        child = joint.find("child")
        if parent is None or child is None:
            raise ValueError(f"joint {name} requires parent and child elements")
        parent_link = parent.get("link", "")
        child_link = child.get("link", "")
        if parent_link not in link_name_set or child_link not in link_name_set:
            raise ValueError(f"joint {name} references an unknown link")
        if child_link in child_to_joint:
            raise ValueError(
                f"link {child_link} has multiple parent joints: "
                f"{child_to_joint[child_link]} and {name}"
            )
        child_to_joint[child_link] = name

        axis: list[float] | None = None
        position_limits_rad: dict[str, float] | None = None
        if joint_type == "revolute":
            axis_element = joint.find("axis")
            axis = _normalise_axis(
                axis_element.get("xyz") if axis_element is not None else None,
                joint_name=name,
            )
            limit = joint.find("limit")
            if limit is None or limit.get("lower") is None or limit.get("upper") is None:
                raise ValueError(f"revolute joint {name} requires lower and upper limits")
            lower = float(limit.get("lower", "nan"))
            upper = float(limit.get("upper", "nan"))
            if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
                raise ValueError(f"joint {name} has invalid position limits")
            position_limits_rad = {"lower": lower, "upper": upper}

        joint_names.append(name)
        raw_joints.append(
            {
                "name": name,
                "type": joint_type,
                "parent_link": parent_link,
                "child_link": child_link,
                "parent_to_joint": _parse_origin(
                    joint.find("origin"),
                    field_name=f"joint {name} origin",
                ),
                "axis_joint_frame": axis,
                "position_limits_rad": position_limits_rad,
            }
        )

    _require_unique(joint_names, kind="joint")
    root_link, ordered_link_names, ordered_joints = _topological_order(
        link_names=link_names,
        raw_joints=raw_joints,
    )

    configured_root = str(lock["model"].get("root_link", ""))
    if root_link != configured_root:
        raise ValueError(f"source root {root_link!r} does not match lock {configured_root!r}")

    links_by_name = {link.get("name", ""): link for link in link_elements}
    ordered_links = [
        {
            "name": link_name,
            "visuals": _parse_visuals(links_by_name[link_name], link_name=link_name),
        }
        for link_name in ordered_link_names
    ]

    tool_frames = [str(value) for value in lock["model"].get("tool_frames", [])]
    arm_group = [str(value) for value in lock["model"].get("arm_joint_group", [])]
    gripper_group = [str(value) for value in lock["model"].get("gripper_joint_group", [])]

    unknown_tools = sorted(set(tool_frames) - link_name_set)
    unknown_group_joints = sorted((set(arm_group) | set(gripper_group)) - set(joint_names))
    overlap = sorted(set(arm_group) & set(gripper_group))
    if unknown_tools:
        raise ValueError(f"unknown configured tool frames: {', '.join(unknown_tools)}")
    if unknown_group_joints:
        raise ValueError(f"unknown configured group joints: {', '.join(unknown_group_joints)}")
    if overlap:
        raise ValueError(f"joint groups overlap: {', '.join(overlap)}")

    source = lock["source"]
    return {
        "schema_version": DESCRIPTION_SCHEMA_VERSION,
        "model_id": str(lock.get("model_id", root.get("name", ""))),
        "model_revision": f"git:{source['commit']}:{actual_blob_sha1}",
        "source": {
            "repository": str(source["repository"]),
            "commit": str(source["commit"]),
            "path": str(source["path"]),
            "git_blob_sha1": actual_blob_sha1,
            "licence": str(source["licence"]),
            "vendor_modified": bool(lock["vendor"].get("modified", False)),
        },
        "coordinate_convention": {
            "handedness": "RIGHT_HANDED",
            "up_axis": "Z",
            "length_unit": "metre",
            "angle_unit": "radian",
            "rotation_representation": "quaternion_xyzw",
            "transform_notation": "parent_T_child",
        },
        "root_link": root_link,
        "links": ordered_links,
        "joints": ordered_joints,
        "joint_groups": [
            {"name": "arm", "joints": arm_group},
            {"name": "gripper", "joints": gripper_group},
        ],
        "tool_frames": [{"name": frame, "link": frame} for frame in tool_frames],
        "known_limitations": [str(lock["notes"]["gripper_mapping"])],
    }


def serialize_description(description: dict[str, Any]) -> str:
    """Serialize canonical data deterministically for review and drift checks."""

    return json.dumps(description, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _default_repo_path(relative: str) -> Path:
    return Path(relative)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=_default_repo_path("robots/so101/upstream/so101_new_calib.urdf"),
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=_default_repo_path("robots/so101/source-lock.toml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_default_repo_path("robots/so101/generated/so101.kinematics.json"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the committed generated file differs from a fresh generation.",
    )
    args = parser.parse_args(argv)

    rendered = serialize_description(generate_so101_description(args.source, args.lock))
    if args.check:
        if not args.output.exists():
            parser.error(f"generated output does not exist: {args.output}")
        if args.output.read_text(encoding="utf-8") != rendered:
            parser.error(f"generated output is stale: {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
