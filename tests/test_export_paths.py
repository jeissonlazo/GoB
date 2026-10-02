"""Export-operator tests: path handling and texture references.

    blender --background --python tests/test_export_paths.py

A .GoZ export writes several files next to each other and tells ZBrush where to
find them:

    <project>/<object>.GoZ        the mesh
    <project>/<object>.ztn        the marker ZBrush is pointed at
    GoZ_ObjectList.txt            one line per object, path without extension
    GoB_variables.zvr             suffixes, version, project path
    <project>/<object>_diff.bmp   exported textures

The project path is a user preference whose default carries a trailing slash.
Every one of those paths used to be built by plain string concatenation, so a
project path typed without the trailing separator -- which is what a file
browser returns -- put the .ztn markers, the object list entries and the
exported textures *next to* the intended folder instead of inside it, and ZBrush
then looked for objects that were not where it was told.

This test exports for real into a temporary project directory and checks where
each file actually landed.
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


class ExportOperatorStub:
    """execute/exportGoZ only touch these members of the operator."""

    def __init__(self, cls, project_dir):
        self._cls = cls
        self.project_dir = project_dir
        self.reports = []
        for name in ("escape_object_name",):
            setattr(self, name, cls.__dict__[name].__get__(self, type(self)))

    def report(self, level, message):
        self.reports.append(message)
        print(f"INFO | export reported {level}: {message}", flush=True)

    def export(self, scene, obj):
        return self._cls.__dict__["exportGoZ"](self, scene, obj, self.project_dir)


def make_object(name):
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
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
    stub.export_polygroups = "NONE"
    stub.export_merge = False
    stub.export_run_zbrush = False
    stub.export_remove_internal_faces = False
    stub.clean_project_path = False
    utils._TEST_PREFS = stub

    # --- join_goz_path must not depend on a trailing separator -------------
    for directory, expected in (
        ("C:/GoZProjects/Default/", "C:/GoZProjects/Default/Cube.ztn"),
        ("C:/GoZProjects/Default", "C:/GoZProjects/Default/Cube.ztn"),
    ):
        got = paths.join_goz_path(directory, "Cube.ztn")
        check(f"join_goz_path({directory!r})", got == expected,
              f"got {got!r} expected {expected!r}")

    # The helper has to actually differ from the concatenation it replaced,
    # otherwise this test would pass against the broken code too.
    for directory in ("C:/GoZProjects/Default", "C:/GoZProjects/Default/"):
        naive = directory + "Cube.ztn"
        check(f"join_goz_path differs from concatenation for {directory!r}",
              paths.join_goz_path(directory, "Cube.ztn").endswith("/Cube.ztn")
              or directory.endswith("/"),
              f"naive={naive!r}")

    # --- the entry the exporter writes for ZBrush --------------------------
    # execute() builds the .ztn path and the GoZ_ObjectList entry with
    # join_goz_path. ZBrush strips the project-path prefix from the list entry
    # and appends ".GoZ" itself, so the entry must resolve to the mesh file.
    root = tempfile.mkdtemp(prefix="gob_entry_")
    project_no_sep = os.path.join(root, "GoZProjects", "Default")
    os.makedirs(project_no_sep)
    try:
        object_name = "EntryProbe"
        mesh_path = paths.join_goz_path(project_no_sep, f"{object_name}.GoZ")
        with open(mesh_path, "wb") as handle:
            handle.write(b"GoZb 1.0 ZBrush GoZ Binary")

        ztn_path = f"{paths.join_goz_path(project_no_sep, object_name)}.ztn"
        check("the .ztn path stays inside the project directory",
              os.path.dirname(ztn_path) == project_no_sep.replace("\\", "/"),
              f"ztn={ztn_path!r} project={project_no_sep!r}")

        # This is the form that used to be written and that ZBrush could not
        # resolve: the project prefix followed directly by the object name.
        naive_parent = project_no_sep + object_name
        check("the entry differs from the concatenated form that used to break",
              ztn_path != f"{naive_parent}.ztn",
              f"both are {ztn_path!r}")

        # Simulate ZBrush: strip the project prefix, append the extension.
        entry = paths.join_goz_path(project_no_sep, object_name)
        prefix = project_no_sep.replace("\\", "/") + "/"
        check("ZBrush's prefix-strip resolves back to the exported mesh",
              os.path.isfile(f"{prefix}{entry[len(prefix):]}.GoZ"),
              f"stripped entry {entry[len(prefix):]!r} + .GoZ not found")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # --- a real export, into a project path with NO trailing separator -----
    root = tempfile.mkdtemp(prefix="gob_export_")
    goz_root = os.path.join(root, "Pixologic")
    # No trailing separator: this is the case that used to break.
    project = os.path.join(goz_root, "GoZProjects", "Default")
    os.makedirs(project)

    stub.custom_pixologoc_path = True
    stub.pixologoc_path = goz_root
    stub.pixologoc_path_windows = goz_root
    stub.project_path = project
    stub.project_path_windows = project

    original_goz = paths.PATH_GOZ
    paths.set_goz_path(goz_root)
    try:
        object_name = "ExportProbe"
        obj = make_object(object_name)
        operator = ExportOperatorStub(gob_export.GoB_OT_export, project)
        exported = operator.export(bpy.context.scene, obj)
        print(f"INFO | exportGoZ returned {exported!r}")

        goz_file = os.path.join(project, f"{object_name}.GoZ")
        check("the .GoZ file landed inside the project directory",
              os.path.isfile(goz_file), f"expected {goz_file}")
        if os.path.isfile(goz_file):
            with open(goz_file, "rb") as handle:
                data = handle.read()
            check("the .GoZ file carries the GoZ magic",
                  data.startswith(b"GoZb 1.0 ZBrush GoZ Binary"))
            vertices = struct.unpack_from(
                "<Q", data, data.find(b"\x11\x27\x00\x00") + 8
            )[0]
            faces = struct.unpack_from(
                "<Q", data, data.find(b"\x21\x4e\x00\x00") + 8
            )[0]
            check("the exported mesh has the source geometry (8 verts, 6 faces)",
                  vertices == 8 and faces == 6, f"verts={vertices} faces={faces}")

        variables = os.path.join(
            goz_root, "GoZProjects", "Default", "GoB_variables.zvr"
        )
        check("GoB_variables.zvr was written", os.path.isfile(variables))
        if os.path.isfile(variables):
            with open(variables, "rb") as handle:
                blob = handle.read()
            check("the variables file records the project path",
                  project.replace("\\", "/").encode() in blob
                  or project.encode() in blob,
                  "project path missing from GoB_variables.zvr")

        # --- nothing may be dropped next to the project directory ----------
        parent = os.path.dirname(project)
        strays = [
            name for name in os.listdir(parent)
            if name.lower().endswith((".goz", ".ztn", ".ztl"))
        ]
        check("no .GoZ/.ztn files leaked into the parent directory", not strays,
              f"found {strays} in {parent}")

        stray_bmp = [
            name for name in os.listdir(os.path.dirname(parent))
            if name.lower().endswith(".bmp")
        ]
        check("no texture leaked outside the project directory", not stray_bmp,
              f"found {stray_bmp}")

        # --- the object list entry must resolve to a file that exists ------
        # ZBrush strips this prefix and appends the extension itself.
        list_path = os.path.join(goz_root, "GoZBrush", "GoZ_ObjectList.txt")
        if os.path.isfile(list_path):
            with open(list_path, "rt") as handle:
                entries = [line.strip() for line in handle if line.strip()]
            print(f"INFO | GoZ_ObjectList entries: {entries}")
            check("the object list has one entry", len(entries) >= 1)
            if entries:
                check(
                    "the object list entry resolves to the exported file",
                    os.path.isfile(f"{entries[0]}.GoZ"),
                    f"{entries[0]}.GoZ does not exist",
                )
        else:
            print("INFO | GoZ_ObjectList.txt not written by the stub call")
    finally:
        paths.set_goz_path(original_goz)
        shutil.rmtree(root, ignore_errors=True)

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
