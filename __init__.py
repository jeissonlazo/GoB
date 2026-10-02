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


import os

import bpy
import bpy.utils.previews

from . import gob_export, gob_import, paths, preferences, ui, utils

bl_info = {
    "name": "GoB",
    "description": """GoB (for GoBlender) is an unofficial GoZ-like extension, providing a seamless bridge between ZBrush and Blender.
          Effortlessly transfer your models between ZBrush and Blender with a single click, streamlining your workflow and maximizing efficiency.""",
    "author": "ODe, JoseConseco, Daniel Grauer (kromar)",
    "version": (4, 6, 0),
    "blender": (4, 2, 0),
    "location": "In the info header",
    "doc_url": "https://github.com/JoseConseco/GoB/wiki",
    "tracker_url": "https://github.com/JoseConseco/GoB/issues/new",
    "category": "Import-Export",
}


classes = (
    gob_import.GoB_OT_import,
    gob_export.GoB_OT_export,
    ui.GoB_OT_export_button,
    ui.GOB_OT_Popup,
    paths.GoB_OT_GoZ_Installer,
    preferences.GoB_Preferences,
)


_registered = False


def register():
    global _registered
    if _registered:
        # Blender can reach register() again after a partial unregister() (for
        # example when a reload failed halfway). Registering the same classes
        # twice raises, so treat the call as idempotent.
        return

    [bpy.utils.register_class(c) for c in classes]
    _registered = True

    global icons
    icons = bpy.utils.previews.new()
    icons_dir = os.path.join(os.path.dirname(__file__), "icons")
    icons.load("GOZ_SEND", os.path.join(icons_dir, "goz_send.png"), "IMAGE")
    icons.load(
        "GOZ_SYNC_ENABLED", os.path.join(icons_dir, "goz_sync_enabled.png"), "IMAGE"
    )
    icons.load(
        "GOZ_SYNC_DISABLED", os.path.join(icons_dir, "goz_sync_disabled.png"), "IMAGE"
    )

    icons.load("GOZ_SEND_FLAT", os.path.join(icons_dir, "goz_send_flat.png"), "IMAGE")
    icons.load("GOZ_SYNC_FLAT", os.path.join(icons_dir, "goz_sync_flat.png"), "IMAGE")

    ui.preview_collections["main"] = icons
    bpy.types.TOPBAR_HT_upper_bar.prepend(ui.draw_goz_buttons)

    # Re-arm the background listener. Automatic mode used to be silently lost on
    # every Blender restart, leaving the header button claiming sync was off
    # while ZBrush exports went unnoticed.
    try:
        if utils.prefs().import_method == "AUTOMATIC":
            gob_import.set_sync_active(True)
    except Exception as error:  # pragma: no cover - startup diagnostics only
        print(f"GoB: could not start the background listener: {error}")


def unregister():
    global _registered
    if not _registered:
        return

    # Stop the timer first so it cannot fire while the classes are being torn
    # down. set_sync_active(False) also clears the module flag; previously the
    # flag stayed True and the header icon kept showing sync as enabled.
    gob_import.set_sync_active(False)

    try:
        bpy.types.TOPBAR_HT_upper_bar.remove(ui.draw_goz_buttons)
    except (ValueError, RuntimeError):
        pass

    for preview_collection in list(ui.preview_collections.values()):
        try:
            bpy.utils.previews.remove(preview_collection)
        except (KeyError, RuntimeError):
            pass
    ui.preview_collections.clear()

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError) as error:
            # Blender reports "missing bl_rna attribute" when the class was
            # already torn down; a reload can leave the module in that state.
            print(f"GoB: {cls.__name__} was already unregistered ({error})")

    _registered = False
