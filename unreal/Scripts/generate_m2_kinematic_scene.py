"""Generate a local, primitive-only M2.5 SO-101 kinematic scene.

The recipe is intentionally idempotent and follows the M1 editor-generator
pattern.  It creates presentation assets in the M2 folder, then initializes
three independent actor instances from the committed canonical description.
The three semantic layers and the deterministic non-zero state are synthetic
demonstration inputs for visual review; they are not measured telemetry,
arrival prediction, or an operational target UI.
Generated assets are editor products and are not required in source control.
"""

from __future__ import annotations

import os

import unreal

PLUGIN_ROOT = "/DeferredTeleop"
MATERIAL_ROOT = f"{PLUGIN_ROOT}/M2"
EXAMPLE_ROOT = f"{PLUGIN_ROOT}/M2"
BLUEPRINT_PATH = f"{EXAMPLE_ROOT}/BP_M2KinematicSO101"
BLUEPRINT_OBJECT_PATH = f"{BLUEPRINT_PATH}.BP_M2KinematicSO101"
LEVEL_PATH = f"{EXAMPLE_ROOT}/M2_KinematicSO101"
LEVEL_OBJECT_PATH = f"{LEVEL_PATH}.M2_KinematicSO101"


def _create_material(name: str, color: unreal.LinearColor, opacity: float) -> unreal.Material:
    if not unreal.EditorAssetLibrary.does_directory_exist(MATERIAL_ROOT):
        if not unreal.EditorAssetLibrary.make_directory(MATERIAL_ROOT):
            raise RuntimeError(f"could not create asset directory {MATERIAL_ROOT}")
    asset_path = f"{MATERIAL_ROOT}/{name}"
    material = unreal.load_asset(f"{asset_path}.{name}")
    if material is not None and not isinstance(material, unreal.Material):
        raise RuntimeError(f"{asset_path} exists but is not a Material")
    if material is None:
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name,
            MATERIAL_ROOT,
            unreal.Material,
            unreal.MaterialFactoryNew(),
        )
    if not isinstance(material, unreal.Material):
        raise RuntimeError(f"could not create {asset_path}")

    material.set_editor_property(
        "blend_mode",
        unreal.BlendMode.BLEND_OPAQUE
        if opacity >= 1.0
        else unreal.BlendMode.BLEND_TRANSLUCENT,
    )
    material.set_editor_property("two_sided", True)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)

    emissive = unreal.MaterialEditingLibrary.get_material_property_input_node(
        material,
        unreal.MaterialProperty.MP_EMISSIVE_COLOR,
    )
    opacity_node = (
        unreal.MaterialEditingLibrary.get_material_property_input_node(
            material,
            unreal.MaterialProperty.MP_OPACITY,
        )
        if opacity < 1.0
        else None
    )
    if emissive is None or (opacity < 1.0 and opacity_node is None):
        unreal.MaterialEditingLibrary.delete_all_material_expressions(material)
        emissive = unreal.MaterialEditingLibrary.create_material_expression(
            material,
            unreal.MaterialExpressionConstant3Vector,
            -300,
            0,
        )
        unreal.MaterialEditingLibrary.connect_material_property(
            emissive,
            "",
            unreal.MaterialProperty.MP_BASE_COLOR,
        )
        unreal.MaterialEditingLibrary.connect_material_property(
            emissive,
            "",
            unreal.MaterialProperty.MP_EMISSIVE_COLOR,
        )
        if opacity < 1.0:
            opacity_node = unreal.MaterialEditingLibrary.create_material_expression(
                material,
                unreal.MaterialExpressionConstant,
                -300,
                180,
            )
            unreal.MaterialEditingLibrary.connect_material_property(
                opacity_node,
                "",
                unreal.MaterialProperty.MP_OPACITY,
            )
    emissive.set_editor_property("constant", color)
    if opacity_node is not None:
        opacity_node.set_editor_property("r", opacity)
    errors = unreal.MaterialEditingLibrary.recompile_material(material)
    if errors:
        raise RuntimeError(f"material {name} failed to compile: {errors}")
    unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    return material


def _create_blueprint(materials: dict[str, unreal.Material]) -> unreal.Blueprint:
    if not unreal.EditorAssetLibrary.does_directory_exist(EXAMPLE_ROOT):
        if not unreal.EditorAssetLibrary.make_directory(EXAMPLE_ROOT):
            raise RuntimeError(f"could not create asset directory {EXAMPLE_ROOT}")
    blueprint = unreal.load_asset(BLUEPRINT_OBJECT_PATH)
    if blueprint is None:
        parent_class = unreal.load_class(
            None,
            "/Script/DeferredTeleopRuntime.DeferredTeleopKinematicRobotActor",
        )
        if parent_class is None:
            raise RuntimeError("DeferredTeleopKinematicRobotActor class is unavailable")
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("parent_class", parent_class)
        blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            "BP_M2KinematicSO101",
            EXAMPLE_ROOT,
            unreal.Blueprint,
            factory,
        )
    if blueprint is None or not hasattr(blueprint, "generated_class"):
        raise RuntimeError(f"{BLUEPRINT_PATH} is not a usable Blueprint")

    defaults = unreal.get_default_object(blueprint.generated_class())
    defaults.set_editor_property("link_material", materials["confirmed"])
    defaults.set_editor_property("tool_material", materials["tool"])
    defaults.set_editor_property("segment_material", materials["segment"])
    defaults.set_editor_property("show_debug_names", True)
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)
    return blueprint


def _load_description() -> object:
    project_dir = unreal.Paths.project_dir()
    description_path = os.path.normpath(
        os.path.join(
            project_dir,
            "..",
            "..",
            "robots",
            "so101",
            "generated",
            "so101.kinematics.json",
        )
    )
    with open(description_path, encoding="utf-8") as stream:
        json_text = stream.read()

    parsed = unreal.DeferredTeleopKinematicsLibrary.parse_robot_description_json(json_text)
    if parsed is None:
        raise RuntimeError("SO-101 description parser rejected the generated model")
    if not isinstance(parsed, tuple) or len(parsed) != 2:
        raise RuntimeError("SO-101 description parser returned an unexpected value")
    description, error = parsed
    if description is None:
        raise RuntimeError(str(error) or "SO-101 description parser rejected the generated model")
    if error:
        raise RuntimeError(str(error))
    return description


def _make_root(x: float, y: float) -> object:
    root = unreal.DttCanonicalTransform()
    translation = unreal.DttCanonicalVector()
    translation.x = x
    translation.y = y
    translation.z = 0.0
    root.translation_metres = translation
    rotation = unreal.DttCanonicalQuaternion()
    rotation.x = 0.0
    rotation.y = 0.0
    rotation.z = 0.0
    rotation.w = 1.0
    root.rotation = rotation
    return root


def _make_state(description: object) -> list[object]:
    state = []
    revolute_index = 0
    revolute_type = unreal.DttRobotJointType.REVOLUTE
    for joint in description.joints:
        if joint.type != revolute_type:
            continue
        position = 0.04 * (revolute_index + 1)
        if joint.has_position_limits:
            midpoint = 0.5 * (joint.lower_position_radians + joint.upper_position_radians)
            margin = 0.2 * (joint.upper_position_radians - joint.lower_position_radians)
            position = max(
                joint.lower_position_radians + margin,
                min(midpoint + position, joint.upper_position_radians - margin),
            )
        item = unreal.DttNamedJointPosition()
        item.joint_name = joint.name
        item.position_radians = position
        state.append(item)
        revolute_index += 1
    if not state:
        raise RuntimeError("SO-101 description contains no revolute joints")
    return state


def _call_with_error(callable_object, *args) -> None:
    result = callable_object(*args)
    if result is None:
        raise RuntimeError("kinematic actor rejected generated input")
    if not isinstance(result, str):
        raise RuntimeError("kinematic actor returned an unexpected error value")
    if result:
        raise RuntimeError(result)


def _ensure_camera_and_light(actors: unreal.EditorActorSubsystem) -> None:
    level_actors = actors.get_all_level_actors()
    cameras = [actor for actor in level_actors if actor.get_actor_label() == "M2 Kinematic Camera"]
    if not cameras:
        camera = actors.spawn_actor_from_class(
            unreal.CameraActor,
            unreal.Vector(-420.0, 0.0, 220.0),
            # Unreal's Python binding orders Rotator arguments as roll, pitch,
            # yaw.  The camera must pitch down to centre the three robots.
            unreal.Rotator(0.0, -22.0, 0.0),
            False,
        )
        if camera is None:
            raise RuntimeError("could not place M2 Kinematic Camera")
        camera.set_actor_label("M2 Kinematic Camera")
    else:
        camera = cameras[0]
    # Reapply the review framing for an already generated level as well.
    camera.set_actor_location(unreal.Vector(-420.0, 0.0, 220.0), False, False)
    camera.set_actor_rotation(unreal.Rotator(0.0, -22.0, 0.0), False)
    camera.camera_component.set_editor_property("field_of_view", 55.0)

    lights = [actor for actor in level_actors if actor.get_actor_label() == "M2 Kinematic Light"]
    if not lights:
        light = actors.spawn_actor_from_class(
            unreal.PointLight,
            unreal.Vector(-160.0, 0.0, 360.0),
            unreal.Rotator(0.0, 0.0, 0.0),
            False,
        )
        if light is None:
            raise RuntimeError("could not place M2 Kinematic Light")
        light.set_actor_label("M2 Kinematic Light")
    else:
        light = lights[0]
    light.set_actor_location(unreal.Vector(-160.0, 0.0, 360.0), False, False)
    light.point_light_component.set_editor_property("intensity", 12000.0)
    light.point_light_component.set_editor_property("attenuation_radius", 1800.0)


def _ensure_synthetic_legends(actors: unreal.EditorActorSubsystem) -> None:
    """Make the synthetic demonstration status visible in the captured view."""
    legends = [
        (
            "M2 Synthetic Legend Confirmed",
            "SYNTHETIC<br>DEMONSTRATION<br>CONFIRMED",
            unreal.Vector(20.0, 180.0, 90.0),
            unreal.Color(220, 225, 235, 255),
        ),
        (
            "M2 Synthetic Legend Arrival",
            "SYNTHETIC<br>DEMONSTRATION<br>ARRIVAL",
            unreal.Vector(20.0, 0.0, 90.0),
            unreal.Color(210, 210, 220, 255),
        ),
        (
            "M2 Synthetic Legend Target",
            "SYNTHETIC<br>DEMONSTRATION<br>TARGET",
            unreal.Vector(20.0, -180.0, 90.0),
            unreal.Color(80, 150, 255, 255),
        ),
    ]
    existing = {actor.get_actor_label(): actor for actor in actors.get_all_level_actors()}
    for label, text, location, color in legends:
        legend = existing.get(label)
        if legend is None:
            legend = actors.spawn_actor_from_class(
                unreal.TextRenderActor,
                location,
                unreal.Rotator(0.0, 0.0, 180.0),
                False,
            )
            if legend is None:
                raise RuntimeError(f"could not place synthetic legend {label}")
            legend.set_actor_label(label)
        legend.set_actor_location(location, False, False)
        legend.set_actor_rotation(unreal.Rotator(0.0, 0.0, 180.0), False)
        text_render = legend.get_editor_property("text_render")
        text_render.set_text(text)
        text_render.set_world_size(6.0)
        text_render.set_text_render_color(color)
        # TextRenderActor carries an editor billboard icon.  Hide only that
        # icon so the viewport proof contains the text, not authoring chrome.
        for component in legend.get_components_by_class(unreal.BillboardComponent):
            component.set_visibility(False)


def _create_level(
    blueprint: unreal.Blueprint,
    description: object,
    materials: dict[str, unreal.Material],
) -> None:
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if unreal.load_asset(LEVEL_OBJECT_PATH) is not None:
        if not levels.load_level(LEVEL_PATH):
            raise RuntimeError(f"could not load {LEVEL_PATH}")
    elif not levels.new_level(LEVEL_PATH, False):
        raise RuntimeError(f"could not create {LEVEL_PATH}")

    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    generated_class = blueprint.generated_class()
    robot_actors = [
        actor for actor in actors.get_all_level_actors() if actor.get_class() == generated_class
    ]
    while len(robot_actors) < 3:
        actor = actors.spawn_actor_from_class(
            generated_class,
            unreal.Vector(0.0, 0.0, 0.0),
            unreal.Rotator(0.0, 0.0, 0.0),
            False,
        )
        if actor is None:
            raise RuntimeError("could not place M2 kinematic actor")
        robot_actors.append(actor)

    # This deterministic non-zero vector is synthetic demonstration data.  It
    # intentionally carries no measurement, prediction, or UI provenance.
    state = _make_state(description)
    layer_enum = unreal.DeferredTeleopKinematicSemanticLayer
    layers = [layer_enum.CONFIRMED, layer_enum.ARRIVAL, layer_enum.TARGET]
    layer_materials = [materials["confirmed"], materials["arrival"], materials["target"]]
    labels = [
        "M2 SYNTHETIC CONFIRMED SO101",
        "M2 SYNTHETIC ARRIVAL SO101",
        "M2 SYNTHETIC TARGET SO101",
    ]
    # Canonical Y offsets become Unreal -Y offsets at the one conversion
    # boundary, so these instances are visibly side-by-side in the scene.
    canonical_y_offsets = [-1.8, 0.0, 1.8]
    for index, actor in enumerate(robot_actors[:3]):
        actor.set_editor_property("semantic_layer", layers[index])
    # The caller maps its explicit semantic value to presentation.  The
    # actor itself never derives provenance from this material.
        actor.set_editor_property("link_material", layer_materials[index])
        # The dedicated synthetic legend is readable at capture scale; the
        # per-link names would otherwise obscure the axes and joints.
        actor.set_editor_property("show_debug_names", False)
        actor.set_actor_label(labels[index])
        _call_with_error(
            actor.initialize_model, description, _make_root(0.0, canonical_y_offsets[index])
        )
        _call_with_error(actor.apply_state, state)
        actor.set_debug_frames_visible(True)

    _ensure_camera_and_light(actors)
    _ensure_synthetic_legends(actors)
    if not levels.save_current_level():
        raise RuntimeError(f"could not save {LEVEL_PATH}")


def main() -> None:
    materials = {
        "confirmed": _create_material(
            "M_M2KinematicConfirmed",
            unreal.LinearColor(0.16, 0.18, 0.22, 1.0),
            1.0,
        ),
        "arrival": _create_material(
            "M_M2KinematicArrival",
            unreal.LinearColor(0.75, 0.75, 0.78, 1.0),
            0.72,
        ),
        "target": _create_material(
            "M_M2KinematicTarget",
            unreal.LinearColor(0.02, 0.18, 1.0, 1.0),
            0.72,
        ),
        "tool": _create_material(
            "M_M2KinematicTool",
            unreal.LinearColor(0.95, 0.10, 0.75, 1.0),
            1.0,
        ),
        "segment": _create_material(
            "M_M2KinematicSegment",
            unreal.LinearColor(0.12, 0.65, 1.0, 1.0),
            0.72,
        ),
    }
    description = _load_description()
    blueprint = _create_blueprint(materials)
    _create_level(blueprint, description, materials)
    unreal.log("M2.5 kinematic SO-101 scene generated successfully")


main()
