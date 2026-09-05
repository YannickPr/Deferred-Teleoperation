"""Generate a local, primitive-only M2.9a articulated scene.

The recipe creates three persistent kinematic actors and one articulated scene
controller.  It configures the controller with an explicit local description,
replays the checked-in three-layer fixture through the production scene API,
and labels the result ``FIXTURE REPLAY / SYNTHETIC DEMONSTRATION``.  The
fixture's declared provenance values are preserved in the runtime status; the
label makes clear that the capture is not live telemetry.

The recipe is editor-only and desktop-only.  It does not create VR, hardware,
IK, skeletal, or Target-authoring assets.
"""

from __future__ import annotations

import os

import unreal

PLUGIN_ROOT = "/DeferredTeleop"
SCENE_ROOT = f"{PLUGIN_ROOT}/M2"
ROBOT_BLUEPRINT_PATH = f"{SCENE_ROOT}/BP_M2ArticulatedSO101"
ROBOT_BLUEPRINT_OBJECT_PATH = f"{ROBOT_BLUEPRINT_PATH}.BP_M2ArticulatedSO101"
SCENE_BLUEPRINT_PATH = f"{SCENE_ROOT}/BP_M2ArticulatedScene"
SCENE_BLUEPRINT_OBJECT_PATH = f"{SCENE_BLUEPRINT_PATH}.BP_M2ArticulatedScene"
LEVEL_PATH = f"{SCENE_ROOT}/M2_9a_ArticulatedSO101"
LEVEL_OBJECT_PATH = f"{LEVEL_PATH}.M2_9a_ArticulatedSO101"


def _call_with_error(callable_object, *args) -> None:
    """Handle Unreal Python's out-parameter convention.

    A failed bool+outs call returns ``None``.  A successful call returns a
    tuple containing only out values, so the bool return value is not treated
    as an error string.
    """

    result = callable_object(*args)
    if result is None:
        raise RuntimeError("Unreal callable rejected its input")
    if isinstance(result, tuple):
        values = [value for value in result if not isinstance(value, bool)]
        errors = [value for value in values if isinstance(value, str) and value]
        if errors:
            raise RuntimeError(errors[-1])
        return
    if isinstance(result, str):
        if result:
            raise RuntimeError(result)
        return
    if result is False:
        raise RuntimeError("Unreal callable returned failure")


def _create_material(name: str, color: unreal.LinearColor) -> unreal.Material:
    if not unreal.EditorAssetLibrary.does_directory_exist(SCENE_ROOT):
        if not unreal.EditorAssetLibrary.make_directory(SCENE_ROOT):
            raise RuntimeError(f"could not create asset directory {SCENE_ROOT}")
    path = f"{SCENE_ROOT}/{name}"
    material = unreal.load_asset(f"{path}.{name}")
    if material is None:
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name,
            SCENE_ROOT,
            unreal.Material,
            unreal.MaterialFactoryNew(),
        )
    if not isinstance(material, unreal.Material):
        raise RuntimeError(f"{path} is not a Material")
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", True)
    unreal.MaterialEditingLibrary.delete_all_material_expressions(material)
    expression = unreal.MaterialEditingLibrary.create_material_expression(
        material,
        unreal.MaterialExpressionConstant3Vector,
        -300,
        0,
    )
    expression.set_editor_property("constant", color)
    unreal.MaterialEditingLibrary.connect_material_property(
        expression,
        "",
        unreal.MaterialProperty.MP_BASE_COLOR,
    )
    unreal.MaterialEditingLibrary.connect_material_property(
        expression,
        "",
        unreal.MaterialProperty.MP_EMISSIVE_COLOR,
    )
    errors = unreal.MaterialEditingLibrary.recompile_material(material)
    if errors:
        raise RuntimeError(f"material {name} failed to compile: {errors}")
    unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    return material


def _create_blueprint(path: str, name: str, parent_path: str) -> unreal.Blueprint:
    if not unreal.EditorAssetLibrary.does_directory_exist(SCENE_ROOT):
        if not unreal.EditorAssetLibrary.make_directory(SCENE_ROOT):
            raise RuntimeError(f"could not create asset directory {SCENE_ROOT}")
    blueprint = unreal.load_asset(f"{path}.{name}")
    if blueprint is None:
        parent_class = unreal.load_class(None, parent_path)
        if parent_class is None:
            raise RuntimeError(f"class is unavailable: {parent_path}")
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("parent_class", parent_class)
        blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name,
            SCENE_ROOT,
            unreal.Blueprint,
            factory,
        )
    if blueprint is None or not hasattr(blueprint, "generated_class"):
        raise RuntimeError(f"{path} is not a usable Blueprint")
    return blueprint


def _description_path() -> str:
    return os.path.normpath(
        os.path.join(
            unreal.Paths.project_dir(),
            "..",
            "..",
            "robots",
            "so101",
            "generated",
            "so101.kinematics.json",
        )
    )


def _fixture_path() -> str:
    return os.path.normpath(
        os.path.join(
            unreal.Paths.project_dir(),
            "..",
            "..",
            "fixtures",
            "m2",
            "articulated-state",
            "valid-articulated-view.json",
        )
    )


def _make_binding() -> object:
    binding = unreal.DeferredTeleopArticulatedModelBinding()
    binding.robot_id = "so101-follower-1"
    binding.description_file_path = _description_path()
    binding.expected_frame_id = "field-world"
    binding.expected_calibration_version = "field-cal-1"
    return binding


def _configure_robot_defaults(
    blueprint: unreal.Blueprint,
    materials: dict[str, unreal.Material],
) -> None:
    defaults = unreal.get_default_object(blueprint.generated_class())
    defaults.set_editor_property("link_material", materials["confirmed"])
    defaults.set_editor_property("tool_material", materials["tool"])
    defaults.set_editor_property("segment_material", materials["segment"])
    defaults.set_editor_property("show_debug_names", False)
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)


def _configure_scene_defaults(blueprint: unreal.Blueprint) -> None:
    defaults = unreal.get_default_object(blueprint.generated_class())
    mission_client = defaults.get_editor_property("mission_client")
    mission_client.set_editor_property(
        "wire_mode",
        unreal.DeferredTeleopMissionWireMode.ARTICULATED_VIEW,
    )
    mission_client.set_editor_property("auto_connect", False)
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)


def _configure_scene_binding(scene: object, binding: object) -> None:
    try:
        _call_with_error(scene.configure_binding, binding)
    except RuntimeError as configuration_error:
        # Re-running the editor recipe against an already configured level
        # actor uses the explicit replacement operation.
        try:
            _call_with_error(scene.reload_local_description)
        except RuntimeError:
            raise configuration_error from None


def _ensure_label(
    actors: unreal.EditorActorSubsystem,
    label: str,
    text: str,
    location: unreal.Vector,
) -> None:
    existing = {
        actor.get_actor_label(): actor for actor in actors.get_all_level_actors()
    }
    actor = existing.get(label)
    if actor is None:
        actor = actors.spawn_actor_from_class(
            unreal.TextRenderActor,
            location,
            unreal.Rotator(0.0, 0.0, 180.0),
            False,
        )
        if actor is None:
            raise RuntimeError(f"could not create label {label}")
        actor.set_actor_label(label)
    actor.set_actor_location(location, False, False)
    text_render = actor.get_editor_property("text_render")
    text_render.set_text(text)
    text_render.set_world_size(7.0)
    text_render.set_text_render_color(unreal.Color(230, 235, 245, 255))
    for component in actor.get_components_by_class(unreal.BillboardComponent):
        component.set_visibility(False)


def _ensure_camera_and_light(actors: unreal.EditorActorSubsystem) -> None:
    existing = {
        actor.get_actor_label(): actor for actor in actors.get_all_level_actors()
    }
    camera = existing.get("M2.9a Articulated Camera")
    if camera is None:
        camera = actors.spawn_actor_from_class(
            unreal.CameraActor,
            unreal.Vector(-460.0, 0.0, 240.0),
            unreal.Rotator(0.0, -22.0, 0.0),
            False,
        )
        if camera is None:
            raise RuntimeError("could not create articulated camera")
        camera.set_actor_label("M2.9a Articulated Camera")
    camera.set_actor_location(unreal.Vector(-460.0, 0.0, 240.0), False, False)
    camera.set_actor_rotation(unreal.Rotator(0.0, -22.0, 0.0), False)
    camera.camera_component.set_editor_property("field_of_view", 55.0)

    light = existing.get("M2.9a Articulated Light")
    if light is None:
        light = actors.spawn_actor_from_class(
            unreal.PointLight,
            unreal.Vector(-160.0, 0.0, 360.0),
            unreal.Rotator(0.0, 0.0, 0.0),
            False,
        )
        if light is None:
            raise RuntimeError("could not create articulated light")
        light.set_actor_label("M2.9a Articulated Light")
    light.set_actor_location(unreal.Vector(-160.0, 0.0, 360.0), False, False)
    light.point_light_component.set_editor_property("intensity", 12000.0)
    light.point_light_component.set_editor_property("attenuation_radius", 1800.0)


def _create_level(
    robot_blueprint: unreal.Blueprint,
    scene_blueprint: unreal.Blueprint,
    materials: dict[str, unreal.Material],
) -> None:
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if unreal.load_asset(LEVEL_OBJECT_PATH) is not None:
        if not levels.load_level(LEVEL_PATH):
            raise RuntimeError(f"could not load {LEVEL_PATH}")
    elif not levels.new_level(LEVEL_PATH, False):
        raise RuntimeError(f"could not create {LEVEL_PATH}")

    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    robot_class = robot_blueprint.generated_class()
    scene_class = scene_blueprint.generated_class()
    robots = [
        actor for actor in actors.get_all_level_actors() if actor.get_class() == robot_class
    ]
    while len(robots) < 3:
        actor = actors.spawn_actor_from_class(
            robot_class,
            unreal.Vector(0.0, 0.0, 0.0),
            unreal.Rotator(0.0, 0.0, 0.0),
            False,
        )
        if actor is None:
            raise RuntimeError("could not create articulated robot actor")
        robots.append(actor)

    layers = [
        unreal.DeferredTeleopKinematicSemanticLayer.CONFIRMED,
        unreal.DeferredTeleopKinematicSemanticLayer.ARRIVAL,
        unreal.DeferredTeleopKinematicSemanticLayer.TARGET,
    ]
    labels = [
        "M2.9a CONFIRMED SO101",
        "M2.9a ARRIVAL SO101",
        "M2.9a TARGET SO101",
    ]
    layer_materials = [
        materials["confirmed"],
        materials["arrival"],
        materials["target"],
    ]
    canonical_y_offsets = [-1.8, 0.0, 1.8]
    for index, actor in enumerate(robots[:3]):
        actor.set_editor_property("semantic_layer", layers[index])
        actor.set_editor_property("link_material", layer_materials[index])
        actor.set_editor_property("tool_material", materials["tool"])
        actor.set_editor_property("segment_material", layer_materials[index])
        actor.set_editor_property("show_debug_names", False)
        actor.set_actor_label(labels[index])
        # Establish distinct positions for a readable fixture replay.  The
        # articulated scene applies canonical roots when its view is replayed.
        actor.set_actor_location(
            unreal.Vector(0.0, -100.0 * canonical_y_offsets[index], 0.0),
            False,
            False,
        )
        actor.set_debug_frames_visible(True)

    scenes = [
        actor for actor in actors.get_all_level_actors() if actor.get_class() == scene_class
    ]
    if scenes:
        scene = scenes[0]
    else:
        scene = actors.spawn_actor_from_class(
            scene_class,
            unreal.Vector(0.0, 0.0, 0.0),
            unreal.Rotator(0.0, 0.0, 0.0),
            False,
        )
        if scene is None:
            raise RuntimeError("could not create articulated scene actor")
    scene.set_actor_label("M2.9a Articulated Scene")
    scene.set_editor_property("confirmed_actor", robots[0])
    scene.set_editor_property("arrival_actor", robots[1])
    scene.set_editor_property("target_actor", robots[2])

    binding = _make_binding()
    scene.set_editor_property("model_binding", binding)
    _configure_scene_binding(scene, binding)
    mission_client = scene.get_editor_property("mission_client")
    mission_client.set_editor_property(
        "wire_mode",
        unreal.DeferredTeleopMissionWireMode.ARTICULATED_VIEW,
    )
    mission_client.set_editor_property("auto_connect", False)

    # The fixture is parsed by the same strict C++ parser used by the Mission
    # client, then fed through the same production transaction used by the
    # live delegate.  Its status remains MEASURED/PREDICTED/OPERATOR_ASSERTED,
    # while the labels explicitly identify this as synthetic replay.
    try:
        with open(_fixture_path(), "rb") as stream:
            fixture_bytes = stream.read()
        fixture_json = fixture_bytes.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"could not read fixture {_fixture_path()}: {error}") from error
    _call_with_error(scene.apply_articulated_view_json, fixture_json)
    _ensure_label(
        actors,
        "M2.9a Synthetic Fixture Label",
        "FIXTURE REPLAY / SYNTHETIC DEMONSTRATION",
        unreal.Vector(0.0, 0.0, 150.0),
    )
    _ensure_camera_and_light(actors)
    if not levels.save_current_level():
        raise RuntimeError(f"could not save {LEVEL_PATH}")


def main() -> None:
    materials = {
        "confirmed": _create_material(
            "M_M2_9aArticulatedConfirmed",
            unreal.LinearColor(0.05, 0.85, 0.25, 1.0),
        ),
        "arrival": _create_material(
            "M_M2_9aArticulatedArrival",
            unreal.LinearColor(0.75, 0.75, 0.78, 1.0),
        ),
        "target": _create_material(
            "M_M2_9aArticulatedTarget",
            unreal.LinearColor(0.02, 0.18, 1.0, 1.0),
        ),
        "tool": _create_material(
            "M_M2_9aArticulatedTool",
            unreal.LinearColor(0.95, 0.10, 0.75, 1.0),
        ),
        "segment": _create_material(
            "M_M2_9aArticulatedSegment",
            unreal.LinearColor(0.12, 0.65, 1.0, 1.0),
        ),
    }
    robot_blueprint = _create_blueprint(
        ROBOT_BLUEPRINT_PATH,
        "BP_M2ArticulatedSO101",
        "/Script/DeferredTeleopRuntime.DeferredTeleopKinematicRobotActor",
    )
    scene_blueprint = _create_blueprint(
        SCENE_BLUEPRINT_PATH,
        "BP_M2ArticulatedScene",
        "/Script/DeferredTeleopRuntime.DeferredTeleopArticulatedSceneActor",
    )
    _configure_robot_defaults(robot_blueprint, materials)
    _configure_scene_defaults(scene_blueprint)
    _create_level(robot_blueprint, scene_blueprint, materials)
    unreal.log("M2.9a articulated fixture replay generated successfully")


main()
