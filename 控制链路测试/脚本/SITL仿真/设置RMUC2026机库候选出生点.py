"""Add an editor-visible candidate start above the Creo part named 机场_PRT."""

import unreal


LEVEL = "/Game/RMUC2026Full/Maps/RMUC2026_Full_Field"
AIRPORT_PARENT = "机场_PRT"
START_LABEL = "RMUC_HANGAR_PLAYER_START_CANDIDATE"
MARKER_LABEL = "RMUC_HANGAR_CANDIDATE_MARKER"


def attached_to_airport(actor):
    parent = actor.get_attach_parent_actor()
    seen = set()
    while parent is not None and parent.get_name() not in seen:
        seen.add(parent.get_name())
        if parent.get_actor_label() == AIRPORT_PARENT:
            return True
        parent = parent.get_attach_parent_actor()
    return False


unreal.EditorLoadingAndSavingUtils.load_map(LEVEL)
actors = unreal.EditorLevelLibrary.get_all_level_actors()

airport_mesh = None
for actor in actors:
    if attached_to_airport(actor):
        airport_mesh = actor
        break
if airport_mesh is None:
    raise RuntimeError("could not find geometry attached to " + AIRPORT_PARENT)

origin, extent = airport_mesh.get_actor_bounds(False)
top_z = origin.z + extent.z
start_location = unreal.Vector(origin.x, origin.y, top_z + 75.0)

# Make the script repeatable without touching user-created PlayerStart actors.
for actor in list(actors):
    if actor.get_actor_label() in (START_LABEL, MARKER_LABEL):
        unreal.EditorLevelLibrary.destroy_actor(actor)

player_start = unreal.EditorLevelLibrary.spawn_actor_from_class(
    unreal.PlayerStart, start_location, unreal.Rotator(0.0, 0.0, 0.0)
)
player_start.set_actor_label(START_LABEL)

cube = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube.Cube")
marker = unreal.EditorLevelLibrary.spawn_actor_from_object(cube, start_location)
marker.set_actor_label(MARKER_LABEL)
marker.set_actor_scale3d(unreal.Vector(0.12, 0.12, 1.0))
marker.set_actor_enable_collision(False)
marker.set_actor_hidden_in_game(True)

if not unreal.EditorLevelLibrary.save_current_level():
    raise RuntimeError("could not save the full RMUC field level")

unreal.log(
    "RMUC_HANGAR_START_OK location_cm=({:.3f},{:.3f},{:.3f}) airport_top_z_cm={:.3f}".format(
        start_location.x, start_location.y, start_location.z, top_z
    )
)
