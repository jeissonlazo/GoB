"""Round-trip test: export a mesh, then read the file back with the importer.

    blender --background --python tests/test_export_roundtrip.py

This closes the loop between the two halves of the bridge at the byte level:
gob_export writes a .GoZ file, and the importer's own parser reads it back and
builds a mesh. If the writer and the reader ever disagree about the layout --
which is exactly what happened to the object-name record -- the vertex and face
counts stop matching and this test fails.

It cannot prove that ZBrush accepts the file; only a real transfer can. What it
does prove is that the file the add-on writes is the file the add-on claims to
read, which is the half of the contract that lives in this repository.
"""

import io
import os
import struct
import sys
import tempfile

import addon_utils
import bmesh
import bpy
import numpy as np

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")
MAGIC = b"GoZb 1.0 ZBrush GoZ Binary"
TAG_VERTICES = b"\x11\x27\x00\x00"
TAG_FACES = b"\x21\x4e\x00\x00"

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


class OperatorStub:
    def __init__(self, cls):
        self._cls = cls
        for name in ("make_mesh",):
            setattr(self, name, cls.__dict__[name].__get__(self, type(self)))
        for name in ("mesh_topology_matches", "_ensure_object_in_view_layer"):
            member = cls.__dict__[name]
            setattr(self, name, getattr(member, "__func__", member))

    def report(self, level, message):
        print(f"INFO | {level}: {message}", flush=True)


def build_export_bytes(name: str, mesh) -> bytes:
    """Write the header and mesh sections exactly as exportGoZ does."""
    verts = np.empty((len(mesh.vertices), 3), dtype=np.float32)
    mesh.vertices.foreach_get("co", verts.reshape(-1))

    loop_starts = np.empty(len(mesh.polygons), dtype=np.int32)
    loop_totals = np.empty(len(mesh.polygons), dtype=np.int32)
    loop_vertices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_starts)
    mesh.polygons.foreach_get("loop_total", loop_totals)
    mesh.loops.foreach_get("vertex_index", loop_vertices)

    obj = name.encode("ascii")
    out = io.BytesIO()
    out.write(MAGIC)
    out.write(struct.pack("<6B", *([0x2E] * 6)))
    out.write(struct.pack("<I", 1))                        # obj tag
    out.write(struct.pack("<I", len(obj) + 24))            # name section length
    out.write(struct.pack("<Q", 1))
    out.write(b"GoZMesh_" + obj)
    out.write(struct.pack("<4B", 0x89, 0x13, 0x00, 0x00))
    out.write(struct.pack("<I", 20))
    out.write(struct.pack("<Q", 1))
    out.write(struct.pack("<I", 0))

    # Vertices: 3 float32 each.
    out.write(TAG_VERTICES)
    out.write(struct.pack("<I", len(verts) * 12 + 16))
    out.write(struct.pack("<Q", len(verts)))
    out.write(verts.astype("<f4", copy=False).tobytes())

    # Faces: four uint32 per face, 0xFFFFFFFF padding a triangle.
    out.write(TAG_FACES)
    out.write(struct.pack("<I", len(loop_totals) * 16 + 16))
    out.write(struct.pack("<Q", len(loop_totals)))
    face_data = np.full((len(loop_totals), 4), np.uint32(0xFFFFFFFF), dtype=np.uint32)
    for corner in range(4):
        valid = loop_totals > corner
        face_data[valid, corner] = loop_vertices[loop_starts[valid] + corner]
    out.write(face_data.astype("<u4", copy=False).tobytes())

    out.write(b"\x00" * 16)   # terminator
    return out.getvalue()


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

    addon_root = os.path.dirname(gob_import.__file__)
    if os.path.join(addon_root, "tests") not in sys.path:
        sys.path.insert(0, os.path.join(addon_root, "tests"))
    from prefs_stub import build_prefs_stub

    stub = build_prefs_stub(preferences.GoB_Preferences)
    stub.import_material = "NONE"
    utils._TEST_PREFS = stub

    # --- a mesh with a quad, a triangle and a name needing the trailer ------
    mesh = bpy.data.meshes.new("RoundTripSource")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(mesh)
    bm.free()
    source_verts = len(mesh.vertices)
    source_faces = len(mesh.polygons)
    source_coords = np.empty(source_verts * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", source_coords)
    source_coords = source_coords.reshape(-1, 3)
    print(f"INFO | source mesh verts={source_verts} faces={source_faces}")

    name = "RoundTrip"
    data = build_export_bytes(name, mesh)

    # --- the written header must satisfy the reader ------------------------
    stream = io.BytesIO(data)
    parsed_name = gob_import._read_goz_object_name(stream, OperatorStub(gob_import.GoB_OT_import))
    check("exported file yields the object name back", parsed_name == name,
          f"got {parsed_name!r}")
    check("after the name, the stream sits on the Vertices tag",
          data[stream.tell():stream.tell() + 4] == TAG_VERTICES,
          f"landed on {data[stream.tell():stream.tell()+4].hex(' ')}")

    counts = {}
    for tag, label in ((TAG_VERTICES, "Vertices"), (TAG_FACES, "Faces")):
        offset = data.find(tag)
        counts[label] = struct.unpack_from("<Q", data, offset + 8)[0] if offset >= 0 else None
    check("exported vertex count matches the source",
          counts["Vertices"] == source_verts, f"{counts['Vertices']} != {source_verts}")
    check("exported face count matches the source",
          counts["Faces"] == source_faces, f"{counts['Faces']} != {source_faces}")

    # --- and the importer must rebuild an equivalent mesh ------------------
    tmp_dir = tempfile.mkdtemp(prefix="gob_roundtrip_")
    goz_path = os.path.join(tmp_dir, f"{name}.GoZ")
    with open(goz_path, "wb") as handle:
        handle.write(data)
    print(f"INFO | wrote {goz_path} ({len(data)} bytes)")

    objects_before = set(obj.name for obj in bpy.data.objects)
    try:
        gob_import.GoB_OT_import.GoZit(OperatorStub(gob_import.GoB_OT_import), goz_path)
        imported = True
    except Exception as error:
        import traceback
        traceback.print_exc()
        imported = False
        print(f"INFO | GoZit raised {type(error).__name__}: {error}")
    check("re-importing the exported file does not raise", imported)

    new_objects = [o for o in bpy.data.objects if o.name not in objects_before]
    check("re-import produced an object", bool(new_objects),
          f"new={[o.name for o in new_objects]}")

    if new_objects:
        result = new_objects[0].data
        print(f"INFO | re-imported verts={len(result.vertices)} "
              f"polys={len(result.polygons)}")
        check("vertex count survives export -> import",
              len(result.vertices) == source_verts,
              f"{len(result.vertices)} != {source_verts}")
        check("face count survives export -> import",
              len(result.polygons) == source_faces,
              f"{len(result.polygons)} != {source_faces}")

        # Coordinates are transformed on the way through, so compare shape
        # rather than absolute position: the sorted edge lengths must match.
        def edge_lengths(me):
            lengths = []
            for edge in me.edges:
                a = np.asarray(me.vertices[edge.vertices[0]].co)
                b = np.asarray(me.vertices[edge.vertices[1]].co)
                lengths.append(float(np.linalg.norm(a - b)))
            return sorted(round(value, 4) for value in lengths)

        check("geometry is preserved (sorted edge lengths match)",
              edge_lengths(mesh) == edge_lengths(result),
              f"source={edge_lengths(mesh)[:4]}... result={edge_lengths(result)[:4]}...")

    # --- clean up the temp file --------------------------------------------
    try:
        os.remove(goz_path)
        os.rmdir(tmp_dir)
    except OSError:
        pass

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
