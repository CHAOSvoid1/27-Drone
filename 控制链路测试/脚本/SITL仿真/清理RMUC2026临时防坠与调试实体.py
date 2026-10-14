"""Remove the temporary safety floor and make field markers non-physical."""

import unreal


LEVEL = "/Game/RMUC2026Full/Maps/RMUC2026_Full_Field"
SAFETY_FLOOR = "RMUC_TEMP_SAFETY_FLOOR_Z0"
MARKERS = {
    "RMUC_ORIGIN_Z0",
    "RMUC_POSITIVE_X_AXIS",
    "RMUC_POSITIVE_Y_AXIS",
    "RMUC_HANGAR_CANDIDATE_MARKER",
}


unreal.EditorLoadingAndSavingUtils.load_map(LEVEL)
removed_floor = 0
updated_markers = 0
updated_lights = 0

for actor in list(unreal.EditorLevelLibrary.get_all_level_actors()):
    label = actor.get_actor_label()
    if label == SAFETY_FLOOR:
        unreal.EditorLevelLibrary.destroy_actor(actor)
        removed_floor += 1
    elif label in MARKERS:
        actor.set_actor_enable_collision(False)
        actor.set_actor_hidden_in_game(True)
        # Keep them visible and selectable in the editor while excluding them
        # from packaged/runtime worlds when this property is available.
        try:
            actor.set_editor_property("is_editor_only_actor", True)
        except Exception:
            pass
        actor.modify()
        updated_markers += 1
    elif label in ("RMUC_DirectionalLight", "RMUC_SkyLight"):
        root = actor.get_editor_property("root_component")
        if root is not None:
            root.set_mobility(unreal.ComponentMobility.MOVABLE)
        if label == "RMUC_DirectionalLight":
            component = actor.get_component_by_class(unreal.DirectionalLightComponent)
            if component is not None:
                try:
                    component.set_editor_property("atmosphere_sun_light", True)
                except Exception:
                    pass
        actor.modify()
        updated_lights += 1

world = unreal.EditorLevelLibrary.get_editor_world()
if world is not None:
    world_settings = world.get_world_settings()
    try:
        world_settings.set_editor_property("force_no_precomputed_lighting", True)
        world_settings.modify()
    except Exception:
        pass

if not unreal.EditorLevelLibrary.save_current_level():
    raise RuntimeError("could not save the RMUC full field")

unreal.log("RMUC_DEBUG_CLEANUP_OK removed_floor={} updated_markers={} updated_lights={}".format(
    removed_floor, updated_markers, updated_lights
))
