"""Does the ONLY_EXPORT path export the CURRENT mesh, or a stale evaluated one?

The user's preference is export_modifiers = 'ONLY_EXPORT', which exports the
evaluated mesh (modifiers applied) via object_eval.to_mesh(). Every test in this
suite forces 'IGNORE' instead, so this path was never exercised.

ONLY_EXPORT obtains a temporary mesh that has to be released with
to_mesh_clear(). If that release is missed, or if the evaluated depsgraph is not
current, the next export repeats the first geometry - the export would look
correct once and then never change again, which is the reported symptom.

The test exports, edits one vertex, exports again for both modes, and reports
whether the second file reflects the edit.
"""
import os
import shutil
import struct
import sys
import tempfile

import addon_utils
import bmesh
import bpy

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")
FAILURES = []
CHECKS = 0


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS | {label}", flush=True)
    else:
        FAILURES.append(label)
        print(f"FAIL | {label} | {detail}", flush=True)


def vertices_of(path):
    with open(path, "rb") as handle:
        data = handle.read()
    offset = data.find(b"\x11\x27\x00\x00")
    count = struct.unpack_from("<Q", data, offset + 8)[0]
    values = struct.unpack_from(f"<{count * 3}f", data, offset + 16)
    return [tuple(round(values[i * 3 + k], 4) for k in range(3))
            for i in range(count)]


def run_mode(mode):
    """Export, move a vertex, export again. Return (first, second) vertices."""
    root = tempfile.mkdtemp(prefix=f"gob_{mode.lower()}_")
    goz_root = os.path.join(root, "Pixologic")
    project = os.path.join(goz_root, "GoZProjects", "Default")
    os.makedirs(project)

    paths = sys.modules[f"{EXT}.paths"]
    utils = sys.modules[f"{EXT}.utils"]
    preferences = sys.modules[f"{EXT}.preferences"]
    sys.path.insert(0, os.path.join(os.path.dirname(paths.__file__), "tests"))
    from prefs_stub import build_prefs_stub

    stub = build_prefs_stub(preferences.GoB_Preferences)
    stub.export_modifiers = mode
    stub.export_polygroups = "NONE"
    stub.export_mask = "NONE"
    stub.export_run_zbrush = False
    stub.export_remove_internal_faces = False
    stub.clean_project_path = False
    stub.custom_pixologoc_path = True
    stub.pixologoc_path = goz_root
    stub.pixologoc_path_windows = goz_root
    stub.project_path = project
    stub.project_path_windows = project
    utils._TEST_PREFS = stub

    original = paths.PATH_GOZ
    paths.set_goz_path(goz_root)
    try:
        for existing in list(bpy.data.objects):
            bpy.data.objects.remove(existing, do_unlink=True)

        mesh = bpy.data.meshes.new("M")
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=2.0)
        bm.to_mesh(mesh)
        bm.free()
        mesh.uv_layers.new(name="UVMap")
        obj = bpy.data.objects.new("ModeProbe", mesh)
        bpy.context.scene.collection.objects.link(obj)
        # A modifier the user cares about, so a destructive export is visible.
        modifier = obj.modifiers.new(name="Subdivision", type="SUBSURF")
        modifier.levels = 2
        for other in bpy.context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        goz = os.path.join(project, "ModeProbe.GoZ")

        bpy.ops.scene.gob_export()
        first = vertices_of(goz) if os.path.isfile(goz) else []

        after_first_modifiers = [
            (m.name, m.type, getattr(m, "levels", None)) for m in obj.modifiers
        ]
        head = getattr(mesh, "name", None)

        # Move one vertex a long way, the way a user's edit would.
        local = tuple(mesh.vertices[0].co)
        mesh.vertices[0].co = (local[0] + 5.0, local[1], local[2])
        mesh.update()

        bpy.ops.scene.gob_export()
        second = vertices_of(goz) if os.path.isfile(goz) else []

        after_second_modifiers = [
            (m.name, m.type, getattr(m, "levels", None)) for m in obj.modifiers
        ]

        return {
            "first": first,
            "second": second,
            "object_mesh_is_original": obj.data is mesh,
            "mesh_name": head,
            "modifiers_after_first": after_first_modifiers,
            "modifiers_after_second": after_second_modifiers,
        }
    finally:
        paths.set_goz_path(original)
        utils._TEST_PREFS = None
        shutil.rmtree(root, ignore_errors=True)


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 74)
    addon_utils.enable(EXT, default_set=False, persistent=True)
    if f"{EXT}.gob_export" not in sys.modules:
        print(f"FAIL | extension {EXT!r} is not available")
        return 1

    for mode in ("IGNORE", "ONLY_EXPORT", "APPLY_EXPORT"):
        print()
        print(f"--- export_modifiers = {mode!r} ---")
        result = run_mode(mode)
        first = result["first"]
        second = result["second"]

        if not first or not second:
            check(f"{mode}: both exports produced a file", False,
                  f"first={len(first)} verts, second={len(second)} verts")
            continue

        check(f"{mode}: both exports produced {len(first)} vertices",
              len(first) == len(second), f"{len(first)} vs {len(second)}")
        check(f"{mode}: the second export differs from the first",
              first != second,
              "the exported file did not change after editing the mesh, so "
              "ZBrush would replace the subtool with the OLD geometry")

        # The object must still be the one the user is editing. A destructive
        # export leaves it pointing at a throwaway mesh, and every later edit
        # then goes somewhere the export never looks.
        check(f"{mode}: the object still holds the user's own mesh",
              result["object_mesh_is_original"],
              "the export replaced obj.data, so later edits are lost")
        check(f"{mode}: the user's modifier survived the export",
              result["modifiers_after_second"] == [("Subdivision", "SUBSURF", 2)],
              f"{result['modifiers_after_second']}")
        check(f"{mode}: the modifier settings are intact",
              result["modifiers_after_second"] == result["modifiers_after_first"],
              f"{result['modifiers_after_first']} -> "
              f"{result['modifiers_after_second']}")

        if first != second:
            differences = [
                (i, a, b) for i, (a, b) in enumerate(zip(first, second)) if a != b
            ]
            print(f"INFO | changed vertices: {len(differences)}, first "
                  f"{differences[0] if differences else None}")

            # Compare the extent of the mesh rather than one vertex: with a
            # subdivision modifier the move is spread over many vertices, so no
            # single vertex shifts by the full amount and the growth is a
            # fraction of the move. A tenth of a unit is still far above the
            # float noise seen in a round trip, and catches a stale export.
            def extent(values, axis):
                return max(v[axis] for v in values) - min(v[axis] for v in values)

            growth = [
                round(extent(second, axis) - extent(first, axis), 4)
                for axis in range(3)
            ]
            grew = any(delta > 0.1 for delta in growth)
            print(f"INFO | extents first : "
                  f"{[round(extent(first, a), 3) for a in range(3)]}")
            print(f"INFO | extents second: "
                  f"{[round(extent(second, a), 3) for a in range(3)]}")
            check(f"{mode}: the edit reached the file", grew,
                  "the exported mesh did not grow after moving a vertex 5 units")
        else:
            print(f"INFO | first  : {first[:2]}")
            print(f"INFO | second : {second[:2]}")

    print()
    print("=" * 74)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
