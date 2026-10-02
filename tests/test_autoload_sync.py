"""Blender 5.2 tests for the GoZ autoload bookkeeping.

    blender --background --python tests/test_autoload_sync.py

The background sync used to lose work in three ways, all covered here:

* the object list's mtime was consumed *before* the import ran, and imported
  paths were only forgotten on a later idle timer tick, so two exports landing
  inside one poll interval were dropped and never retried;
* a write arriving while a slow import was running was consumed by the tick
  that then skipped it;
* an export whose timestamp did not advance (NTFS has coarse mtime resolution
  and ZBrush rewrites the file from scratch) woke nothing up at all.
"""

import os
import sys

import addon_utils
import bpy

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")
FAILURES = []
CHECKS = 0

A = "C:/GoZProjects/Default/A.GoZ"
B = "C:/GoZProjects/Default/B.GoZ"
C = "C:/GoZProjects/Default/C.GoZ"


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS | {label}")
    else:
        FAILURES.append(label)
        print(f"FAIL | {label} | {detail}")


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)

    addon_utils.enable(EXT, default_set=False, persistent=True)
    gob_import = sys.modules.get(f"{EXT}.gob_import")
    utils = sys.modules.get(f"{EXT}.utils")
    preferences = sys.modules.get(f"{EXT}.preferences")
    if gob_import is None or utils is None or preferences is None:
        print(f"FAIL | extension {EXT!r} is not available")
        return 1

    addon_root = os.path.dirname(gob_import.__file__)
    if os.path.join(addon_root, "tests") not in sys.path:
        sys.path.insert(0, os.path.join(addon_root, "tests"))
    from prefs_stub import build_prefs_stub

    utils._TEST_PREFS = build_prefs_stub(preferences.GoB_Preferences)

    def reset(mtime=100.0):
        """Start from a clean session: nothing imported, nothing seen."""
        gob_import._last_imported_paths = []
        gob_import._last_seen_paths = []
        gob_import.cached_last_edition_time = mtime

    def timer(paths_list, mtime):
        """Stand in for one run_import_periodically tick."""
        changed = gob_import._revision_changed(list(paths_list), mtime)
        if changed:
            gob_import.cached_last_edition_time = mtime
            gob_import._last_seen_paths = list(paths_list)
        return changed

    def pending(paths_list):
        return gob_import._pending_goz_paths(list(paths_list))

    def commit(imported):
        gob_import._commit_imported_paths(list(imported))

    # --- steady state -------------------------------------------------------
    reset(100.0)
    check("timer wakes on a new list", timer([A], 200.0))
    check("operator sees the new object", pending([A]) == [A])
    commit(pending([A]))
    check("timer idles on an unchanged list", not timer([A], 200.0))
    check("operator has nothing left to do", pending([A]) == [])

    # --- two exports inside one poll interval -------------------------------
    reset(100.0)
    timer([A], 200.0)
    commit(pending([A]))
    timer([A, B], 300.0)
    check("second export in the same window is not lost",
          pending([A, B]) == [B], f"pending={pending([A, B])}")
    commit(pending([A, B]))
    check("both exports are then marked done", pending([A, B]) == [])

    # --- a write arriving during a slow import ------------------------------
    reset(100.0)
    timer([A], 200.0)
    commit(pending([A]))
    check("write during the import is picked up", timer([A, C], 400.0))
    check("and only the new object is pending", pending([A, C]) == [C])

    # --- a partial import must leave the rest outstanding -------------------
    reset(100.0)
    timer([A, B], 200.0)
    check("both objects pending", sorted(pending([A, B])) == sorted([A, B]))
    commit([A])
    check("the object that failed is retried", pending([A, B]) == [B],
          f"pending={pending([A, B])}")
    check("the object that succeeded is skipped", A not in pending([A, B]))

    # --- rewrite with an unchanged timestamp --------------------------------
    reset(100.0)
    timer([A], 200.0)
    commit(pending([A]))
    check("same mtime but different contents is detected", timer([B], 200.0),
          "the timer stayed idle and the export would be ignored")
    check("and the new object is pending", pending([B]) == [B])

    # --- an object listed but never importable ------------------------------
    reset(100.0)
    timer([A], 200.0)
    commit([])
    check("an import that never happened is retried", pending([A]) == [A])

    # --- no spurious wake-ups ----------------------------------------------
    reset(100.0)
    timer([A], 200.0)
    commit(pending([A]))
    check("no spurious timer wake-ups", not timer([A], 200.0))
    check("no spurious re-import", pending([A]) == [])

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
