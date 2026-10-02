"""Blender 5.2 tests for GoB's mask encoding and transform handling.

    blender --background --python tests/test_mask_and_transform.py

Regression coverage for three defects that corrupted sculpt data:

1. ``GoZ`` stores *unmaskedness* while Blender stores *maskedness*. The import
   read the raw record straight into a vertex-group weight, so the mask came
   back complemented and alternated on every Blender -> ZBrush -> Blender trip.
2. ``apply_transformation`` called ``flip_normals()`` on every import. On the
   in-place coordinate update the mesh is reused, so the winding toggled each
   sync, which made ``mesh_topology_matches`` fail forever after and forced a
   full rebuild (losing vertex groups) on every later synchronisation.
3. The export ran ``apply_transformation`` on a mesh it had to own, so a
   mirrored axis remap could alter the user's own ``obj.data``.
"""

import os
import sys

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
        print(f"PASS | {label}")
    else:
        FAILURES.append(label)
        print(f"FAIL | {label} | {detail}")


def _load_addon():
    addon_utils.enable(EXT, default_set=False, persistent=True)
    modules = {
        name: sys.modules.get(f"{EXT}.{name}")
        for name in ("geometry", "mask_codec", "utils", "preferences")
    }
    missing = [name for name, mod in modules.items() if mod is None]
    if missing:
        raise RuntimeError(
            f"GoB extension {EXT!r} is not available (missing: {missing}). "
            "Install/enable GoB, or set GOB_EXT to the correct module name."
        )
    return modules


def _cube_mesh(name):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def _winding(mesh):
    return [tuple(poly.vertices) for poly in mesh.polygons]


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)

    modules = _load_addon()
    geometry = modules["geometry"]
    mask_codec = modules["mask_codec"]
    utils = modules["utils"]
    pref_cls = modules["preferences"].GoB_Preferences

    addon_root = os.path.dirname(geometry.__file__)
    if os.path.join(addon_root, "tests") not in sys.path:
        sys.path.insert(0, os.path.join(addon_root, "tests"))
    from prefs_stub import build_prefs_stub

    stub = build_prefs_stub(pref_cls)
    utils._TEST_PREFS = stub

    # --- 1. mask codec ------------------------------------------------------
    print("-- mask encoding --")
    import numpy as np

    for value in (0.0, 0.25, 0.5, 0.75, 1.0):
        wire = mask_codec.bl_to_goz_mask(np.array([value], dtype=np.float32))
        back = float(mask_codec.goz_to_bl_weight(np.asarray(wire))[0])
        check(f"mask {value:.2f} survives one round trip",
              abs(back - value) < 1e-4, f"got {back}")

    check("unmasked is 65535 on the wire",
          int(mask_codec.bl_to_goz_mask(np.array([0.0], dtype=np.float32))[0]) == 65535)
    check("fully masked is 0 on the wire",
          int(mask_codec.bl_to_goz_mask(np.array([1.0], dtype=np.float32))[0]) == 0)

    values = np.linspace(0.0, 1.0, 17, dtype=np.float32)
    current = values.copy()
    for _ in range(4):
        current = mask_codec.goz_to_bl_weight(mask_codec.bl_to_goz_mask(current))
    drift = float(np.max(np.abs(current - values)))
    check("four round trips do not drift", drift < 1e-4, f"max error {drift}")

    # The old code used the raw record as the weight, i.e. the complement.
    wire = mask_codec.bl_to_goz_mask(np.array([0.25], dtype=np.float32))
    old_value = float(wire[0]) / 65535.0
    check("the old raw-record reading was indeed the complement",
          abs(old_value - 0.75) < 1e-4, f"got {old_value}")

    # --- 2. winding is stable on the incremental path -----------------------
    print("-- winding across repeated imports --")
    stub.flip_x_axis = True          # forces a mirrored remap (det < 0)

    fresh = []
    for i in range(3):
        mesh = _cube_mesh(f"gob_test_rebuild_{i}")
        geometry.apply_transformation(mesh, is_import=True, flip_winding=True)
        fresh.append(_winding(mesh)[0])
    check("rebuilding always yields the same winding",
          len(set(fresh)) == 1, f"{fresh}")

    reused = _cube_mesh("gob_test_incremental")
    geometry.apply_transformation(reused, is_import=True, flip_winding=True)
    first = _winding(reused)[0]
    seen = []
    for _ in range(3):
        geometry.apply_transformation(reused, is_import=True, flip_winding=False)
        seen.append(_winding(reused)[0])
    check("in-place updates keep the winding stable",
          all(sig == first for sig in seen), f"first={first} then={seen}")

    # Demonstrate the old behaviour so the regression is unmistakable.
    toggling = _cube_mesh("gob_test_toggling")
    geometry.apply_transformation(toggling, is_import=True, flip_winding=True)
    toggle_seen = []
    for _ in range(3):
        geometry.apply_transformation(toggling, is_import=True, flip_winding=True)
        toggle_seen.append(_winding(toggling)[0])
    check("always flipping would toggle the winding (old bug reproduced)",
          len(set(toggle_seen)) > 1, f"{toggle_seen}")

    # --- 3. the export must not hand back the user's mesh -------------------
    print("-- export mesh ownership --")

    def make_object(name):
        mesh = _cube_mesh(name)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj

    for mode in ("IGNORE", "ONLY_EXPORT"):
        stub.export_modifiers = mode
        stub.flip_x_axis = True
        obj = make_object(f"gob_test_user_{mode}")
        before = _winding(obj.data)
        faces_before = len(obj.data.polygons)

        out = geometry.apply_modifiers(obj)
        out, _ = geometry.apply_transformation(out, is_import=False)

        check(f"[{mode}] export returns a separate mesh", out is not obj.data)
        check(f"[{mode}] user's mesh keeps its winding",
              _winding(obj.data) == before, f"{_winding(obj.data)[0]}")
        check(f"[{mode}] user's mesh keeps its face count",
              len(obj.data.polygons) == faces_before)

    # --- 4. no datablock leak over repeated exports -------------------------
    stub.export_modifiers = "ONLY_EXPORT"
    obj = make_object("gob_test_leak")
    deltas = []
    for _ in range(6):
        before = len(bpy.data.meshes)
        out = geometry.apply_modifiers(obj)
        bpy.data.meshes.remove(out)
        deltas.append(len(bpy.data.meshes) - before)
    check("repeated exports do not leak mesh datablocks",
          set(deltas) == {0}, f"deltas={deltas}")

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
