"""Tests for linking imported objects into the view layer.

    blender --background --python tests/test_import_view_layer.py

An imported object can end up outside the view layer: it may be newly created
without a collection, or it may live in a collection the user has excluded. Both
cases have to remain selectable afterwards, and the second must keep its original
collection membership while being re-exposed.
"""

import os
import sys
import unittest
from pathlib import Path

import addon_utils
import bpy

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")

# The add-on is loaded as an extension, like every other test here. Importing it
# as a package named 'gob' cannot work: the checkout directory is 'GoB', so the
# import is mis-cased and raises ModuleNotFoundError on case-sensitive lookups.
addon_utils.enable(EXT, default_set=False, persistent=True)

try:
    gob_import = sys.modules[f"{EXT}.gob_import"]
except KeyError:  # pragma: no cover - reported as a failure below
    gob_import = None


def find_layer_collection(layer_collection, name):
    if layer_collection.name == name:
        return layer_collection
    for child in layer_collection.children:
        match = find_layer_collection(child, name)
        if match is not None:
            return match
    return None


@unittest.skipIf(gob_import is None, f"extension {EXT!r} is not available")
class EnsureObjectInViewLayerTests(unittest.TestCase):
    def tearDown(self):
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in list(bpy.data.collections):
            bpy.data.collections.remove(collection)
        for mesh in list(bpy.data.meshes):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)

    def test_relinks_orphaned_object(self):
        obj = bpy.data.objects.new("Orphan", bpy.data.meshes.new("OrphanMesh"))

        self.assertNotIn(obj.name, bpy.context.view_layer.objects)
        gob_import.GoB_OT_import._ensure_object_in_view_layer(obj)

        self.assertIn(obj.name, bpy.context.view_layer.objects)
        obj.select_set(True)

    def test_preserves_excluded_collection_membership(self):
        excluded_collection = bpy.data.collections.new("Excluded")
        bpy.context.scene.collection.children.link(excluded_collection)
        obj = bpy.data.objects.new("ExcludedObject", bpy.data.meshes.new("Mesh"))
        excluded_collection.objects.link(obj)
        bpy.context.view_layer.update()

        layer_collection = find_layer_collection(
            bpy.context.view_layer.layer_collection, excluded_collection.name
        )
        self.assertIsNotNone(layer_collection)
        layer_collection.exclude = True
        bpy.context.view_layer.update()
        self.assertNotIn(obj.name, bpy.context.view_layer.objects)

        gob_import.GoB_OT_import._ensure_object_in_view_layer(obj)

        self.assertIn(obj.name, bpy.context.view_layer.objects)
        self.assertIn(excluded_collection, obj.users_collection)
        obj.select_set(True)


if __name__ == "__main__":
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        EnsureObjectInViewLayerTests
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failures = len(result.failures) + len(result.errors)
    print("=" * 72)
    print(f"RESULT checks={result.testsRun} failures={failures}")
    for case, _trace in result.failures + result.errors:
        print(f"  FAILED: {case}")
    print("VERDICT", "ALL_PASS" if failures == 0 else "FAILURES_PRESENT")
    sys.exit(1 if failures else 0)
