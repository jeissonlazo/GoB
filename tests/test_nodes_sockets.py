"""Blender 5.2 compatibility tests for GoB's material node graph.

Run with:

    blender --background --python tests/test_nodes_sockets.py

The tests run against the *deployed* extension
(``bl_ext.user_default.gob``) so that the add-on preferences resolve the same
way they do for a real user. Install the extension first, or point ``GOB_EXT``
at the module name Blender reports for your install.

Why this test exists
--------------------
``nodes.py`` used to connect the Normal Map node with a hardcoded socket index
(``shader_node.inputs[22]``). The Principled BSDF input layout is not stable:

    Blender 4.2   30 inputs   inputs[5] = 'Normal'   inputs[22] = 'Coat Normal'
    Blender 5.2   32 inputs   inputs[6] = 'Normal'   inputs[22] = 'Coat IOR'

Blender 5.2 accepts that link without raising, so imported normal maps were
silently wired to the clearcoat IOR: the import "succeeded" and the normal map
simply had no effect. These tests fail if the wiring ever regresses.

Note on comparison: Blender hands out a NEW Python wrapper every time a node is
looked up, so identity checks such as ``link.to_node is shader`` are always
false. Every assertion here compares by node name or socket name.
"""

import os
import sys

import addon_utils
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


def links_into(mat, node_name, socket_name):
    """Names of the source nodes feeding ``socket_name`` of ``node_name``."""
    return [
        link.from_node.name
        for link in mat.node_tree.links
        if link.to_node.name == node_name and link.to_socket.name == socket_name
    ]


def node_named(mat, node_type, label=None):
    for node in mat.node_tree.nodes:
        if node.bl_idname != node_type:
            continue
        if label is None or node.label == label:
            return node.name
    return None


def build_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    return mat


def _load_addon():
    addon_utils.enable(EXT, default_set=False, persistent=True)
    modules = {
        name: sys.modules.get(f"{EXT}.{name}")
        for name in ("nodes", "geometry", "gob_import", "utils", "preferences")
    }
    missing = [name for name, mod in modules.items() if mod is None]
    if missing:
        raise RuntimeError(
            f"GoB extension {EXT!r} is not available (missing: {missing}). "
            "Install/enable GoB, or set GOB_EXT to the correct module name."
        )
    return modules


def _install_prefs_stub(addon_root, pref_cls, utils):
    """Provide preference values when Blender has no preferences entry.

    Blender only creates ``bpy.context.preferences.addons[<id>]`` when the
    extension is enabled through the add-on system, which is unreliable in
    ``--background`` runs. The stand-in reads defaults from the real class RNA
    in ``prefs_stub.py`` so it cannot drift from preferences.py.
    """
    if os.path.join(addon_root, "tests") not in sys.path:
        sys.path.insert(0, os.path.join(addon_root, "tests"))
    from prefs_stub import build_prefs_stub

    utils._TEST_PREFS = build_prefs_stub(pref_cls)


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)

    try:
        modules = _load_addon()
    except RuntimeError as exc:
        print(f"FAIL | add-on load | {exc}")
        return 1

    nodes = modules["nodes"]
    utils = modules["utils"]
    pref_cls = modules["preferences"].GoB_Preferences
    print(f"INFO | testing deployed copy at {nodes.__file__}")

    try:
        _install_prefs_stub(os.path.dirname(nodes.__file__), pref_cls, utils)
        print("INFO | preference stand-in installed from RNA defaults")
    except Exception as exc:  # pragma: no cover - diagnostic only
        print(f"INFO | prefs stand-in unavailable: {type(exc).__name__}: {exc}")

    for op in ("scene.gob_export", "scene.gob_import",
               "scene.gob_export_button", "gob.search_zbrush", "gob.install_goz"):
        category, name = op.split(".")
        check(f"operator {op} registered", hasattr(getattr(bpy.ops, category), name))

    # --- the regression -----------------------------------------------------
    mat = build_material("goz_test_mat")
    shader_name = node_named(mat, "ShaderNodeBsdfPrincipled")
    normal_index = list(
        mat.node_tree.nodes[shader_name].inputs
    ).index(mat.node_tree.nodes[shader_name].inputs["Normal"])
    print(f"INFO | 'Normal' is index {normal_index} in this build "
          f"(the old code hardcoded 22)")

    norm_img = bpy.data.images.new("goz_test_norm", 4, 4)
    diff_img = bpy.data.images.new("goz_test_diff", 4, 4)
    disp_img = bpy.data.images.new("goz_test_disp", 4, 4)

    nodes.material_from_texture(mat, diff_img, norm_img, disp_img)

    normal_sources = links_into(mat, shader_name, "Normal")
    normal_is_normal_map = (
        len(normal_sources) == 1
        and mat.node_tree.nodes[normal_sources[0]].bl_idname == "ShaderNodeNormalMap"
    )
    check("normal map node feeds Principled 'Normal'", normal_is_normal_map,
          f"sources={normal_sources!r}")
    print(f"INFO | links into Principled: "
          f"Base Color={links_into(mat, shader_name, 'Base Color')!r}, "
          f"Normal={normal_sources!r}")

    output_name = node_named(mat, "ShaderNodeOutputMaterial")
    check("Base Color is linked", bool(links_into(mat, shader_name, "Base Color")))
    check("Surface is linked", bool(links_into(mat, output_name, "Surface")))
    check("Displacement is linked",
          bool(links_into(mat, output_name, "Displacement")))

    # An index-based link silently lands on a Coat socket in 5.2, so assert the
    # coat inputs stay empty as a guard against reintroducing index lookups.
    shader_node = mat.node_tree.nodes[shader_name]
    coat_linked = [s.name for s in shader_node.inputs
                   if s.name.startswith("Coat") and s.is_linked]
    check("no Coat* socket is linked (index-wiring guard)", not coat_linked,
          f"linked coat sockets={coat_linked!r}")

    # --- polypaint ----------------------------------------------------------
    poly_mat = build_material("goz_test_polypaint")
    nodes.material_from_polypaint(poly_mat)
    poly_shader = node_named(poly_mat, "ShaderNodeBsdfPrincipled")
    vcol = node_named(poly_mat, "ShaderNodeVertexColor")
    check("polypaint vertex color node created", vcol is not None)
    check("polypaint drives Base Color",
          links_into(poly_mat, poly_shader, "Base Color") == [vcol],
          f"sources={links_into(poly_mat, poly_shader, 'Base Color')!r}")

    # --- idempotency and stale textures -------------------------------------
    before = len(mat.node_tree.nodes)
    nodes.material_from_texture(mat, diff_img, norm_img, disp_img)
    check("re-import does not duplicate nodes",
          len(mat.node_tree.nodes) == before,
          f"{before} -> {len(mat.node_tree.nodes)}")

    new_diff = bpy.data.images.new("goz_test_diff_v2", 4, 4)
    nodes.material_from_texture(mat, new_diff, norm_img, disp_img)
    diff_node = node_named(mat, "ShaderNodeTexImage", nodes.DIFFUSE_LABEL)
    check("re-import refreshes the diffuse image",
          diff_node is not None
          and mat.node_tree.nodes[diff_node].image is new_diff,
          f"image={mat.node_tree.nodes[diff_node].image if diff_node else None}")

    # --- helper that replaced nodes["Principled BSDF"].inputs[0] -----------
    color_mat = build_material("goz_test_color")
    nodes.set_node_base_color(color_mat, (0.25, 0.5, 0.75, 1.0))
    color_shader = node_named(color_mat, "ShaderNodeBsdfPrincipled")
    base = tuple(color_mat.node_tree.nodes[color_shader]
                 .inputs["Base Color"].default_value)
    check("set_node_base_color writes Base Color",
          abs(base[0] - 0.25) < 1e-6 and abs(base[2] - 0.75) < 1e-6, f"{base}")

    # --- no legacy Texture datablocks --------------------------------------
    check("no legacy Texture datablocks allocated",
          not [t for t in bpy.data.textures if "goz_test" in t.name])

    # --- a missing socket must fail loudly, not mis-wire silently ----------
    try:
        nodes._socket(shader_node, "Definitely Not A Socket", "INPUT")
        check("unknown socket raises RuntimeError", False, "no exception raised")
    except RuntimeError as exc:
        check("unknown socket raises RuntimeError",
              "has no input socket" in str(exc), str(exc))

    # --- lookup by node type survives duplicate/renamed nodes --------------
    dup_mat = build_material("goz_test_duplicate")
    dup_mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    duplicate_ok = True
    try:
        nodes.material_from_texture(dup_mat, diff_img, norm_img, disp_img)
    except Exception as exc:
        duplicate_ok = False
        print(f"INFO | duplicate-node case raised {type(exc).__name__}: {exc}")
    check("material build survives a duplicated Principled node", duplicate_ok)

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
