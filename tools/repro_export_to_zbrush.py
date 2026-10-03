"""Reproduce the user's failing flow: ZBrush already open, press Export.

    blender --background --python .probe/repro_export.py

Uses the *real* GoZ root (C:/Users/Pixologic) and the real preferences path,
with export_run_zbrush on, exactly like the add-on does when the button is
pressed. Prints the ZBrush process list before and after, so a second instance
started by the old Popen path is visible.
"""

import os
import subprocess
import sys
import time

import addon_utils
import bmesh
import bpy

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")
PROBE_NAME = "GoBHandoffProbe"
ZBRUSH = r"C:\Program Files\Maxon ZBrush 2026\ZBrush.exe"
REAL_PROJECT = "C:/Users/Public/Pixologic/GoZProjects/Default/"


def zbrush_pids():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ZBrush.exe", "/NH"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as error:
        return [f"<tasklist failed: {error}>"]
    pids = []
    for line in out.splitlines():
        parts = [part.strip('"') for part in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("zbrush.exe"):
            pids.append(parts[1])
    return pids or ["<none>"]


def main():
    addon_utils.enable(EXT, default_set=False, persistent=True)
    gob_export = sys.modules.get(f"{EXT}.gob_export")
    paths = sys.modules.get(f"{EXT}.paths")
    utils = sys.modules.get(f"{EXT}.utils")
    preferences = sys.modules.get(f"{EXT}.preferences")
    if None in (gob_export, paths, utils, preferences):
        print(f"FAIL | extension {EXT!r} is not available")
        return 1

    tests = os.path.join(os.path.dirname(gob_export.__file__), "tests")
    if tests not in sys.path:
        sys.path.insert(0, tests)
    from prefs_stub import build_prefs_stub

    stub = build_prefs_stub(preferences.GoB_Preferences)
    stub.export_modifiers = "IGNORE"
    stub.export_polygroups = "FACE_SETS"
    stub.export_mask = "NONE"
    stub.export_merge = False
    stub.export_run_zbrush = True          # the real flow: hand off to ZBrush
    stub.export_remove_internal_faces = False
    stub.clean_project_path = False
    stub.debug_output = True
    stub.custom_pixologoc_path = False     # -> default, the real Public folder
    stub.project_path = REAL_PROJECT
    stub.project_path_windows = REAL_PROJECT
    stub.zbrush_exec = ZBRUSH
    stub.zbrush_exec_windows = ZBRUSH
    utils._TEST_PREFS = stub

    mesh = bpy.data.meshes.new(f"{PROBE_NAME}Mesh")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(PROBE_NAME, mesh)
    bpy.context.scene.collection.objects.link(obj)
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()

    print(f"INFO | goz root      : {paths.PATH_GOZ}")
    print(f"INFO | object list   : {paths.PATH_OBJLIST}")
    print(f"INFO | helper        : {paths.find_goz_from_app_helper()}")
    print(f"INFO | zbrush running: {paths.zbrush_is_running()}")
    print(f"INFO | ZBrush pids before: {zbrush_pids()}")
    print(f"INFO | exporting {PROBE_NAME} "
          f"({len(obj.data.vertices)}v/{len(obj.data.polygons)}f)")

    result = bpy.ops.scene.gob_export()
    print(f"INFO | export result: {result}")

    time.sleep(6)
    print(f"INFO | ZBrush pids after : {zbrush_pids()}")

    for label, path in (
        ("GoZ_ObjectList.txt", paths.PATH_OBJLIST),
        ("GoZ_ObjectPath.txt", paths.goz_object_path_file()),
        ("GoZ_Application.txt",
         os.path.join(paths.PATH_GOZ, "GoZBrush", "GoZ_Application.txt")),
        (f"{PROBE_NAME}.GoZ",
         os.path.join(REAL_PROJECT.replace("/", os.sep), f"{PROBE_NAME}.GoZ")),
    ):
        if os.path.isfile(path):
            with open(path, "rt", encoding="utf-8", errors="replace") as handle:
                content = handle.read().strip()
            print(f"INFO | {label:22} {os.path.getsize(path):>8} bytes  "
                  f"{time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(path)))}"
                  f"  | {content[:80]}")
        else:
            print(f"INFO | {label:22} MISSING ({path})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
