"""Import the RMUC 2026 STEP assembly into the isolated UE4.27 AirSim project.

Run only through UE4Editor-Cmd with the RMUC2026-UE4 project. The script never
touches the original CAD files or the known-working Blocks project.
"""

import os
import unreal


SOURCE = r"H:\VisualDrone\AirSim\场地文件\CreoImportStaging\rmuc2026-1.asm"
DESTINATION = "/Game/RMUC2026Full/CAD"
LEVEL = "/Game/RMUC2026Full/Maps/RMUC2026_Full_Field"


def fail(message):
    unreal.log_error("RMUC_IMPORT_FAILED: " + message)
    raise RuntimeError(message)


if not os.path.isfile(SOURCE):
    fail("source STEP does not exist: " + SOURCE)

unreal.log("RMUC_IMPORT source={}".format(SOURCE))
unreal.log("RMUC_IMPORT destination={}".format(DESTINATION))

if unreal.EditorAssetLibrary.does_asset_exist(LEVEL):
    fail("destination level already exists; inspect or remove it before reimport: " + LEVEL)
if not unreal.EditorLevelLibrary.new_level(LEVEL):
    fail("could not create destination level: " + LEVEL)
unreal.log("RMUC_IMPORT level={}".format(LEVEL))

scene = unreal.DatasmithSceneElement.construct_datasmith_scene_from_cad_files(
    [SOURCE]
)
if scene is None:
    fail("Datasmith could not construct a scene from the STEP assembly")

# The first pass deliberately uses a coarse tessellation. The source is a
# 174.5 MiB mechanical assembly and the flight simulator does not need bolts
# and sub-millimetre curves. Values are in centimetres/degrees in UE4.27.
tessellation = scene.get_options(unreal.DatasmithCommonTessellationOptions)
if tessellation is not None:
    options = tessellation.get_editor_property("options")
    options.set_editor_property("chord_tolerance", 1.0)
    options.set_editor_property("max_edge_length", 20.0)
    options.set_editor_property("normal_tolerance", 30.0)
    options.set_editor_property(
        "stitching_technique",
        unreal.DatasmithCADStitchingTechnique.STITCHING_SEW,
    )
    tessellation.set_editor_property("options", options)
    unreal.log("RMUC_IMPORT tessellation chord=1cm edge=20cm normal=30deg sew")
else:
    unreal.log_warning("RMUC_IMPORT using Datasmith default tessellation")

result = scene.import_scene(DESTINATION)
if result is None:
    fail("Datasmith import returned no result")

# UE4.27 exposes a smaller DatasmithImportFactoryCreateFileResult than newer
# releases. Count through editor libraries instead of newer result fields.
imported_assets = unreal.EditorAssetLibrary.list_assets(
    DESTINATION, recursive=True, include_folder=False
)
imported_actors = unreal.EditorLevelLibrary.get_all_level_actors()

# Creo/Datasmith meshes often arrive without simple collision. Use their
# rendered triangles for the first physics pass; selected field structures can
# later receive cheaper hand-authored collision meshes.
collision_meshes = 0
for asset_path in imported_assets:
    asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    if isinstance(asset, unreal.StaticMesh):
        body_setup = asset.get_editor_property("body_setup")
        if body_setup is not None:
            body_setup.set_editor_property(
                "collision_trace_flag",
                unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE,
            )
            asset.modify()
            collision_meshes += 1

# Add enough neutral lighting for visual inspection. The imported CAD scene
# does not contain an Unreal sky or lights.
directional = unreal.EditorLevelLibrary.spawn_actor_from_class(
    unreal.DirectionalLight, unreal.Vector(0.0, 0.0, 1000.0), unreal.Rotator(-45.0, -35.0, 0.0)
)
directional.set_actor_label("RMUC_DirectionalLight")
directional_component = directional.get_component_by_class(unreal.DirectionalLightComponent)
directional_component.set_editor_property("intensity", 6.0)
directional_component.set_mobility(unreal.ComponentMobility.MOVABLE)
try:
    directional_component.set_editor_property("atmosphere_sun_light", True)
except Exception:
    pass

skylight = unreal.EditorLevelLibrary.spawn_actor_from_class(
    unreal.SkyLight, unreal.Vector(0.0, 0.0, 500.0)
)
skylight.set_actor_label("RMUC_SkyLight")
skylight_component = skylight.get_component_by_class(unreal.SkyLightComponent)
skylight_component.set_editor_property("intensity", 1.0)
skylight_component.set_mobility(unreal.ComponentMobility.MOVABLE)

if hasattr(unreal, "SkyAtmosphere"):
    atmosphere = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.SkyAtmosphere, unreal.Vector(0.0, 0.0, 0.0)
    )
    atmosphere.set_actor_label("RMUC_SkyAtmosphere")

cube = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube.Cube")
# Visible CAD-origin and positive-axis markers make the assembly coordinate
# system easy to locate before a final PlayerStart is chosen at the hangar.
origin = unreal.EditorLevelLibrary.spawn_actor_from_object(
    cube, unreal.Vector(0.0, 0.0, 25.0)
)
origin.set_actor_label("RMUC_ORIGIN_Z0")
origin.set_actor_scale3d(unreal.Vector(0.5, 0.5, 0.5))
x_axis = unreal.EditorLevelLibrary.spawn_actor_from_object(
    cube, unreal.Vector(250.0, 0.0, 10.0)
)
x_axis.set_actor_label("RMUC_POSITIVE_X_AXIS")
x_axis.set_actor_scale3d(unreal.Vector(5.0, 0.1, 0.1))
y_axis = unreal.EditorLevelLibrary.spawn_actor_from_object(
    cube, unreal.Vector(0.0, 250.0, 10.0)
)
y_axis.set_actor_label("RMUC_POSITIVE_Y_AXIS")
y_axis.set_actor_scale3d(unreal.Vector(0.1, 5.0, 0.1))

for marker in (origin, x_axis, y_axis):
    marker.set_actor_enable_collision(False)
    marker.set_actor_hidden_in_game(True)
    try:
        marker.set_editor_property("is_editor_only_actor", True)
    except Exception:
        pass

world = unreal.EditorLevelLibrary.get_editor_world()
if world is not None:
    world_settings = world.get_world_settings()
    try:
        world_settings.set_editor_property("force_no_precomputed_lighting", True)
    except Exception:
        pass

unreal.EditorAssetLibrary.save_directory("/Game/RMUC2026Full", True, True)
if not unreal.EditorLevelLibrary.save_current_level():
    fail("assets imported, but the RMUC2026 level could not be saved")
unreal.log(
    "RMUC_IMPORT_OK assets={} actors={}".format(
        len(imported_assets), len(imported_actors)
    )
)
unreal.log("RMUC_IMPORT collision_meshes={}".format(collision_meshes))
