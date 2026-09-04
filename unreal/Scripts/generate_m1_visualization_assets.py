"""Generate the repository-owned M1 Blueprint, materials and example level."""

from __future__ import annotations

import unreal

PLUGIN_ROOT = "/DeferredTeleop"
MATERIAL_ROOT = f"{PLUGIN_ROOT}/Materials"
EXAMPLE_ROOT = f"{PLUGIN_ROOT}/M1"
BLUEPRINT_PATH = f"{EXAMPLE_ROOT}/BP_M1DeferredStates"
BLUEPRINT_OBJECT_PATH = f"{BLUEPRINT_PATH}.BP_M1DeferredStates"
LEVEL_PATH = f"{EXAMPLE_ROOT}/M1_DeferredStates"
LEVEL_OBJECT_PATH = f"{LEVEL_PATH}.M1_DeferredStates"


def _create_material(name: str, color: unreal.LinearColor, opacity: float) -> unreal.Material:
    asset_path = f"{MATERIAL_ROOT}/{name}"
    material = unreal.load_asset(f"{asset_path}.{name}")
    material_exists = material is not None
    if material_exists:
        if not isinstance(material, unreal.Material):
            raise RuntimeError(f"{asset_path} exists but is not a Material")
    else:
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

    color_node = unreal.MaterialEditingLibrary.get_material_property_input_node(
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
    unreal.log(
        f"M1 material {name}: expressions="
        f"{unreal.MaterialEditingLibrary.get_num_material_expressions(material)}, "
        f"emissive={color_node}, opacity={opacity_node}"
    )
    needs_rebuild = color_node is None or (opacity < 1.0 and opacity_node is None)
    if needs_rebuild:
        unreal.MaterialEditingLibrary.delete_all_material_expressions(material)
        color_node = unreal.MaterialEditingLibrary.create_material_expression(
            material,
            unreal.MaterialExpressionConstant3Vector,
            -300,
            0,
        )
        color_node.set_editor_property("constant", color)
        unreal.MaterialEditingLibrary.connect_material_property(
            color_node,
            "",
            unreal.MaterialProperty.MP_BASE_COLOR,
        )
        unreal.MaterialEditingLibrary.connect_material_property(
            color_node,
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
            opacity_node.set_editor_property("r", opacity)
            unreal.MaterialEditingLibrary.connect_material_property(
                opacity_node,
                "",
                unreal.MaterialProperty.MP_OPACITY,
            )
    else:
        color_node.set_editor_property("constant", color)
        if opacity_node is not None:
            opacity_node.set_editor_property("r", opacity)

    errors = unreal.MaterialEditingLibrary.recompile_material(material)
    if errors:
        raise RuntimeError(f"material {name} failed to compile: {errors}")
    unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    return material


def _create_blueprint(materials: dict[str, unreal.Material]) -> unreal.Blueprint:
    blueprint = unreal.load_asset(BLUEPRINT_OBJECT_PATH)
    if blueprint is None:
        parent_class = unreal.load_class(
            None,
            "/Script/DeferredTeleopRuntime.DeferredTeleopStateVisualizationActor",
        )
        if parent_class is None:
            raise RuntimeError("DeferredTeleopStateVisualizationActor class is unavailable")
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("parent_class", parent_class)
        blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            "BP_M1DeferredStates",
            EXAMPLE_ROOT,
            unreal.Blueprint,
            factory,
        )
    if blueprint is None or not hasattr(blueprint, "generated_class"):
        raise RuntimeError(f"{BLUEPRINT_PATH} is not a usable Blueprint")

    generated_class = blueprint.generated_class()
    defaults = unreal.get_default_object(generated_class)
    defaults.set_editor_property("confirmed_material", materials["confirmed"])
    defaults.set_editor_property("arrival_material", materials["arrival"])
    defaults.set_editor_property("target_material", materials["target"])
    defaults.set_editor_property("trajectory_material", materials["trajectory"])
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)
    return blueprint


def _create_level(blueprint: unreal.Blueprint) -> None:
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if unreal.load_asset(LEVEL_OBJECT_PATH) is not None:
        if not levels.load_level(LEVEL_PATH):
            raise RuntimeError(f"could not load {LEVEL_PATH}")
    elif not levels.new_level(LEVEL_PATH, False):
        raise RuntimeError(f"could not create {LEVEL_PATH}")

    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    generated_class = blueprint.generated_class()
    existing = [
        actor
        for actor in actors.get_all_level_actors()
        if actor.get_class() == generated_class
    ]
    if not existing:
        actor = actors.spawn_actor_from_class(
            generated_class,
            unreal.Vector(0.0, 0.0, 0.0),
            unreal.Rotator(0.0, 0.0, 0.0),
            False,
        )
        if actor is None:
            raise RuntimeError("could not place BP_M1DeferredStates")
        actor.set_actor_label("M1 Deferred State Visualization")
    if not levels.save_current_level():
        raise RuntimeError(f"could not save {LEVEL_PATH}")


def main() -> None:
    materials = {
        "confirmed": _create_material(
            "M_Confirmed",
            unreal.LinearColor(0.16, 0.18, 0.22, 1.0),
            1.0,
        ),
        "arrival": _create_material(
            "M_Arrival",
            unreal.LinearColor(1.0, 1.0, 1.0, 1.0),
            0.55,
        ),
        "target": _create_material(
            "M_Target",
            unreal.LinearColor(0.02, 0.18, 1.0, 1.0),
            0.65,
        ),
        "trajectory": _create_material(
            "M_Trajectory",
            unreal.LinearColor(0.12, 0.65, 1.0, 1.0),
            0.72,
        ),
    }
    blueprint = _create_blueprint(materials)
    _create_level(blueprint)
    unreal.log("M1 visualization assets generated successfully")


main()
