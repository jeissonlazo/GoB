"""Re-importing a GoZ object must reuse the Blender object, not add ".001".

This is the Blender half of a reported bug: sending a model from ZBrush,
editing it in Blender and sending it back created a SECOND subtool in ZBrush
instead of updating the loaded one.

The chain was:

  Blender import  -> "finger2.001" because "finger2" was already in the scene
  export          -> escape_object_name sanitises the dot, so "finger2_001"
  ZBrush          -> matches subtools by name, finds no "finger2_001", and
                     adds a new subtool instead of updating the loaded one

The fix is at the source: the import reuses the object that already carries the
name, so it stays "finger2" and the export matches the subtool again.

Everything here runs in a temporary GoZ root. It deliberately does not touch the
shared GoZ folder, so running the suite cannot disturb a live ZBrush session or
the objects the other tests read.
"""
import os
import shutil
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


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS | {label}", flush=True)
    else:
        FAILURES.append(label)
        print(f"FAIL | {label} | {detail}", flush=True)


def object_name_in_file(path):
    """Read the GoZMesh_ name, which is what the ZScript matches on."""
    with open(path, "rb") as handle:
        data = handle.read()
    stored_length = struct.unpack_from("<I", data, 36)[0]
    name_length = stored_length - 24
    prefix = len(b"GoZMesh_")
    return data[48 + prefix:48 + prefix + name_length].decode(
        "utf-8", errors="replace"
    )


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)

    addon_utils.enable(EXT, default_set=False, persistent=True)
    gob_import = sys.modules.get(f"{EXT}.gob_import")
    paths = sys.modules.get(f"{EXT}.paths")
    utils = sys.modules.get(f"{EXT}.utils")
    preferences = sys.modules.get(f"{EXT}.preferences")
    if None in (gob_import, paths, utils, preferences):
        print(f"FAIL | extension {EXT!r} is not available")
        return 1

    addon_root = os.path.dirname(gob_import.__file__)
    if os.path.join(addon_root, "tests") not in sys.path:
        sys.path.insert(0, os.path.join(addon_root, "tests"))
    from prefs_stub import build_prefs_stub

    stub = build_prefs_stub(preferences.GoB_Preferences)
    stub.export_modifiers = "IGNORE"
    stub.export_polygroups = "NONE"
    stub.export_mask = "NONE"
    stub.export_run_zbrush = False
    stub.export_remove_internal_faces = False
    stub.clean_project_path = False
    stub.import_material = "NONE"
    stub.project_path = ""
    stub.custom_pixologoc_path = True
    utils._TEST_PREFS = stub

    # --- the lookup helper, in isolation -----------------------------------
    print("-- find_object_for_name --")
    for existing in list(bpy.data.objects):
        bpy.data.objects.remove(existing, do_unlink=True)

    plain = bpy.data.objects.new("alpha", bpy.data.meshes.new("alphaMesh"))
    bpy.context.scene.collection.objects.link(plain)
    found = gob_import.GoB_OT_import.find_object_for_name("alpha")
    check("an exact name is found",
          found is not None and found.name == "alpha", f"{found}")

    suffixed = bpy.data.objects.new("beta", bpy.data.meshes.new("betaMesh"))
    bpy.context.scene.collection.objects.link(suffixed)
    suffixed.name = "beta.001"
    found = gob_import.GoB_OT_import.find_object_for_name("beta")
    check("a suffixed variant is found when the plain name is free",
          found is not None and found.name == "beta.001", f"{found}")

    # Blender suffixes when the plain name is taken by something unlinked, so
    # the helper has to find that too.
    hidden = bpy.data.meshes.new("gammaMesh")
    hidden_obj = bpy.data.objects.new("gamma", hidden)
    # Deliberately not linked to the scene.
    hidden_obj.name = "gamma.001"
    found = gob_import.GoB_OT_import.find_object_for_name("gamma")
    check("an unlinked suffixed object is still found",
          found is not None and found.name == "gamma.001", f"{found}")
    bpy.data.objects.remove(hidden_obj, do_unlink=True)

    check("an unrelated name is not matched",
          gob_import.GoB_OT_import.find_object_for_name("delta") is None)
    check("a similar name that is not a numeric suffix is not matched",
          gob_import.GoB_OT_import.find_object_for_name("alph") is None)

    # --- and the importer must not create a suffixed object ---------------
    print("-- make_mesh reuses the object --")
    root = tempfile.mkdtemp(prefix="gob_names_")
    goz_root = os.path.join(root, "Pixologic")
    project = os.path.join(goz_root, "GoZProjects", "Default")
    os.makedirs(project)
    stub.pixologoc_path = goz_root
    stub.pixologoc_path_windows = goz_root
    stub.project_path = project
    stub.project_path_windows = project
    original_goz = paths.PATH_GOZ
    paths.set_goz_path(goz_root)
    try:
        for existing in list(bpy.data.objects):
            bpy.data.objects.remove(existing, do_unlink=True)

        verts = np.array(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], dtype=np.float32
        )
        # A triangle, padded the way the GoZ format pads one.
        faces = np.array([[0, 1, 2, 0xFFFFFFFF]], dtype=np.uint32)

        class OperatorStub:
            """Only the members make_mesh touches."""

            def __init__(self, cls):
                for name in ("make_mesh",):
                    setattr(self, name, cls.__dict__[name].__get__(self, type(self)))
                for name in ("mesh_topology_matches", "_ensure_object_in_view_layer",
                             "find_object_for_name"):
                    member = cls.__dict__[name]
                    setattr(self, name, getattr(member, "__func__", member))

            def report(self, level, message):
                pass

        operator = OperatorStub(gob_import.GoB_OT_import)

        first_obj, _ = operator.make_mesh("GoBNameProbe", verts, faces)
        names_first = sorted(o.name for o in bpy.data.objects)
        print(f"INFO | after the first make_mesh: {names_first}")
        check("the first import uses the plain name",
              first_obj.name == "GoBNameProbe", f"{first_obj.name!r}")

        second_obj, _ = operator.make_mesh("GoBNameProbe", verts, faces)
        names_second = sorted(o.name for o in bpy.data.objects)
        print(f"INFO | after the second make_mesh: {names_second}")
        check("the second import reuses the same object",
              second_obj.name == first_obj.name,
              f"{first_obj.name!r} vs {second_obj.name!r}")
        check("no suffixed copy was created",
              not [n for n in names_second if n.startswith("GoBNameProbe.")],
              f"{names_second}")
        check("there is exactly one object for that name",
              names_second.count("GoBNameProbe") == 1, f"{names_second}")

        # --- the export must therefore write the plain name ---------------
        export_cls = sys.modules[f"{EXT}.gob_export"].GoB_OT_export

        class ExportSelf:
            def __init__(self):
                self.escape_object_name = export_cls.__dict__[
                    "escape_object_name"
                ].__get__(self, type(self))

        ExportSelf().escape_object_name(second_obj)
        print(f"INFO | name after escape_object_name: {second_obj.name!r}")
        check("the exported name still matches the ZBrush subtool",
              second_obj.name == "GoBNameProbe",
              f"got {second_obj.name!r}; ZBrush would look for that name and "
              "add a new subtool instead of updating the loaded one")
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
