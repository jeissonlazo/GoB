"""End-to-end import test using a real ZBrush-written .GoZ file.

    blender --background --python tests/test_real_file_import.py

Every other test in this suite checks a piece in isolation. This one drives the
whole read path -- header, object name, mesh sections, transforms, null checks --
and then asserts on the mesh Blender actually ends up with.

It needs a file that ZBrush itself produced, because that is the only authority
on the byte layout. Such a file exists after any GoZ transfer and lives in the
shared GoZ project folder. When it is absent the test reports that it skipped
instead of failing, so it is safe to run on a machine that has never used GoZ.

What it caught: the object-name record used to be parsed 8 bytes short, which
swallowed the name trailer into the object name and left the parser reading a
non-mesh block as if it were the first mesh section.
"""

import os
import sys

import addon_utils
import bpy

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")
FAILURES = []
CHECKS = 0

GOZ_PROJECT_DIR = os.path.join(
    os.environ.get("PUBLIC", r"C:\Users\Public"),
    "Pixologic",
    "GoZProjects",
    "Default",
)
# Set GOB_REAL_GOZ to point at a specific file instead.
GOZ_FILE = os.environ.get("GOB_REAL_GOZ", "")


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS | {label}", flush=True)
    else:
        FAILURES.append(label)
        print(f"FAIL | {label} | {detail}", flush=True)


def find_real_goz():
    if GOZ_FILE:
        return GOZ_FILE if os.path.isfile(GOZ_FILE) else None
    if not os.path.isdir(GOZ_PROJECT_DIR):
        return None
    candidates = sorted(
        os.path.join(GOZ_PROJECT_DIR, name)
        for name in os.listdir(GOZ_PROJECT_DIR)
        if name.lower().endswith(".goz")
    )
    return candidates[0] if candidates else None


class OperatorStub:
    """GoZit is called directly; it only uses these members of the operator.

    Binding is done explicitly rather than through __getattr__, because
    Blender's RNA metaclass does not hand out plain bound methods.
    """

    def __init__(self, cls):
        self._cls = cls
        self.reports = []
        # Plain methods: bind to this stub.
        for name in ("make_mesh",):
            setattr(self, name, cls.__dict__[name].__get__(self, type(self)))
        # staticmethod objects need __func__ to be called without a self.
        for name in ("mesh_topology_matches", "_ensure_object_in_view_layer",
                     "find_object_for_name"):
            member = cls.__dict__[name]
            setattr(self, name, getattr(member, "__func__", member))

    def report(self, level, message):
        self.reports.append(message)
        print(f"INFO | import reported {level}: {message}", flush=True)


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)

    addon_utils.enable(EXT, default_set=False, persistent=True)
    gob_import = sys.modules.get(f"{EXT}.gob_import")
    utils = sys.modules.get(f"{EXT}.utils")
    preferences = sys.modules.get(f"{EXT}.preferences")
    if None in (gob_import, utils, preferences):
        print(f"FAIL | extension {EXT!r} is not available")
        return 1

    goz_path = find_real_goz()
    if goz_path is None:
        print(
            "INFO | no ZBrush-written .GoZ file found in "
            f"{GOZ_PROJECT_DIR!r}; skipping the end-to-end import.\n"
            "INFO | run a GoZ transfer or set GOB_REAL_GOZ to a file to enable it."
        )
        print("=" * 72)
        print("RESULT checks=0 failures=0 (skipped)")
        print("VERDICT ALL_PASS")
        return 0

    addon_root = os.path.dirname(gob_import.__file__)
    if os.path.join(addon_root, "tests") not in sys.path:
        sys.path.insert(0, os.path.join(addon_root, "tests"))
    from prefs_stub import build_prefs_stub

    stub = build_prefs_stub(preferences.GoB_Preferences)
    stub.import_material = "NONE"      # do not create materials for this probe
    stub.import_uv_name = "UVMap"
    utils._TEST_PREFS = stub

    expected_name = os.path.splitext(os.path.basename(goz_path))[0]
    print(f"INFO | importing {goz_path}")
    print(f"INFO | expected object name {expected_name!r}")

    # --- the parser must agree with the file -------------------------------
    with open(goz_path, "rb") as handle:
        head = handle.read()
    check("real file carries the GoZ magic",
          head.startswith(b"GoZb 1.0 ZBrush GoZ Binary"))

    # --- drive the real import path ----------------------------------------
    objects_before = set(obj.name for obj in bpy.data.objects)
    stub_reports = OperatorStub(gob_import.GoB_OT_import)
    try:
        gob_import.GoB_OT_import.GoZit(stub_reports, goz_path)
        imported_ok = True
    except Exception as error:
        import traceback
        traceback.print_exc()
        imported_ok = False
        print(f"INFO | GoZit raised {type(error).__name__}: {error}")
    check("importing a real ZBrush file does not raise", imported_ok)

    new_objects = [
        obj for obj in bpy.data.objects if obj.name not in objects_before
    ]
    check("the import created an object", bool(new_objects),
          f"objects before={sorted(objects_before)} new={[o.name for o in new_objects]}")

    if not new_objects:
        print("=" * 72)
        print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
        print("VERDICT FAILURES_PRESENT")
        return 1

    obj = new_objects[0]
    mesh = obj.data
    print(f"INFO | object={obj.name!r} verts={len(mesh.vertices)} "
          f"polys={len(mesh.polygons)} loops={len(mesh.loops)}")

    check("object is named after the file", obj.name == expected_name,
          f"{obj.name!r} != {expected_name!r}")
    check("mesh has geometry",
          len(mesh.vertices) > 0 and len(mesh.polygons) > 0)

    # --- the numbers must match the sections actually in the file ----------
    import struct

    def section_count(tag):
        offset = head.find(tag)
        if offset < 0:
            return None
        return struct.unpack_from("<Q", head, offset + 8)[0]

    declared_vertices = section_count(b"\x11\x27\x00\x00")
    declared_faces = section_count(b"\x21\x4e\x00\x00")
    print(f"INFO | file declares vertices={declared_vertices} faces={declared_faces}")
    check("vertex count matches the Vertices section",
          declared_vertices is None or len(mesh.vertices) == declared_vertices,
          f"mesh={len(mesh.vertices)} file={declared_vertices}")
    check("face count matches the Faces section",
          declared_faces is None or len(mesh.polygons) == declared_faces,
          f"mesh={len(mesh.polygons)} file={declared_faces}")

    # --- every face must reference valid vertices (no garbage indices) -----
    loop_vertices = [loop.vertex_index for loop in mesh.loops]
    check("all loops reference real vertices",
          bool(loop_vertices) and max(loop_vertices) < len(mesh.vertices),
          f"max loop index={max(loop_vertices) if loop_vertices else None} "
          f"vertices={len(mesh.vertices)}")
    check("all faces are triangles or quads",
          all(3 <= len(poly.vertices) <= 4 for poly in mesh.polygons))

    # --- attributes implied by the sections present ------------------------
    if section_count(b"\xb9\x88\x00\x00") is not None:
        check("polypaint section produced a colour attribute",
              bool(mesh.color_attributes),
              f"color_attributes={[a.name for a in mesh.color_attributes]}")
    if section_count(b"\x32\x75\x00\x00") is not None:
        check("mask section produced the mask data",
              "mask" in obj.vertex_groups or ".sculpt_mask" in mesh.attributes,
              f"vertex_groups={[g.name for g in obj.vertex_groups]} "
              f"attributes={[a.name for a in mesh.attributes]}")
    if section_count(b"\x41\x9c\x00\x00") is not None:
        check("polygroup section produced face sets",
              ".sculpt_face_set" in mesh.attributes,
              f"attributes={[a.name for a in mesh.attributes]}")
    if section_count(b"\xa9\x61\x00\x00") is not None:
        check("UV section produced a UV layer",
              bool(mesh.uv_layers),
              f"uv_layers={[layer.name for layer in mesh.uv_layers]}")
    else:
        print("INFO | file has no UV section; no UV layer is expected")

    # --- the coordinates must be finite and not collapsed ------------------
    import numpy as np

    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coords)
    coords = coords.reshape(-1, 3)
    check("all vertex coordinates are finite", bool(np.isfinite(coords).all()))
    extent = coords.max(axis=0) - coords.min(axis=0)
    check("the mesh is not collapsed to a point",
          float(extent.max()) > 0.0, f"extent={extent.tolist()}")

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
