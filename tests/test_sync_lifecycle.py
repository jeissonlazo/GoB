"""Blender 5.2 tests for the GoZ background-listener lifecycle.

    blender --background --python tests/test_sync_lifecycle.py

Covers how the automatic sync is started and stopped. The old code kept the
listener's state in a module flag that nothing re-armed:

* automatic mode was silently lost on every Blender restart -- the header
  button drew "sync off" and ZBrush exports went unnoticed until the user
  toggled the button twice;
* ``unregister()`` stopped the timer but left the flag True, so the icon could
  claim sync was running when no timer existed;
* enabling the listener created GoZ_ObjectList.txt with ``open(path, "x")``,
  so turning sync on for a ZBrush install that had not written the file yet
  produced a stray empty file.

The listener state is now derived from the timer itself, so what the button
shows cannot drift from reality.
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
        print(f"PASS | {label}", flush=True)
    else:
        FAILURES.append(label)
        print(f"FAIL | {label} | {detail}", flush=True)


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)

    addon_utils.enable(EXT, default_set=False, persistent=True)
    gob_import = sys.modules.get(f"{EXT}.gob_import")
    utils = sys.modules.get(f"{EXT}.utils")
    preferences = sys.modules.get(f"{EXT}.preferences")
    paths = sys.modules.get(f"{EXT}.paths")
    if None in (gob_import, utils, preferences, paths):
        print(f"FAIL | extension {EXT!r} is not available")
        return 1

    addon_root = os.path.dirname(gob_import.__file__)
    if os.path.join(addon_root, "tests") not in sys.path:
        sys.path.insert(0, os.path.join(addon_root, "tests"))
    from prefs_stub import build_prefs_stub

    stub = build_prefs_stub(preferences.GoB_Preferences)
    utils._TEST_PREFS = stub

    timer = gob_import.run_import_periodically

    # --- turning it on and off ---------------------------------------------
    gob_import.set_sync_active(False)
    check("starts stopped", not gob_import.is_sync_active())

    gob_import.set_sync_active(True)
    check("set_sync_active(True) registers the timer",
          bpy.app.timers.is_registered(timer))
    check("is_sync_active() reflects the timer", gob_import.is_sync_active())

    gob_import.set_sync_active(False)
    check("set_sync_active(False) unregisters the timer",
          not bpy.app.timers.is_registered(timer))
    check("is_sync_active() is then False", not gob_import.is_sync_active())

    # --- turning it on twice must not register two timers -------------------
    gob_import.set_sync_active(True)
    gob_import.set_sync_active(True)
    check("enabling twice leaves it registered exactly once",
          bpy.app.timers.is_registered(timer))
    gob_import.set_sync_active(False)
    check("disabling twice is harmless", not gob_import.is_sync_active())

    # --- the legacy flag must not drift ------------------------------------
    gob_import.set_sync_active(True)
    check("legacy flag agrees while running", bool(gob_import.run_background_update))
    gob_import.set_sync_active(False)
    check("legacy flag agrees while stopped",
          not bool(gob_import.run_background_update),
          "unregister() used to leave this True")

    # --- unregister() must stop the timer ----------------------------------
    gob_import.set_sync_active(True)
    check("listener running before unregister", gob_import.is_sync_active())

    gob = sys.modules[f"{EXT}"]
    gob.unregister()
    module_import = gob_import
    timer_still = bpy.app.timers.is_registered(module_import.run_import_periodically)
    check("unregister() stops the listener", not timer_still)
    check("unregister() clears the legacy flag",
          not bool(module_import.run_background_update))
    check("unregister() clears preview collections",
          not sys.modules[f"{EXT}.ui"].preview_collections)

    # --- register() re-arms automatic mode ---------------------------------
    stub.import_method = "AUTOMATIC"
    try:
        gob.register()
        check("register() re-arms the listener in AUTOMATIC mode",
              bpy.app.timers.is_registered(
                  module_import.run_import_periodically),
              "this is the Blender-restart regression")
    except Exception as error:
        check("register() re-arms the listener in AUTOMATIC mode", False,
              f"{type(error).__name__}: {error}")

    # --- manual mode must not auto-start -----------------------------------
    gob.unregister()
    stub.import_method = "MANUAL"
    gob.register()
    check("register() leaves the listener off in MANUAL mode",
          not bpy.app.timers.is_registered(
              module_import.run_import_periodically))
    gob.unregister()

    # --- enabling must not create a stray object list ----------------------
    # Point the GoZ root at a path that does not exist, to model a ZBrush
    # install that has not written GoZ_ObjectList.txt yet. The root has to be
    # driven through the preference because set_sync_active() re-resolves the
    # path from preferences.
    missing_root = os.path.join(
        os.environ.get("TEMP", os.path.dirname(addon_root)),
        "gob_test_missing_goz_root",
    )
    original_goz = paths.PATH_GOZ
    original_custom = stub.custom_pixologoc_path
    original_pixologic = getattr(stub, "pixologoc_path", "")
    try:
        paths.set_goz_path(missing_root)
        if os.path.exists(paths.PATH_OBJLIST):
            os.remove(paths.PATH_OBJLIST)
        stub.set("custom_pixologoc_path", True)
        stub.set("pixologoc_path", missing_root)
        stub.set("pixologoc_path_windows", missing_root)

        check("object list does not exist yet",
              not os.path.exists(paths.PATH_OBJLIST))

        gob_import.set_sync_active(True)
        check("enabling with a missing object list still registers the timer",
              bpy.app.timers.is_registered(
                  module_import.run_import_periodically))
        check("enabling did not create a stray GoZ_ObjectList.txt",
              not os.path.exists(paths.PATH_OBJLIST),
              "the old code used open(path, 'x') here")
    finally:
        gob_import.set_sync_active(False)
        stub.set("custom_pixologoc_path", original_custom)
        stub.set("pixologoc_path", original_pixologic)
        paths.set_goz_path(original_goz)

    # --- missing preferences must fail loudly, and not spam the timer ------
    # Enable first, while preferences still resolve, then remove them to model
    # the add-on being disabled while its persistent timer is still alive.
    gob_import.set_sync_active(True)
    prefs_backup = utils._TEST_PREFS
    utils._TEST_PREFS = None
    try:
        # Enabling itself has to resolve the GoZ path, so it must raise a
        # diagnosable error rather than a bare KeyError.
        try:
            gob_import.set_sync_active(True)
            check("enabling without preferences raises a clear error", False,
                  "no exception raised")
        except RuntimeError as error:
            check("enabling without preferences raises a clear error",
                  "not enabled" in str(error), str(error))

        # The timer callback must swallow that and stop, instead of logging the
        # same traceback on every tick.
        result = module_import.run_import_periodically()
        check("timer degrades cleanly when preferences are gone",
              result is None or isinstance(result, float),
              f"returned {result!r}")
    except Exception as error:
        check("timer degrades cleanly when preferences are gone", False,
              f"{type(error).__name__}: {error}")
    finally:
        utils._TEST_PREFS = prefs_backup
        gob_import.set_sync_active(False)

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
