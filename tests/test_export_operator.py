"""Run GoB's full export pipeline headless and validate the bytes it writes.

    blender --background --python tests/test_export_operator.py

The other export tests reproduce the file layout by hand. This one calls the
real ``GoB_OT_export`` operator with a real scene, so the whole chain runs:
scene setup, the GoZ handshake files, mesh conversion, every mesh section, the
texture stage and the object-list entry.

Blender's background mode has a usable context (window manager, scene,
selection, depsgraph and preferences all exist), which is what makes this
possible without a window.

The output is also left on disk so ZBrush can be pointed at it.
"""

import os
import struct
import sys
import tempfile

import addon_utils
import bmesh
import bpy
import numpy as np

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")
FAILURES = []
CHECKS = 0

MAGIC = b"GoZb 1.0 ZBrush GoZ Binary"
TAGS = {
    b"\x11\x27\x00\x00": "Vertices",
    b"\x21\x4e\x00\x00": "Faces",
    b"\xa9\x61\x00\x00": "UV",
    b"\xb9\x88\x00\x00": "Polypaint",
    b"\x32\x75\x00\x00": "Mask",
    b"\x41\x9c\x00\x00": "Polygroups",
}


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS | {label}", flush=True)
    else:
        FAILURES.append(label)
        print(f"FAIL | {label} | {detail}", flush=True)


def sections_of(data):
    """Return {name: (count, payload length)} for a GoZ byte string."""
    found = {}
    for tag, name in TAGS.items():
        offset = data.find(tag)
        while offset >= 0:
            length = struct.unpack_from("<I", data, offset + 4)[0]
            count = struct.unpack_from("<Q", data, offset + 8)[0]
            if 16 <= length <= len(data) - offset:
                found[name] = (count, length - 16)
                break
            offset = data.find(tag, offset + 1)
    return found


def build_scene_object():
    """A cube carrying UVs, polypaint, a sculpt mask and face sets."""
    mesh = bpy.data.meshes.new("GoBExportMesh")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(mesh)
    bm.free()

    # UVs with distinct per-corner values.
    uv_layer = mesh.uv_layers.new(name="UVMap")
    values = np.empty(len(uv_layer.data) * 2, dtype=np.float32)
    for index in range(len(uv_layer.data)):
        values[index * 2] = (index * 7 % 23) / 23.0
        values[index * 2 + 1] = (index * 11 % 19) / 19.0
    uv_layer.data.foreach_set("uv", values)

    # Polypaint as a point-domain byte colour attribute.
    colours = mesh.color_attributes.new("Col", "BYTE_COLOR", "POINT")
    palette = np.zeros(len(colours.data) * 4, dtype=np.float32)
    for index in range(len(colours.data)):
        palette[index * 4] = (index % 7) / 7.0
        palette[index * 4 + 1] = (index % 5) / 5.0
        palette[index * 4 + 2] = (index % 3) / 3.0
        palette[index * 4 + 3] = 1.0
    colours.data.foreach_set("color_srgb", palette)

    # Sculpt mask: 0 = unmasked, 1 = masked.
    mask = mesh.attributes.new(".sculpt_mask", "FLOAT", "POINT")
    mask_values = np.linspace(0.0, 1.0, len(mask.data), dtype=np.float32)
    mask.data.foreach_set("value", mask_values)

    # Face sets, so polygroups have something to export.
    face_sets = mesh.attributes.new(".sculpt_face_set", "INT", "FACE")
    face_values = np.arange(len(face_sets.data), dtype=np.int32) % 3 + 1
    face_sets.data.foreach_set("value", face_values)

    obj = bpy.data.objects.new("GoBExportProbe", mesh)
    bpy.context.scene.collection.objects.link(obj)
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)

    addon_utils.enable(EXT, default_set=False, persistent=True)
    gob_export = sys.modules.get(f"{EXT}.gob_export")
    paths = sys.modules.get(f"{EXT}.paths")
    utils = sys.modules.get(f"{EXT}.utils")
    preferences = sys.modules.get(f"{EXT}.preferences")
    if None in (gob_export, paths, utils, preferences):
        print(f"FAIL | extension {EXT!r} is not available")
        return 1

    addon_root = os.path.dirname(gob_export.__file__)
    if os.path.join(addon_root, "tests") not in sys.path:
        sys.path.insert(0, os.path.join(addon_root, "tests"))
    from prefs_stub import build_prefs_stub

    stub = build_prefs_stub(preferences.GoB_Preferences)
    stub.export_modifiers = "IGNORE"
    stub.export_polygroups = "FACE_SETS"
    stub.export_mask = "SCULPT_MASK"
    stub.export_merge = False
    stub.export_run_zbrush = False          # do not launch ZBrush from a test
    stub.export_remove_internal_faces = False
    stub.clean_project_path = False
    stub.project_path = ""
    utils._TEST_PREFS = stub

    root = tempfile.mkdtemp(prefix="gob_operator_")
    goz_root = os.path.join(root, "Pixologic")
    project = os.path.join(goz_root, "GoZProjects", "Default")
    os.makedirs(project)
    # No trailing separator, which is the case that used to break.
    stub.custom_pixologoc_path = True
    stub.pixologoc_path = goz_root
    stub.pixologoc_path_windows = goz_root
    stub.project_path = project
    stub.project_path_windows = project

    original_goz = paths.PATH_GOZ
    paths.set_goz_path(goz_root)
    try:
        obj = build_scene_object()
        print(f"INFO | source object {obj.name!r} verts={len(obj.data.vertices)} "
              f"polys={len(obj.data.polygons)}")

        result = bpy.ops.scene.gob_export()
        check("the export operator ran headless", "FINISHED" in result, f"{result}")

        goz_path = os.path.join(project, f"{obj.name}.GoZ")
        check("the operator wrote the .GoZ file", os.path.isfile(goz_path),
              f"expected {goz_path}")
        if not os.path.isfile(goz_path):
            return 1

        with open(goz_path, "rb") as handle:
            data = handle.read()
        check("the file carries the GoZ magic", data.startswith(MAGIC))
        print(f"INFO | wrote {goz_path} ({len(data)} bytes)")

        found = sections_of(data)
        print(f"INFO | sections written: "
              f"{ {k: v for k, v in sorted(found.items())} }")

        check("Vertices section written", "Vertices" in found)
        check("Faces section written", "Faces" in found)
        check("UV section written", "UV" in found)
        check("Polypaint section written", "Polypaint" in found)
        check("Mask section written", "Mask" in found)
        check("Polygroups section written", "Polygroups" in found)

        check("vertex count matches the mesh",
              found.get("Vertices", (0,))[0] == len(obj.data.vertices),
              f"{found.get('Vertices')} vs {len(obj.data.vertices)}")
        check("face count matches the mesh",
              found.get("Faces", (0,))[0] == len(obj.data.polygons),
              f"{found.get('Faces')} vs {len(obj.data.polygons)}")
        check("UV count is the face count",
              found.get("UV", (0,))[0] == len(obj.data.polygons),
              f"{found.get('UV')} vs {len(obj.data.polygons)}")
        check("UV payload is 8 bytes per face",
              found.get("UV", (0, 0))[1] == len(obj.data.polygons) * 32,
              f"{found.get('UV')} expected {len(obj.data.polygons) * 32}")
        check("mask payload is 2 bytes per vertex",
              found.get("Mask", (0, 0))[1] == len(obj.data.vertices) * 2,
              f"{found.get('Mask')}")
        check("polygroup payload is 2 bytes per face",
              found.get("Polygroups", (0, 0))[1] == len(obj.data.polygons) * 2,
              f"{found.get('Polygroups')}")

        # The mask must be inverted on the wire: Blender 0.0 is unmasked, GoZ
        # 65535 is unmasked.
        mask_offset = data.find(b"\x32\x75\x00\x00")
        if mask_offset >= 0:
            records = np.frombuffer(
                data, dtype="<u2", count=found["Mask"][0], offset=mask_offset + 16
            )
            print(f"INFO | mask records first 4: {records[:4].tolist()} "
                  f"(mesh values {np.linspace(0, 1, len(records))[:4].tolist()})")
            check("unmasked vertices are 65535 on the wire", records[0] == 65535,
                  f"first record {records[0]}")
            check("fully masked vertices are 0 on the wire", records[-1] == 0,
                  f"last record {records[-1]}")

        # The handshake files ZBrush reads.
        check("GoB_variables.zvr written",
              os.path.isfile(os.path.join(project, "GoB_variables.zvr")))
        check("GoZ_ObjectList.txt written", os.path.isfile(paths.PATH_OBJLIST))
        check("GoZ_ProjectPath.txt written",
              os.path.isfile(os.path.join(goz_root, "GoZBrush", "GoZ_ProjectPath.txt")))

        if os.path.isfile(paths.PATH_OBJLIST):
            with open(paths.PATH_OBJLIST, "rt") as handle:
                entries = [line.strip() for line in handle if line.strip()]
            print(f"INFO | object list: {entries}")
            check("the object list names the exported object",
                  bool(entries) and os.path.isfile(f"{entries[0]}.GoZ"),
                  f"{entries}")

        # Hand the file over for ZBrush to pick up.
        handoff = os.path.join(
            os.environ.get("PUBLIC", r"C:\Users\Public"),
            "Pixologic", "GoZProjects", "Default",
        )
        if os.path.isdir(handoff):
            import shutil
            target = os.path.join(handoff, f"{obj.name}.GoZ")
            shutil.copy2(goz_path, target)
            print(f"INFO | copied the artifact to {target} for ZBrush to import")
    finally:
        paths.set_goz_path(original_goz)

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
