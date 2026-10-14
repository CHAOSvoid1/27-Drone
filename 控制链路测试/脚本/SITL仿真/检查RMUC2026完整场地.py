"""Inspect the imported full RMUC field without modifying its assets."""

import json
import math
import os
import unreal


LEVEL = "/Game/RMUC2026Full/Maps/RMUC2026_Full_Field"
REPORT = r"E:\湘大\RM\逐曦\进度汇报\RM\27赛季无人机\控制链路测试\测试备份\RMUC2026完整场地检查.json"
KEYWORDS = ("机场", "机库", "hangar", "airport", "jichang", "garage")


def vector_dict(value):
    return {"x": value.x, "y": value.y, "z": value.z}


if not unreal.EditorAssetLibrary.does_asset_exist(LEVEL):
    raise RuntimeError("level does not exist: " + LEVEL)

unreal.EditorLoadingAndSavingUtils.load_map(LEVEL)
actors = unreal.EditorLevelLibrary.get_all_level_actors()

candidates = []
hangar_descendants = []
mesh_actors = 0
collision_modes = {}
collision_profiles = {}
world_min = [math.inf, math.inf, math.inf]
world_max = [-math.inf, -math.inf, -math.inf]


def ancestor_labels(actor):
    labels = []
    parent = actor.get_attach_parent_actor()
    seen = set()
    while parent is not None and parent.get_name() not in seen:
        seen.add(parent.get_name())
        labels.append(parent.get_actor_label())
        parent = parent.get_attach_parent_actor()
    return labels

for actor in actors:
    label = actor.get_actor_label()
    name = actor.get_name()
    text = (label + " " + name).lower()
    location = actor.get_actor_location()
    item = {
        "label": label,
        "name": name,
        "class": actor.get_class().get_name(),
        "location_cm": vector_dict(location),
    }
    ancestors = ancestor_labels(actor)
    if ancestors:
        item["ancestor_labels"] = ancestors

    if not label.startswith("RMUC_"):
        for component in actor.get_components_by_class(unreal.StaticMeshComponent):
            try:
                mode = str(component.get_collision_enabled())
            except Exception as error:
                mode = "ERROR: " + str(error)
            try:
                profile = str(component.get_collision_profile_name())
            except Exception as error:
                profile = "ERROR: " + str(error)
            collision_modes[mode] = collision_modes.get(mode, 0) + 1
            collision_profiles[profile] = collision_profiles.get(profile, 0) + 1

    try:
        origin, extent = actor.get_actor_bounds(False)
        item["bounds_origin_cm"] = vector_dict(origin)
        item["bounds_extent_cm"] = vector_dict(extent)
        if (extent.x > 0 or extent.y > 0 or extent.z > 0) and not label.startswith("RMUC_"):
            mesh_actors += 1
            low = (origin.x - extent.x, origin.y - extent.y, origin.z - extent.z)
            high = (origin.x + extent.x, origin.y + extent.y, origin.z + extent.z)
            for index in range(3):
                world_min[index] = min(world_min[index], low[index])
                world_max[index] = max(world_max[index], high[index])
    except Exception as error:
        item["bounds_error"] = str(error)

    if any(keyword in text for keyword in KEYWORDS):
        candidates.append(item)
    if any(any(keyword in ancestor.lower() for keyword in KEYWORDS) for ancestor in ancestors):
        hangar_descendants.append(item)

special = []
for actor in actors:
    if actor.get_actor_label().startswith("RMUC_"):
        special.append({
            "label": actor.get_actor_label(),
            "location_cm": vector_dict(actor.get_actor_location()),
        })

report = {
    "level": LEVEL,
    "actor_count": len(actors),
    "actors_with_nonzero_bounds": mesh_actors,
    "static_mesh_component_collision_modes": collision_modes,
    "static_mesh_component_collision_profiles": collision_profiles,
    "world_bounds_cm": {
        "min": {"x": world_min[0], "y": world_min[1], "z": world_min[2]},
        "max": {"x": world_max[0], "y": world_max[1], "z": world_max[2]},
    },
    "keyword_candidates": candidates,
    "hangar_descendants": hangar_descendants,
    "special_markers": special,
}

os.makedirs(os.path.dirname(REPORT), exist_ok=True)
with open(REPORT, "w", encoding="utf-8") as handle:
    json.dump(report, handle, ensure_ascii=False, indent=2)

unreal.log("RMUC_INSPECT_OK actors={} meshes={} candidates={} report={}".format(
    len(actors), mesh_actors, len(candidates), REPORT
))
for candidate in candidates:
    unreal.log("RMUC_CANDIDATE label={} location={}".format(
        candidate["label"], candidate["location_cm"]
    ))
