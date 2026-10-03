"""Prove what Blender writes into the .GoZ when the topology changes.

    blender --background --python tools/verify_topology_export.py

Run before blaming ZBrush. This drives the real ``GoB_OT_export`` operator four
times with the *same* object name and measures the artifact each time:

    base     cube, 8 verts / 6 faces
    moved    identical topology, two vertices moved
    added    the same cube subdivided, so faces were added
    removed  the same cube with two faces deleted

For every step it records the section counts read back out of the .GoZ header
and a digest of the vertex payload, and it stashes the file under
``.agents/probe/variants/``, together with a manifest the ZBrush-side probe
(``tools/zbrush_topology_probe.py``) consumes.

What each outcome means:

* every step reports its own counts and a different payload digest
    -> Blender is not the bottleneck; the loss happens in ZBrush's import.
* a step repeats the previous counts (typically ``added``/``removed``)
    -> the exporter itself is handing ZBrush stale geometry, and the search
       belongs in Blender's export path (modifiers mode, evaluated mesh,
       name escaping), not in ZBrush.
"""

import hashlib
import json
import os
import shutil
import struct
import sys

import addon_utils
import bmesh
import bpy

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")
PROBE_NAME = "GoBTopoProbe"
MAGIC = b"GoZb 1.0 ZBrush GoZ Binary"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Deliberately not under .agents/: that folder is write-protected for spawned
# processes, so Blender cannot create the probe there.
PROBE_ROOT = os.environ.get("GOB_PROBE_DIR") or os.path.join(REPO, ".probe")
VARIANTS = os.path.join(PROBE_ROOT, "variants")
WORK = os.path.join(PROBE_ROOT, "work")

TAGS = {
    b"\x11\x27\x00\x00": "Vertices",
    b"\x21\x4e\x00\x00": "Faces",
    b"\xa9\x61\x00\x00": "UV",
    b"\xb9\x88\x00\x00": "Polypaint",
    b"\x32\x75\x00\x00": "Mask",
    b"\x41\x9c\x00\x00": "Polygroups",
}

FAILURES = []
CHECKS = 0
REPORT = []


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    line = f"{'PASS' if condition else 'FAIL'} | {label}"
    if not condition:
        FAILURES.append(label)
        line += f" | {detail}"
    print(line, flush=True)
    REPORT.append(line)
    return condition


def say(message):
    print(f"INFO | {message}", flush=True)
    REPORT.append(f"INFO | {message}")


def sections_of(data):
    """Return {name: (count, payload length)} for a GoZ byte string."""
    found = {}
    for tag, name in TAGS.items():
        offset = data.find(tag)
        while offset >= 0:
            length = struct.unpack_from("<I", data, offset + 4)[0]
            count = struct.unpack_from("<Q", data, offset + 8)[0]
            if 16 <= length <= len(data) - offset:
                found[name] = (count, length - 16, offset)
                break
            offset = data.find(tag, offset + 1)
    return found


def vertex_digest(data, found):
    """SHA1 over the raw vertex payload, so two exports are comparable."""
    entry = found.get("Vertices")
    if entry is None:
        return ""
    _, payload, offset = entry
    return hashlib.sha1(data[offset + 16:offset + 16 + payload]).hexdigest()[:12]


def drop_probe_object():
    for obj in list(bpy.data.objects):
        if obj.name == PROBE_NAME:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)


def build_cube(name):
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def select_only(obj):
    for other in bpy.context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


# --- the four meshes ---------------------------------------------------------

def mesh_base():
    obj = build_cube(PROBE_NAME)
    # An unmistakable position: the cube's own symmetry hides edits.
    obj.data.vertices[0].co = (2.5, -1.25, 1.125)
    obj.data.update()
    return obj


def mesh_moved():
    """Same topology, visibly different coordinates."""
    obj = mesh_base()
    obj.data.vertices[0].co = (6.0, -4.5, 3.25)
    obj.data.vertices[1].co = (-3.5, 2.25, -1.75)
    obj.data.update()
    return obj


def mesh_added():
    """Subdivide every edge once: 6 faces become 24."""
    obj = mesh_base()
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.subdivide_edges(
        bm, edges=bm.edges[:], cuts=1, use_grid_fill=True
    )
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return obj


def mesh_removed():
    """Delete two faces: 6 faces become 4, plus the loose vertices left behind."""
    obj = mesh_base()
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[0], bm.faces[1]], context="FACES")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return obj


STEPS = (
    ("base", mesh_base, "the untouched cube"),
    ("moved", mesh_moved, "same topology, moved vertices"),
    ("added", mesh_added, "faces added by subdividing"),
    ("removed", mesh_removed, "faces removed"),
)


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
    tests_dir = os.path.join(addon_root, "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from prefs_stub import build_prefs_stub

    for directory in (VARIANTS, WORK):
        os.makedirs(directory, exist_ok=True)

    goz_root = os.path.join(PROBE_ROOT, "Pixologic")
    project = os.path.join(goz_root, "GoZProjects", "Default")
    os.makedirs(project, exist_ok=True)

    stub = build_prefs_stub(preferences.GoB_Preferences)
    # The default path, so the measurement describes what users get.
    stub.export_modifiers = "ONLY_EXPORT"
    stub.export_polygroups = "FACE_SETS"
    stub.export_mask = "SCULPT_MASK"
    stub.export_merge = False
    stub.export_run_zbrush = False
    stub.export_remove_internal_faces = False
    stub.clean_project_path = False
    stub.debug_output = True
    stub.custom_pixologoc_path = True
    stub.pixologoc_path = goz_root
    stub.pixologoc_path_windows = goz_root
    stub.project_path = project
    stub.project_path_windows = project
    utils._TEST_PREFS = stub

    original_goz = paths.PATH_GOZ
    paths.set_goz_path(goz_root)

    manifest = {"probe_dir": PROBE_ROOT, "object_name": PROBE_NAME, "steps": []}
    try:
        for step, factory, description in STEPS:
            drop_probe_object()
            obj = factory()
            select_only(obj)
            bpy.context.view_layer.update()

            expected_verts = len(obj.data.vertices)
            expected_faces = len(obj.data.polygons)

            result = bpy.ops.scene.gob_export()
            if not check(
                f"[{step}] the export operator ran",
                "FINISHED" in result,
                f"{result}",
            ):
                continue

            goz_path = os.path.join(project, f"{PROBE_NAME}.GoZ")
            if not check(
                f"[{step}] the .GoZ file was written",
                os.path.isfile(goz_path),
                goz_path,
            ):
                continue

            with open(goz_path, "rb") as handle:
                data = handle.read()

            check(f"[{step}] the file carries the GoZ magic", data.startswith(MAGIC))

            found = sections_of(data)
            written_verts = found.get("Vertices", (0,))[0]
            written_faces = found.get("Faces", (0,))[0]
            digest = vertex_digest(data, found)

            say(
                f"[{step}] {description}: mesh {expected_verts}v/{expected_faces}f, "
                f"file {written_verts}v/{written_faces}f, "
                f"vertex payload {digest}, {len(data)} bytes"
            )

            check(
                f"[{step}] the file's vertex count is the edited mesh's",
                written_verts == expected_verts,
                f"file {written_verts} vs mesh {expected_verts}",
            )
            check(
                f"[{step}] the file's face count is the edited mesh's",
                written_faces == expected_faces,
                f"file {written_faces} vs mesh {expected_faces}",
            )

            stash = os.path.join(VARIANTS, f"{step}.GoZ")
            shutil.copy2(goz_path, stash)

            manifest["steps"].append(
                {
                    "step": step,
                    "description": description,
                    "variant": stash,
                    "vertices": written_verts,
                    "faces": written_faces,
                    "vertex_digest": digest,
                    "bytes": len(data),
                }
            )

        # The comparisons that decide who is at fault.
        by_step = {entry["step"]: entry for entry in manifest["steps"]}
        if len(by_step) == len(STEPS):
            check(
                "a vertex-only edit changed the vertex payload",
                by_step["base"]["vertex_digest"] != by_step["moved"]["vertex_digest"],
                f"base and moved share digest {by_step['base']['vertex_digest']}",
            )
            check(
                "adding faces increased the face count in the file",
                by_step["added"]["faces"] > by_step["base"]["faces"],
                f"{by_step['base']['faces']} -> {by_step['added']['faces']}",
            )
            check(
                "removing faces decreased the face count in the file",
                by_step["removed"]["faces"] < by_step["base"]["faces"],
                f"{by_step['base']['faces']} -> {by_step['removed']['faces']}",
            )
            check(
                "every step wrote a distinguishable file",
                len(
                    {
                        (entry["vertices"], entry["faces"], entry["vertex_digest"])
                        for entry in by_step.values()
                    }
                )
                == len(by_step),
                "two steps produced identical counts and vertex payloads",
            )
    finally:
        paths.set_goz_path(original_goz)

    manifest_path = os.path.join(PROBE_ROOT, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    say(f"manifest for the ZBrush probe: {manifest_path}")

    with open(os.path.join(PROBE_ROOT, "blender_side_report.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(REPORT) + "\n")

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
