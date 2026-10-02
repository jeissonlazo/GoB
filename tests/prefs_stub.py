"""Build a preferences stand-in from the real AddonPreferences RNA.

Blender only populates ``bpy.context.preferences.addons[<id>]`` when the
extension is enabled through the normal add-on system, which is unreliable in
``--background`` runs (and impossible when Blender cannot write its extension
cache). Reading the defaults straight from the class RNA keeps this stand-in
honest: if a default changes in preferences.py, the tests follow it.
"""


class _PrefsStub:
    """Stand-in exposing the add-on preferences' default values."""

    def __init__(self):
        # Names the add-on explicitly set, mirroring Blender's
        # ``is_property_set`` so path-resolution code can be exercised.
        self._explicitly_set = set()

    def is_property_set(self, name):
        return name in self._explicitly_set

    def set(self, name, value):
        """Assign a preference and mark it as explicitly set."""
        setattr(self, name, value)
        self._explicitly_set.add(name)


def build_prefs_stub(pref_cls):
    """Return an object exposing the add-on preferences' default values."""

    stub = _PrefsStub()
    props = pref_cls.bl_rna.properties
    for prop in props:
        identifier = prop.identifier
        if identifier in {"rna_type", "bl_idname", "bl_rna"}:
            continue
        try:
            default = prop.default
        except AttributeError:
            continue
        # Collection and array defaults are not needed by the code under test.
        if isinstance(default, (str, int, float, bool)):
            setattr(stub, identifier, default)
    return stub
