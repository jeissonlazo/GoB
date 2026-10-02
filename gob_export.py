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
import os
import numpy as np
import time
import shutil
from struct import pack
from subprocess import Popen
from bpy.types import Operator
from bpy.props import BoolProperty
from . import geometry, mask_codec, paths, utils, ui, gob_import


_EXPORT_VERTEX_CHUNK = 1_000_000
_EXPORT_FACE_CHUNK = 500_000


def _mesh_topology_arrays(mesh):
    face_count = len(mesh.polygons)
    loop_starts = np.empty(face_count, dtype=np.int32)
    loop_totals = np.empty(face_count, dtype=np.int32)
    loop_vertices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.polygons.foreach_get('loop_start', loop_starts)
    mesh.polygons.foreach_get('loop_total', loop_totals)
    mesh.loops.foreach_get('vertex_index', loop_vertices)
    return loop_starts, loop_totals, loop_vertices


class GoB_OT_export(Operator):
    bl_idname = "scene.gob_export"
    bl_label = "Export to ZBrush"
    bl_description = "Export selected Objects to ZBrush"

    as_tool: BoolProperty(
        name="Export As Tool",
        description="Export as a tool instead of a subtool",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return geometry.export_poll(cls, context)

    def exportGoZ(self, scn, obj, path_export):
        PATH_PROJECT = utils.get_project_path()
        if utils.prefs().performance_profiling:
            print("\n", 100*"=")
            start_time = utils.profiler(time.perf_counter(), "Export Profiling: " + obj.name)
            start_total_time = utils.profiler(time.perf_counter(), 80*"=")

        mesh_tmp = geometry.apply_modifiers(obj)
        if utils.prefs().performance_profiling:
            start_time = utils.profiler(start_time, "Make Mesh apply_modifiers")

        mesh_tmp, mat_transform = geometry.apply_transformation(mesh_tmp, is_import=False)
        if utils.prefs().performance_profiling:
            start_time = utils.profiler(start_time, "Make Mesh apply_transformation")

        if utils.prefs().performance_profiling:
            start_time = utils.profiler(start_time, "Make Mesh export")

        fileExt = '.bmp'

        # write GoB ZScript variables
        with open(paths.PATH_VARS , 'wb') as GoBVars:
            GoBVars.write(pack('<4B', 0xE9, 0x03, 0x00, 0x00))
            # list size
            GoBVars.write(pack('<1B', 0x07))   #NOTE: n list items, update this when adding new items to list
            GoBVars.write(pack('<2B', 0x00, 0x00))
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "    variablesFile: Write list size")

            # 0: fileExtension
            GoBVars.write(pack('<2B',0x00, 0x53))   #.S
            GoBVars.write(b'.GoZ')
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "    variablesFile: Write fileExtension")

            # 1: textureFormat
            GoBVars.write(pack('<2B',0x00, 0x53))   #.S
            GoBVars.write(b'.bmp')
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "    variablesFile: Write textureFormat")

            # 2: diffTexture suffix
            GoBVars.write(pack('<2B',0x00, 0x53))   #.S
            name = utils.prefs().import_diffuse_suffix
            GoBVars.write(name.encode('utf-8'))
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "    variablesFile: Write diffTexture suffix")

            # 3: normTexture suffix
            GoBVars.write(pack('<2B',0x00, 0x53))   #.S
            name = utils.prefs().import_normal_suffix
            GoBVars.write(name.encode('utf-8'))
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "    variablesFile: Write normTexture suffix")

            # 4: dispTexture suffix
            GoBVars.write(pack('<2B',0x00, 0x53))   #.S
            name = utils.prefs().import_displace_suffix
            GoBVars.write(name.encode('utf-8'))
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "    variablesFile: Write dispTexture suffix")

            # 5: GoB version
            GoBVars.write(pack('<2B',0x00, 0x53))   #.S
            GoBVars.write(utils.gob_version().encode('utf-8'))
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "    variablesFile: Write GoB version")

            # 6: Project Path
            GoBVars.write(pack('<2B',0x00, 0x53))   #.S
            name = utils.get_project_path()
            GoBVars.write(name.encode('utf-8'))
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "    variablesFile: Write Project Path")
            # end
            GoBVars.write(pack('<B', 0x00))  #.
        if utils.prefs().performance_profiling:
            start_time = utils.profiler(start_time, "variablesFile: Write GoB_variables")

        try:
            object_name_bytes = obj.name.encode('ascii')
        except UnicodeEncodeError:
            self.escape_object_name(obj)
            object_name_bytes = obj.name.encode('ascii')

        with open(os.path.join(path_export + '/{0}.GoZ'.format(obj.name)), 'wb') as goz_file:
            numFaces = len(mesh_tmp.polygons)
            numVertices = len(mesh_tmp.vertices)
            loop_starts, loop_totals, loop_vertices = _mesh_topology_arrays(
                mesh_tmp
            )

            # --File Header--
            goz_file.write(b"GoZb 1.0 ZBrush GoZ Binary")
            goz_file.write(pack('<6B', 0x2E, 0x2E, 0x2E, 0x2E, 0x2E, 0x2E))
            goz_file.write(pack('<I', 1))  # obj tag
            goz_file.write(pack('<I', len(object_name_bytes)+24))
            goz_file.write(pack('<Q', 1))
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "Write File Header")

            # --Object Name--
            goz_file.write(b'GoZMesh_' + object_name_bytes)
            goz_file.write(pack('<4B', 0x89, 0x13, 0x00, 0x00))
            goz_file.write(pack('<I', 20))
            goz_file.write(pack('<Q', 1))
            goz_file.write(pack('<I', 0))
            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "Write Object Name")

            # --Vertices--
            goz_file.write(pack('<4B', 0x11, 0x27, 0x00, 0x00))
            goz_file.write(pack('<I', numVertices*3*4+16))
            goz_file.write(pack('<Q', numVertices))

            vertex_coords = np.empty((numVertices, 3), dtype=np.float32)
            mesh_tmp.vertices.foreach_get('co', vertex_coords.reshape(-1))
            matrix_world_np = np.asarray(obj.matrix_world, dtype=np.float32)
            mat_transform_np = np.asarray(mat_transform, dtype=np.float32)
            combined_transform = mat_transform_np @ matrix_world_np
            rotation_scale = combined_transform[:3, :3].T
            translation = combined_transform[:3, 3]

            for chunk_start in range(0, numVertices, _EXPORT_VERTEX_CHUNK):
                chunk_end = min(
                    chunk_start + _EXPORT_VERTEX_CHUNK, numVertices
                )
                final_coords = vertex_coords[chunk_start:chunk_end] @ rotation_scale
                final_coords += translation
                goz_file.write(
                    final_coords.astype('<f4', copy=False).tobytes()
                )
            del vertex_coords

            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "Write Vertices")

            # --Faces--
            goz_file.write(pack('<4B', 0x21, 0x4E, 0x00, 0x00))
            goz_file.write(pack('<I', numFaces*4*4+16))
            goz_file.write(pack('<Q', numFaces))

            for chunk_start in range(0, numFaces, _EXPORT_FACE_CHUNK):
                chunk_end = min(chunk_start + _EXPORT_FACE_CHUNK, numFaces)
                starts = loop_starts[chunk_start:chunk_end]
                totals = loop_totals[chunk_start:chunk_end]
                face_data = np.full(
                    (chunk_end - chunk_start, 4),
                    np.uint32(0xFFFFFFFF),
                    dtype=np.uint32,
                )
                for corner in range(4):
                    valid = totals > corner
                    face_data[valid, corner] = loop_vertices[
                        starts[valid] + corner
                    ]
                goz_file.write(face_data.astype('<u4', copy=False).tobytes())

            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "Write Faces")

            # --UVs--
            if mesh_tmp.uv_layers.active:
                uv_layer = mesh_tmp.uv_layers.active
                goz_file.write(pack('<4B', 0xA9, 0x61, 0x00, 0x00))
                goz_file.write(pack('<I', len(mesh_tmp.polygons)*4*2*4+16))
                goz_file.write(pack('<Q', len(mesh_tmp.polygons)))

                if utils.prefs().performance_profiling:
                    start_time = utils.profiler(start_time, "    UV: polygones")

                uv_coords = np.empty(
                    (len(uv_layer.data), 2), dtype=np.float32
                )
                uv_layer.data.foreach_get('uv', uv_coords.reshape(-1))
                if utils.prefs().export_uv_flip_x:
                    uv_coords[:, 0] = 1.0 - uv_coords[:, 0]
                if utils.prefs().export_uv_flip_y:
                    uv_coords[:, 1] = 1.0 - uv_coords[:, 1]

                for chunk_start in range(0, numFaces, _EXPORT_FACE_CHUNK):
                    chunk_end = min(chunk_start + _EXPORT_FACE_CHUNK, numFaces)
                    starts = loop_starts[chunk_start:chunk_end]
                    totals = loop_totals[chunk_start:chunk_end]
                    uv_data = np.empty(
                        (chunk_end - chunk_start, 4, 2), dtype=np.float32
                    )
                    uv_data[:, :, 0] = 0.0
                    uv_data[:, :, 1] = 1.0
                    for corner in range(4):
                        valid = totals > corner
                        uv_data[valid, corner] = uv_coords[
                            starts[valid] + corner
                        ]
                    goz_file.write(
                        uv_data.astype('<f4', copy=False).tobytes()
                    )

                if utils.prefs().performance_profiling:
                    start_time = utils.profiler(start_time, "    UV: write uvs")

            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "Write UV")

            # --Polypaint--
            if obj.data.color_attributes.active_color_name and obj.data.color_attributes.active_color_index >= 0:

                vcolArray = geometry.get_vertex_colors(mesh_tmp, obj, numVertices)
                if utils.prefs().performance_profiling:
                    start_time = utils.profiler(start_time, "    Polypaint:  vcolArray")

                goz_file.write(pack('<4B', 0xb9, 0x88, 0x00, 0x00))
                goz_file.write(pack('<I', numVertices*4+16))
                goz_file.write(pack('<I', numVertices))
                goz_file.write(pack("<f", 0))
                if utils.prefs().performance_profiling:
                    start_time = utils.profiler(start_time, "    Polypaint:  write numVertices")

                for chunk_start in range(0, numVertices, _EXPORT_VERTEX_CHUNK):
                    chunk_end = min(
                        chunk_start + _EXPORT_VERTEX_CHUNK, numVertices
                    )
                    colors = vcolArray[chunk_start:chunk_end]
                    vcol_data = np.zeros(
                        (chunk_end - chunk_start, 4), dtype=np.uint8
                    )
                    vcol_data[:, 0] = colors[:, 2]
                    vcol_data[:, 1] = colors[:, 1]
                    vcol_data[:, 2] = colors[:, 0]
                    goz_file.write(vcol_data.tobytes())

                if utils.prefs().performance_profiling:
                    start_time = utils.profiler(start_time, "    Polypaint: write color")

                del vcolArray
                if utils.prefs().performance_profiling:
                    start_time = utils.profiler(start_time, "    Polypaint:  vcolArray.clear")

                if utils.prefs().performance_profiling:
                    start_time = utils.profiler(start_time, "Write Polypaint")

            # --Mask--
            if utils.prefs().export_mask !='NONE':
                # since blender 4.1, Sculpt mask values are stored in a generic attribute
                # https://developer.blender.org/docs/release_notes/4.1/python_api/#mesh
                if '.sculpt_mask' in mesh_tmp.attributes and utils.prefs().export_mask == 'SCULPT_MASK' and bpy.app.version >= (4, 1, 0):
                    goz_file.write(pack('<4B', 0x32, 0x75, 0x00, 0x00))
                    goz_file.write(pack('<I', numVertices*2+16))
                    goz_file.write(pack('<Q', numVertices))

                    mask_data = np.zeros(numVertices, dtype=np.float32)
                    mask_attr = mesh_tmp.attributes.get(".sculpt_mask")

                    if mask_attr and len(mask_attr.data) == len(mask_data):
                        mask_attr.data.foreach_get('value', mask_data)
                    else:
                        mask_data[:] = [0.0] * len(mask_data)

                    np.maximum(mask_data, 0.0, out=mask_data)
                    mask_values = mask_codec.bl_to_goz_mask(mask_data)
                    goz_file.write(mask_values.tobytes())

                else:
                    for vertexGroup in obj.vertex_groups:
                        if vertexGroup.name.lower() in {'mask'}:
                            goz_file.write(pack('<4B', 0x32, 0x75, 0x00, 0x00))
                            goz_file.write(pack('<I', numVertices*2+16))
                            goz_file.write(pack('<Q', numVertices))
                            # Vertices outside the group are unmasked, so start
                            # from zeros and fill only the members.
                            mask_data = np.zeros(numVertices, dtype=np.float32)
                            group_index = vertexGroup.index
                            member_count = 0
                            for vertex in mesh_tmp.vertices:
                                for membership in vertex.groups:
                                    if membership.group == group_index:
                                        mask_data[vertex.index] = membership.weight
                                        member_count += 1
                                        break
                            if not member_count and utils.prefs().debug_output:
                                print(
                                    "GoB: 'mask' vertex group has no weights; "
                                    "exporting an unmasked mesh."
                                )
                            mask_values = mask_codec.bl_to_goz_mask(mask_data)
                            goz_file.write(mask_values.tobytes())

            if utils.prefs().performance_profiling:
                start_time = utils.profiler(start_time, "Write Mask")

            # --Polygroups--
            if utils.prefs().export_polygroups != 'NONE':
                if utils.prefs().debug_output:
                    print("Export Polygroups: ", utils.prefs().export_polygroups)

                # Polygroups from Face Sets
                if utils.prefs().export_polygroups == 'FACE_SETS':

                    goz_file.write(pack('<4B', 0x41, 0x9C, 0x00, 0x00))
                    goz_file.write(pack('<I', numFaces*2+16))
                    goz_file.write(pack('<Q', numFaces))

                    face_attr = geometry.get_sculpt_face_set_attribute(mesh_tmp)
                    if utils.prefs().debug_output:
                        print("Exporting Face Sets: ", face_attr)

                    if face_attr is not None and len(face_attr.data) == numFaces:
                        face_set_data = np.zeros(numFaces, dtype=np.int32)
                        face_attr.data.foreach_get("value", face_set_data)

                        face_set_data[face_set_data < 0] = 65504
                        face_set_data = face_set_data.astype('<u2')
                        goz_file.write(face_set_data.tobytes())

                        if utils.prefs().debug_output:
                            print(f"Face sets exported: {numFaces} faces")
                            unique_values = np.unique(face_set_data)
                            print(f"Unique face set values: {unique_values}")

                    else:   #assign empty when no face sets are found
                        default_face_set_data = np.full(
                            numFaces, 65504, dtype='<u2'
                        )
                        goz_file.write(default_face_set_data.tobytes())

                        if utils.prefs().debug_output:
                            print(f"Default face sets written: {numFaces} faces")

                    if utils.prefs().performance_profiling:
                        start_time = utils.profiler(start_time, "Write Polygroup FaceSets")

                # Polygroups from Vertex Groups
                if utils.prefs().export_polygroups == 'VERTEX_GROUPS':
                    goz_file.write(pack('<4B', 0x41, 0x9C, 0x00, 0x00))
                    goz_file.write(pack('<I', numFaces*2+16))
                    goz_file.write(pack('<Q', numFaces))

                    groupColor=[]
                    # create a color for each facemap (0xffff)
                    for vg in obj.vertex_groups:
                        color = utils.random_color()
                        groupColor.append(color)
                    # add a color for elements that are not part of a vertex group
                    groupColor.append(0)

                    polygroup_values = np.full(
                        numFaces, 65504, dtype=np.uint16
                    )
                    if len(obj.vertex_groups) > 0:
                        for face in mesh_tmp.polygons:
                            face_groups = []
                            for vert in face.vertices:
                                for vg in mesh_tmp.vertices[vert].groups:
                                    if (
                                        vg.weight
                                        >= utils.prefs().export_weight_threshold
                                        and vg.group < len(obj.vertex_groups)
                                        and obj.vertex_groups[
                                            vg.group
                                        ].name.lower()
                                        != 'mask'
                                    ):
                                        face_groups.append(vg.group)

                            if face_groups:
                                group = max(
                                    face_groups, key=face_groups.count
                                )
                                if face_groups.count(group) == len(face.vertices):
                                    polygroup_values[face.index] = groupColor[group]

                    goz_file.write(
                        polygroup_values.astype('<u2', copy=False).tobytes()
                    )

                    if utils.prefs().performance_profiling:
                        start_time = utils.profiler(
                            start_time, "Write Polygroup Vertex groups"
                        )

                # Polygroups from materials
                if utils.prefs().export_polygroups == 'MATERIALS':
                    if len(obj.material_slots) > 0:
                        goz_file.write(pack('<4B', 0x41, 0x9C, 0x00, 0x00))
                        goz_file.write(pack('<I', numFaces*2+16))
                        goz_file.write(pack('<Q', numFaces))

                        groupColor=[]
                        for mat in obj.material_slots:
                            if mat:
                                color = utils.random_color()
                                groupColor.append(color)
                            else:
                                groupColor.append(65504)

                        material_indices = np.empty(
                            numFaces, dtype=np.int32
                        )
                        mesh_tmp.polygons.foreach_get(
                            'material_index', material_indices
                        )
                        colors = np.asarray(groupColor, dtype=np.uint16)
                        valid = material_indices < len(colors)
                        polygroup_values = np.full(
                            numFaces, 65504, dtype=np.uint16
                        )
                        polygroup_values[valid] = colors[
                            material_indices[valid]
                        ]
                        goz_file.write(
                            polygroup_values.astype(
                                '<u2', copy=False
                            ).tobytes()
                        )

                    if utils.prefs().performance_profiling:
                        start_time = utils.profiler(start_time, "Write Polygroup materials")

            # Diffuse, displacement and normal maps.
            #
            # Textures are matched to the configured suffixes and saved next to
            # the project as .bmp, then the path is recorded in the file for
            # ZBrush to load. A texture that fails to save is not linked: the
            # old code wrote the path regardless, so ZBrush was told to load a
            # file that does not exist.
            prefs = utils.prefs()
            textures = {}
            for slot in obj.material_slots:
                material = slot.material
                # Check the node tree, not Material.use_nodes: reading that
                # property emits a DeprecationWarning in Blender 5.2.
                if material is None or material.node_tree is None:
                    continue
                for node in material.node_tree.nodes:
                    if node.type != 'TEX_IMAGE' or node.image is None:
                        continue
                    for kind, suffix in (
                        ("diffuse", prefs.import_diffuse_suffix),
                        ("displace", prefs.import_displace_suffix),
                        ("normal", prefs.import_normal_suffix),
                    ):
                        if suffix and suffix in node.image.name:
                            # First match wins, so a later material slot cannot
                            # silently replace an earlier one.
                            textures.setdefault(kind, node.image)

            previous_format = scn.render.image_settings.file_format
            scn.render.image_settings.file_format = 'BMP'
            texture_ext = '.bmp'
            try:
                for kind, tag in (
                    ("diffuse", b'\xc9\xaf\x00\x00'),
                    ("displace", b'\xd9\xd6\x00\x00'),
                    ("normal", b'\x51\xc3\x00\x00'),
                ):
                    image = textures.get(kind)
                    if image is None:
                        continue
                    suffix = {
                        "diffuse": prefs.import_diffuse_suffix,
                        "displace": prefs.import_displace_suffix,
                        "normal": prefs.import_normal_suffix,
                    }[kind]
                    texture_path = paths.join_goz_path(
                        PATH_PROJECT, f"{obj.name}{suffix}{texture_ext}"
                    )
                    try:
                        image.save_render(texture_path)
                    except Exception as error:
                        # Do not advertise a texture ZBrush cannot open.
                        print(
                            f"GoB: could not save the {kind} texture to "
                            f"'{texture_path}': {error}"
                        )
                        continue

                    encoded = texture_path.encode('utf8')
                    goz_file.write(tag)
                    goz_file.write(pack('<I', len(encoded) + 16))
                    goz_file.write(pack('<Q', 1))
                    goz_file.write(pack('%ss' % len(encoded), encoded))
                    if prefs.performance_profiling:
                        start_time = utils.profiler(
                            start_time, f"Write {kind}_texture"
                        )
            finally:
                scn.render.image_settings.file_format = previous_format

            # end
            goz_file.write(pack('16x'))

            if utils.prefs().performance_profiling:
                utils.profiler(start_time, "Write Textures")
                print(30*"-")
                utils.profiler(start_total_time, "Total Export Time")
                print(30*"=")

        bpy.data.meshes.remove(mesh_tmp)
        # The render format is restored by the finally block around the texture
        # export above, so an exception there cannot leave it on BMP.
        return

    def _prepare_goz_directories(self, goz_root):
        """Make sure the folders the handshake writes into exist.

        Without this a missing GoZBrush or GoZApps folder -- which happens when
        the public Pixologic folder is deleted, moved, or never created because
        ZBrush has not run yet -- made the export die with a bare
        FileNotFoundError from somewhere in the middle of the operator.
        """
        for relative in ("GoZBrush", os.path.join("GoZApps", "Blender")):
            directory = os.path.join(goz_root, relative)
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as error:
                print(f"GoB: could not create {directory}: {error}")
                return False
        return True

    def _write_app_registration(self, goz_root):
        """Write the files that tell ZBrush Blender is the GoZ target app."""
        app_dir = os.path.join(goz_root, "GoZApps", "Blender")
        source_info = os.path.join(paths.PATH_GOB, "Blender", "GoZ_Info.txt")
        target_info = os.path.join(app_dir, "GoZ_Info.txt")

        # Refresh GoZ_Info.txt, and create it the first time.
        try:
            if os.path.isdir(os.path.join(paths.PATH_GOB, "Blender")):
                shutil.copy2(source_info, target_info)
        except OSError as error:
            print(f"GoB: could not refresh GoZ_Info.txt: {error}")

        blender_path = os.fspath(paths.PATH_BLENDER).replace('\\', '/')
        try:
            with open(os.path.join(app_dir, "GoZ_Config.txt"), 'wt') as config:
                config.write(f'PATH = "{blender_path}"')
            with open(
                os.path.join(goz_root, "GoZBrush", "GoZ_Application.txt"), 'wt'
            ) as application:
                application.write("Blender")
        except OSError as error:
            print(f"GoB: could not register Blender with GoZ: {error}")

    def _write_project_path(self, goz_root, project_path):
        try:
            with open(
                os.path.join(goz_root, "GoZBrush", "GoZ_ProjectPath.txt"), 'wt'
            ) as project_file:
                project_file.write(project_path)
        except OSError as error:
            print(f"GoB: could not write GoZ_ProjectPath.txt: {error}")

    def _set_import_as_subtool(self):
        """Set IMPORT_AS_SUBTOOL in GoZBrush\\GoZ_Config.txt.

        ZBrush creates this file; before it has run it may not exist. The old
        code caught that and then wrote the wrong file, so the setting silently
        never took effect on a fresh install.
        """
        import_as_subtool = 'IMPORT_AS_SUBTOOL = TRUE'
        import_as_tool = 'IMPORT_AS_SUBTOOL = FALSE'
        wanted = import_as_tool if self.as_tool else import_as_subtool
        unwanted = import_as_subtool if self.as_tool else import_as_tool

        try:
            with open(paths.PATH_CONFIG, "rt") as handle:
                config = handle.read().replace('\t', ' ')
        except OSError:
            # Not there yet: start from the setting ZBrush defaults to.
            config = f"SHOW_HELP_WINDOW = FALSE\n{import_as_subtool}\n"

        # Rewrite every occurrence, then collapse them to one line. The previous
        # version fell through to appending when the value was already correct,
        # so each export added another IMPORT_AS_SUBTOOL line and the file grew
        # without bound.
        lines = []
        seen_setting = False
        for line in config.splitlines():
            stripped = line.strip()
            if stripped in (import_as_subtool, import_as_tool):
                if seen_setting:
                    continue          # drop duplicates
                lines.append(wanted)
                seen_setting = True
                continue
            lines.append(line)
        if not seen_setting:
            lines.append(wanted)
        new_config = "\n".join(lines).rstrip("\n") + "\n"

        try:
            with open(paths.PATH_CONFIG, "wt") as handle:
                handle.write(new_config)
        except OSError as error:
            print(f"GoB: could not write {paths.PATH_CONFIG}: {error}")

    def execute(self, context):

        paths.set_goz_path_from_preferences()

        # Resolve the GoZ root from the preferences instead of reading the
        # module globals, so this cannot act on a path another caller has since
        # replaced.
        goz_root = paths.goz_root_from_preferences()
        PATH_PROJECT = utils.get_project_path()

        if not self._prepare_goz_directories(goz_root):
            ui.ShowReport(
                self,
                [goz_root],
                "GoB: cannot write to the GoZ folder",
                'COLORSET_01_VEC',
            )
            return {'CANCELLED'}

        for directory in (goz_root, PATH_PROJECT):
            if not os.path.isdir(directory):
                try:
                    os.makedirs(directory, exist_ok=True)
                except OSError as error:
                    print(f"GoB: could not create {directory}: {error}")
                    ui.ShowReport(
                        self,
                        [directory],
                        "GoB: project folder is not writable",
                        'COLORSET_01_VEC',
                    )
                    return {'CANCELLED'}

        self._write_app_registration(goz_root)
        self._write_project_path(goz_root, PATH_PROJECT)

        if utils.prefs().clean_project_path and os.path.isdir(PATH_PROJECT):
            for file_name in os.listdir(PATH_PROJECT):
                if file_name.lower().endswith(('.goz', '.ztn', '.ztl')):
                    print('cleaning file:', file_name)
                    os.remove(os.path.join(PATH_PROJECT, file_name))

        self._set_import_as_subtool()

        currentContext = None
        if context.object:
            currentContext = context.object.mode
            if context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')

        wm = context.window_manager
        wm.progress_begin(0,100)
        step =  100  / len(context.selected_objects)
        surface_types = ['SURFACE', 'CURVE', 'FONT', 'META']

        def write_object_entry(obj_for_entry):
            """Write the .ztn marker and append the object to the GoZ list.

            ZBrush reads GoZ_ObjectList.txt, strips the project path prefix and
            appends the extension itself, so the entry must be the path without
            the extension. Building it with os.path.join means a project path
            that lacks a trailing separator no longer produces an entry ZBrush
            cannot resolve.
            """
            object_path = paths.join_goz_path(PATH_PROJECT, obj_for_entry.name)
            with open(f"{object_path}.ztn", 'wt') as ztn:
                ztn.write(object_path)
            GoZ_ObjectList.write(f'{object_path}\n')

        with open(paths.PATH_OBJLIST, 'wt') as GoZ_ObjectList:
            for i, obj in enumerate(context.selected_objects):
                if obj.type in surface_types:

                    depsgraph = context.evaluated_depsgraph_get()
                    obj_to_convert = obj.evaluated_get(depsgraph)
                    mesh_tmp = bpy.data.meshes.new_from_object(obj_to_convert)
                    mesh_tmp.transform(obj.matrix_world)
                    obj_tmp = bpy.data.objects.new(f'{obj.name}_{obj.type}', mesh_tmp)

                    if utils.prefs().export_merge:
                        geometry.mesh_welder(obj_tmp)

                    if len(mesh_tmp.polygons):
                        print("GoB: ", obj_tmp.name, mesh_tmp.name, len(mesh_tmp.polygons), sep=' / ')
                        self.escape_object_name(obj_tmp)
                        self.exportGoZ(context.scene, obj_tmp, f'{PATH_PROJECT}')
                        write_object_entry(obj_tmp)
                        # The temporary object is never linked to the scene, so
                        # it has to be freed explicitly or every export leaks one.
                        bpy.data.objects.remove(obj_tmp, do_unlink=True)
                        bpy.data.meshes.remove(mesh_tmp)

                elif obj.type in {'MESH'}:
                    depsgraph = bpy.context.evaluated_depsgraph_get()

                    if utils.prefs().export_modifiers != 'IGNORE':
                        object_eval = obj.evaluated_get(depsgraph)
                        numFaces = len(object_eval.data.polygons)
                    else:
                        numFaces = len(obj.data.polygons)

                    if numFaces > 0:
                        geometry.process_linked_objects(obj)
                        geometry.remove_internal_faces(obj)

                        # No bpy.ops.geometry.color_attribute_convert() here.
                        # That operator acts on the *active* object rather than
                        # the one being exported, so with several objects
                        # selected it converted the wrong datablock N times and
                        # permanently modified the user's mesh. The exporter
                        # already reads BYTE/CORNER colors correctly through
                        # geometry.get_vertex_colors().

                        self.escape_object_name(obj)
                        self.exportGoZ(context.scene, obj, f'{PATH_PROJECT}')
                        write_object_entry(obj)
                    else:
                        ui.ShowReport(self, ["Object: ", obj.name], "GoB: ZBrush can not import objects without faces", 'COLORSET_01_VEC')

                else:
                    ui.ShowReport(self, [obj.type, obj.name], "GoB: unsupported obj.type found:", 'COLORSET_01_VEC')

                wm.progress_update(step * i)
            wm.progress_end()

        try:
            gob_import.cached_last_edition_time = os.path.getmtime(paths.PATH_OBJLIST)
        except Exception as e:
            print(e)

        if not paths.is_file_empty(paths.PATH_OBJLIST):
            path_exists = paths.find_zbrush(self, context, paths.isMacOS)
            if utils.prefs().export_run_zbrush:
                if not path_exists:
                    bpy.ops.gob.search_zbrush('INVOKE_DEFAULT')
                else:
                    zbrush_exec = utils.get_zbrush_exec()
                    paths.deploy_zfileutils(zbrush_exec)
                    launch_script = paths.get_launch_script()
                    if not os.path.isfile(launch_script):
                        ui.ShowReport(
                            self,
                            [launch_script],
                            "GoB: missing GoB_Import zscript",
                            'COLORSET_01_VEC',
                        )
                    elif paths.isMacOS:
                        print("OSX Popen: ", zbrush_exec, launch_script)
                        Popen(['open', '-a', zbrush_exec, launch_script])
                    else:
                        print("Windows Popen: ", zbrush_exec, launch_script)
                        Popen([zbrush_exec, launch_script])

        if context.object and currentContext:
            bpy.ops.object.mode_set(mode=currentContext)

        return {'FINISHED'}

    def escape_object_name(self, obj):
        import re

        original_name = obj.name
        new_name = re.sub(r'[^A-Za-z0-9_-]+', '_', original_name)
        new_name = re.sub(r'_+', '_', new_name).strip('_-')

        if not new_name:
            new_name = "Object"

        if new_name == original_name:
            return

        base_name = new_name
        i = 0
        while new_name in bpy.data.objects and bpy.data.objects[new_name] != obj:
            new_name = f"{base_name}_{i:02d}"
            i += 1
        obj.name = new_name