# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

import bpy
from . import utils


# Labels used to recognise the nodes this addon creates.
DIFFUSE_LABEL = 'Diffuse Color Map'
NORMAL_LABEL = 'Normal Map'
DISPLACEMENT_LABEL = 'Displacement Map'


def _socket(node, name, io):
    """Return a node socket by name, with a diagnosable error.

    Sockets are looked up by name instead of by index on purpose: the
    Principled BSDF input layout is not stable between Blender releases
    (``Normal`` is index 5 in 4.2 and index 6 in 5.2, and ``inputs[22]``
    silently means ``Coat Normal`` in 4.2 but ``Coat IOR`` in 5.2), so an
    index-based link fails silently or lands on the wrong socket.
    """
    collection = node.inputs if io == 'INPUT' else node.outputs
    socket = collection.get(name)
    if socket is None:
        available = ", ".join(repr(s.name) for s in collection)
        raise RuntimeError(
            f"GoB: node {node.bl_idname!r} has no {io.lower()} socket "
            f"{name!r}. Available: {available}"
        )
    return socket


def _find_node(node_tree, node_type, label=None):
    """Find a node by type, optionally constrained to one of our labels."""
    for node in node_tree.nodes:
        if node.bl_idname != node_type:
            continue
        if label is None or node.label == label:
            return node
    return None


def _ensure_node(node_tree, node_type, label=None, location=(0, 0)):
    """Return an existing node of ``node_type``/``label``, or create one."""
    node = _find_node(node_tree, node_type, label)
    if node is None:
        node = node_tree.nodes.new(node_type)
        node.location = location
        if label is not None:
            node.label = label
    return node


def _set_image(node, image, colorspace=None):
    """Point a texture node at ``image`` and rewire its output.

    Image and colorspace are re-applied on every call so a re-import after
    painting in ZBrush actually shows the new texture instead of keeping the
    one captured on first import.
    """
    node.image = image
    if image is not None and colorspace:
        image.colorspace_settings.name = colorspace


def create_base_nodes(mat):
    """Create the base nodes for the material."""
    mat.use_nodes = True
    tree = mat.node_tree

    output_node = _find_node(tree, 'ShaderNodeOutputMaterial')
    if output_node is None:
        output_node = tree.nodes.new('ShaderNodeOutputMaterial')
        output_node.location = 400, 400

    # Search by node type rather than by name: Blender may rename the default
    # node ("Principled BSDF.001") and users may rename or relabel it.
    shader_node = _find_node(tree, 'ShaderNodeBsdfPrincipled')
    if shader_node is None:
        shader_node = tree.nodes.new('ShaderNodeBsdfPrincipled')
        shader_node.location = 0, 400

    tree.links.new(
        _socket(output_node, 'Surface', 'INPUT'),
        _socket(shader_node, 'BSDF', 'OUTPUT'),
    )

    return tree.nodes, output_node, shader_node


def material_from_texture(mat, diff_texture=None, norm_texture=None, disp_texture=None):
    """Create the texture nodes and connect them to the shader node."""
    nodes, output_node, shader_node = create_base_nodes(mat)
    tree = mat.node_tree
    prefs = utils.prefs()

    # --- Diffuse / Base Color ---
    diff_texture_node = _ensure_node(
        tree, 'ShaderNodeTexImage', DIFFUSE_LABEL, (-700, 500)
    )
    _set_image(diff_texture_node, diff_texture, prefs.import_diffuse_colorspace)
    tree.links.new(
        _socket(shader_node, 'Base Color', 'INPUT'),
        _socket(diff_texture_node, 'Color', 'OUTPUT'),
    )

    # --- Normal ---
    norm_node = _find_node(tree, 'ShaderNodeNormalMap')
    if norm_node is None:
        norm_node = tree.nodes.new('ShaderNodeNormalMap')
        norm_node.location = -300, -100
    tree.links.new(
        _socket(shader_node, 'Normal', 'INPUT'),
        _socket(norm_node, 'Normal', 'OUTPUT'),
    )

    norm_texture_node = _ensure_node(
        tree, 'ShaderNodeTexImage', NORMAL_LABEL, (-700, -100)
    )
    _set_image(norm_texture_node, norm_texture, prefs.import_normal_colorspace)
    tree.links.new(
        _socket(norm_node, 'Color', 'INPUT'),
        _socket(norm_texture_node, 'Color', 'OUTPUT'),
    )

    # --- Displacement ---
    disp_node = _ensure_node(tree, 'ShaderNodeDisplacement', None, (-300, 200))
    tree.links.new(
        _socket(output_node, 'Displacement', 'INPUT'),
        _socket(disp_node, 'Displacement', 'OUTPUT'),
    )

    disp_texture_node = _ensure_node(
        tree, 'ShaderNodeTexImage', DISPLACEMENT_LABEL, (-700, 200)
    )
    _set_image(disp_texture_node, disp_texture, prefs.import_displace_colorspace)
    tree.links.new(
        _socket(disp_node, 'Height', 'INPUT'),
        _socket(disp_texture_node, 'Color', 'OUTPUT'),
    )


def material_from_polypaint(mat):
    """Create a vertex color node and connect it to the shader node."""
    nodes, output_node, shader_node = create_base_nodes(mat)
    tree = mat.node_tree
    polypaint_name = utils.prefs().import_polypaint_name

    # Match on the layer name so the node created by a previous import is
    # reused and re-pointed instead of piling up duplicates.
    vcol_node = None
    for node in tree.nodes:
        if (
            node.bl_idname == 'ShaderNodeVertexColor'
            and node.layer_name == polypaint_name
        ):
            vcol_node = node
            break

    if vcol_node is None:
        vcol_node = tree.nodes.new('ShaderNodeVertexColor')
        vcol_node.location = -300, 200

    vcol_node.layer_name = polypaint_name
    tree.links.new(
        _socket(shader_node, 'Base Color', 'INPUT'),
        _socket(vcol_node, 'Color', 'OUTPUT'),
    )


def set_node_base_color(mat, rgba):
    """Set the base color of the material's shader node.

    Kept here so the hard-coded ``nodes["Principled BSDF"]`` lookup only
    exists in one place.
    """
    mat.use_nodes = True
    shader_node = _find_node(mat.node_tree, 'ShaderNodeBsdfPrincipled')
    if shader_node is None:
        return None
    socket = _socket(shader_node, 'Base Color', 'INPUT')
    socket.default_value = rgba
    return shader_node
