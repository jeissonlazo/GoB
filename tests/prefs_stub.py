"""Build a preferences stand-in from the real AddonPreferences RNA.

Blender only populates ``bpy.context.preferences.addons[<id>]`` when the
extension is enabled through the normal add-on system, which is unreliable in
``--background`` runs (and impossible when Blender cannot write its extension
cache). Reading the defaults straight from the class RNA keeps this stand-in
honest: if a default changes in preferences.py, the tests follow it.
"""


def build_prefs_stub(pref_cls):
    """Return an object exposing the add-on preferences' default values."""

    class _PrefsStub:
        pass

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
        # Array/collection defaults are not needed by the node helpers.
        if isinstance(default, (str, int, float, bool)):
            setattr(stub, identifier, default)
    return stub
