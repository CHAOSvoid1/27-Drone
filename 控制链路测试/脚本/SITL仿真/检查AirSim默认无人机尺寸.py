"""Measure the default AirSim flying pawn in an editor world without saving."""

import json
import os
import unreal


LEVEL = "/Game/RMUC2026Full/Maps/RMUC2026_Full_Field"
BLUEPRINT = "/AirSim/Blueprints/BP_FlyingPawn"
REPORT = r"E:\湘大\RM\逐曦\进度汇报\RM\27赛季无人机\控制链路测试\测试备份\AirSim默认无人机尺寸.json"


def vec(value):
    return {"x": value.x, "y": value.y, "z": value.z}


unreal.EditorLoadingAndSavingUtils.load_map(LEVEL)
pawn_class = unreal.EditorAssetLibrary.load_blueprint_class(BLUEPRINT)
if pawn_class is None:
    raise RuntimeError("could not load " + BLUEPRINT)

pawn = unreal.EditorLevelLibrary.spawn_actor_from_class(
    pawn_class, unreal.Vector(0.0, 0.0, 5000.0)
)
origin, extent = pawn.get_actor_bounds(False)
components = []
for component in pawn.get_components_by_class(unreal.SceneComponent):
    item = {
        "name": component.get_name(),
        "class": component.get_class().get_name(),
        "relative_location_cm": vec(component.get_editor_property("relative_location")),
        "relative_scale": vec(component.get_editor_property("relative_scale3d")),
    }
    if isinstance(component, unreal.PrimitiveComponent):
        try:
            item["collision_enabled"] = str(component.get_collision_enabled())
            item["collision_profile"] = str(component.get_collision_profile_name())
        except Exception as error:
            item["collision_error"] = str(error)
    components.append(item)

report = {
    "blueprint": BLUEPRINT,
    "bounds_origin_cm": vec(origin),
    "bounds_extent_cm": vec(extent),
    "size_cm": {"x": extent.x * 2, "y": extent.y * 2, "z": extent.z * 2},
    "components": components,
}
os.makedirs(os.path.dirname(REPORT), exist_ok=True)
with open(REPORT, "w", encoding="utf-8") as handle:
    json.dump(report, handle, ensure_ascii=False, indent=2)

unreal.EditorLevelLibrary.destroy_actor(pawn)
unreal.log("AIRSIM_PAWN_SIZE_OK size_cm=({:.3f},{:.3f},{:.3f}) report={}".format(
    extent.x * 2, extent.y * 2, extent.z * 2, REPORT
))
