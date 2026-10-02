"""Read the .GoZ ZBrush sent back with GoB's real importer and check it.

    blender --background --python tests/test_zbrush_return.py

This is the second half of the bridge. tests/test_export_operator.py produces
the artifact and tools/zbrush_validate.py confirms ZBrush accepts it; this reads
what ZBrush wrote in reply and asserts the mesh Blender ends up with.

The return trip is driven by tools/zbrush_export_back.py, which imports the
Blender artifact into ZBrush and presses Tool:GoZ. ZBrush rewrites
<project>/<object>.GoZ at that point, and this test consumes it.

When the returned file is absent the test reports a skip and passes, so it is
safe on a machine without ZBrush.
"""

import os
import struct
import sys

import addon_utils
import bpy
import numpy as np

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")
FAILURES = []
CHECKS = 0

GOZ_DIR = os.path.join(
    os.environ.get("PUBLIC", r"C:\Users\Public"),
    "Pixologic", "GoZProjects", "Default",
)
OBJECT = os.environ.get("GOB_RETURN_OBJECT", "GoBExportProbe")
GOZ_PATH = os.path.join(GOZ_DIR, f"{OBJECT}.GoZ")

MAGIC = b"GoZb 1.0 ZBrush GoZ Binary"
TAG_VERTICES = b"\x11\x27\x00\x00"
TAG_FACES = b"\x21\x4e\x00\x00"
TAG_UV = b"\xa9\x61\x00\x00"
TAG_MASK = b"\x32\x75\x00\x00"


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS | {label}", flush=True)
    else:
        FAILURES.append(label)
        print(f"FAIL | {label} | {detail}", flush=True)


class OperatorStub:
    """GoZit only uses these members of the operator."""

    def __init__(self, cls):
        self._cls = cls
        for name in ("make_mesh",):
            setattr(self, name, cls.__dict__[name].__get__(self, type(self)))
        for name in ("mesh_topology_matches", "_ensure_object_in_view_layer",
                     "find_object_for_name"):
            member = cls.__dict__[name]
            setattr(self, name, getattr(member, "__func__", member))

    def report(self, level, message):
        print(f"INFO | import reported {level}: {message}", flush=True)


def section(data, tag):
    """Return (element count, payload) for a section, skipping false matches.

    A tag's four bytes can occur inside mesh data, so a candidate is only
    accepted when its declared payload actually fits inside the file.
    """
    offset = 0
    while True:
        offset = data.find(tag, offset)
        if offset < 0:
            return None, None
        length = struct.unpack_from("<I", data, offset + 4)[0]
        count = struct.unpack_from("<Q", data, offset + 8)[0]
        if 16 <= length <= len(data) - offset:
            # The stored length counts the tag, the length field and the count,
            # so the payload is length - 16 bytes starting at offset + 16. The
            # end is therefore offset + length.
            return count, data[offset + 16: offset + length]
        offset += 1


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)

    if not os.path.isfile(GOZ_PATH):
        print(f"INFO | {GOZ_PATH} not found; skipping the return-trip check.")
        print("INFO | run tools/zbrush_export_back.py in ZBrush first.")
        print("=" * 72)
        print("RESULT checks=0 failures=0 (skipped)")
        print("VERDICT ALL_PASS")
        return 0

    addon_utils.enable(EXT, default_set=False, persistent=True)
    gob_import = sys.modules.get(f"{EXT}.gob_import")
    utils = sys.modules.get(f"{EXT}.utils")
    preferences = sys.modules.get(f"{EXT}.preferences")
    if None in (gob_import, utils, preferences):
        print(f"FAIL | extension {EXT!r} is not available")
        return 1

    addon_root = os.path.dirname(gob_import.__file__)
    if os.path.join(addon_root, "tests") not in sys.path:
        sys.path.insert(0, os.path.join(addon_root, "tests"))
    from prefs_stub import build_prefs_stub

    stub = build_prefs_stub(preferences.GoB_Preferences)
    stub.import_material = "NONE"
    stub.import_uv = True
    stub.import_mask = True
    stub.import_polypaint = True
    stub.import_uv_name = "UVMap"
    utils._TEST_PREFS = stub

    with open(GOZ_PATH, "rb") as handle:
        data = handle.read()
    print(f"INFO | reading {GOZ_PATH} ({len(data)} bytes)")
    check("the returned file carries the GoZ magic", data.startswith(MAGIC))

    # --- what ZBrush wrote --------------------------------------------------
    vert_count, _ = section(data, TAG_VERTICES)
    face_count, _ = section(data, TAG_FACES)
    uv_count, uv_payload = section(data, TAG_UV)
    mask_count, mask_payload = section(data, TAG_MASK)
    print(f"INFO | file declares vertices={vert_count} faces={face_count} "
          f"uv={uv_count} mask={mask_count}")
    print(f"INFO | mask tag at offset {data.find(TAG_MASK)}, "
          f"payload {None if mask_payload is None else len(mask_payload)} bytes, "
          f"{None if mask_payload is None else len(mask_payload) // 2} records")
    print(f"INFO | uv payload {None if uv_payload is None else len(uv_payload)} bytes, "
          f"expected {uv_count * 8 * 4 if uv_count else None}")

    check("ZBrush returned a UV section", uv_count is not None,
          "the UV section is what proves the round trip carries UVs")
    check("ZBrush returned the same vertex count", vert_count == 8,
          f"{vert_count}")
    check("ZBrush returned the same face count", face_count == 6,
          f"{face_count}")
    check("ZBrush returned the same UV face count", uv_count == 6,
          f"{uv_count}")

    # The mask Blender sent was linspace(0, 1, N) inverted onto the wire, so
    # ZBrush echoing it back unchanged means the mask survived both directions.
    if mask_payload is not None:
        records = np.frombuffer(mask_payload, dtype="<u2")
        expected = np.rint(
            (1.0 - np.linspace(0.0, 1.0, len(records), dtype=np.float32)) * 65535
        ).astype("<u2")
        print(f"INFO | mask records returned : {records.tolist()}")
        print(f"INFO | mask records expected : {expected.tolist()}")
        check("the mask came back unchanged",
              records.tolist() == expected.tolist(),
              "ZBrush altered the mask on the way back")

    # Same picture for the UVs. test_export_operator.py writes
    # value(i) = (i*7%23)/23 and (i*11%19)/19 per loop, and the export flips V,
    # so the file carries 1 - v. ZBrush returning the file's own values means
    # the UVs survived unchanged.
    if uv_payload is not None:
        returned = np.frombuffer(uv_payload, dtype="<f4").reshape((-1, 4, 2))
        loops = returned.reshape(-1, 2)
        expected_uv = np.empty_like(loops)
        for index in range(len(loops)):
            expected_uv[index, 0] = (index * 7 % 23) / 23.0
            expected_uv[index, 1] = 1.0 - (index * 11 % 19) / 19.0
        difference = float(np.max(np.abs(expected_uv - loops)))
        print(f"INFO | UV max difference from what Blender wrote: {difference:.8f}")
        check("the UVs came back unchanged", difference < 1e-4,
              f"max difference {difference}")

    # --- and Blender can rebuild it ----------------------------------------
    objects_before = set(obj.name for obj in bpy.data.objects)
    try:
        gob_import.GoB_OT_import.GoZit(
            OperatorStub(gob_import.GoB_OT_import), GOZ_PATH
        )
        imported = True
    except Exception as error:
        import traceback
        traceback.print_exc()
        imported = False
        print(f"INFO | GoZit raised {type(error).__name__}: {error}")
    check("importing the returned file does not raise", imported)

    new_objects = [o for o in bpy.data.objects if o.name not in objects_before]
    check("the returned file produced an object", bool(new_objects),
          f"new={[o.name for o in new_objects]}")

    if new_objects:
        obj = new_objects[0]
        mesh = obj.data
        print(f"INFO | rebuilt {obj.name!r} verts={len(mesh.vertices)} "
              f"polys={len(mesh.polygons)} uvs={[l.name for l in mesh.uv_layers]}")

        check("rebuilt vertex count matches", len(mesh.vertices) == vert_count,
              f"{len(mesh.vertices)} vs {vert_count}")
        check("rebuilt face count matches", len(mesh.polygons) == face_count,
              f"{len(mesh.polygons)} vs {face_count}")
        check("rebuilt mesh has a UV layer", bool(mesh.uv_layers),
              f"uv_layers={[l.name for l in mesh.uv_layers]}")
        check("rebuilt mesh has polypaint",
              bool(mesh.color_attributes),
              f"color_attributes={[a.name for a in mesh.color_attributes]}")

        # The colours matter, not just the presence of the attribute. The
        # export writes (r, g, b) = (i%7/7, i%5/5, i%3/3) per vertex as BGRA,
        # so the import should reproduce them.
        if mesh.color_attributes and vert_count:
            attribute = mesh.color_attributes[0]
            values = np.empty(len(attribute.data) * 4, dtype=np.float32)
            attribute.data.foreach_get("color_srgb", values)
            values = values.reshape((-1, 4))
            expected = np.empty_like(values)
            for index in range(len(values)):
                expected[index, 0] = (index % 7) / 7.0
                expected[index, 1] = (index % 5) / 5.0
                expected[index, 2] = (index % 3) / 3.0
                expected[index, 3] = 1.0
            colour_difference = float(np.max(np.abs(expected[:, :3] - values[:, :3])))
            print(f"INFO | polypaint first 3 vertices: {values[:3, :3].tolist()}")
            print(f"INFO | polypaint max difference: {colour_difference:.6f}")
            check("polypaint colours came back", colour_difference < 0.01,
                  f"max difference {colour_difference}")
        check("rebuilt mesh has mask data",
              "mask" in obj.vertex_groups or ".sculpt_mask" in mesh.attributes,
              f"groups={[g.name for g in obj.vertex_groups]} "
              f"attrs={[a.name for a in mesh.attributes]}")

        coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", coords)
        check("rebuilt coordinates are finite",
              bool(np.isfinite(coords).all()))

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
