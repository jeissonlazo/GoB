"""Drive the ZBrush -> Blender return trip and record what ZBrush sends back.

Run by ZBrush:

    ZBrush.exe -script <this file>

It imports the .GoZ Blender wrote, presses Tool:GoZ (ZBrush's "export current
subtool to the GoZ enabled application"), and records which file changed and
what the GoZ_ObjectList now points at. Blender then reads that file with
tests/test_zbrush_roundtrip.py, so the whole loop is covered by evidence rather
than by watching the GUI.
"""

import os
import sys
import time
import traceback

REPORT = os.path.join(os.path.expanduser("~"), "gob_zbrush_export.txt")
GOZ_DIR = r"C:\Users\Public\Pixologic\GoZProjects\Default"
OBJECT_LIST = r"C:\Users\Public\Pixologic\GoZBrush\GoZ_ObjectList.txt"
TARGET = "GoBExportProbe"
LINES = []


def say(m):
    LINES.append(str(m))
    print(m, flush=True)


def flush():
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(LINES) + "\n")


def snapshot():
    """Map every file in the GoZ folders to its size and mtime."""
    state = {}
    for folder in (GOZ_DIR, os.path.dirname(OBJECT_LIST)):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                stat = os.stat(full)
                state[full] = (stat.st_size, stat.st_mtime)
    return state


def read_object_list():
    try:
        with open(OBJECT_LIST, "rt") as fh:
            return [line.strip() for line in fh if line.strip()]
    except Exception as error:
        return [f"<unreadable: {type(error).__name__}: {error}>"]


def main():
    import zbrush.commands as zbc

    say(f"zbrush version: {zbc.zbrush_info(0)}")
    say(f"object list before: {read_object_list()}")

    zbc.show_actions(0)
    zbc.config(2026)

    path = os.path.join(GOZ_DIR, f"{TARGET}.GoZ")
    say(f"importing {path}")
    before_tools = zbc.get_tool_count()
    zbc.set_next_filename(path)
    zbc.press("Tool:Import")
    after_tools = zbc.get_tool_count()
    say(f"tools {before_tools} -> {after_tools}")
    if after_tools <= before_tools:
        say("FAIL: the import did not create a tool, cannot continue")
        return

    say(f"subtool title: {zbc.get_title('Tool:ItemInfo')!r}")
    say(f"tool path    : {zbc.get_active_tool_path()}")
    say(f"points/faces : {zbc.query_mesh3d(0)} / {zbc.query_mesh3d(1)}")
    say(f"uv bbox      : {zbc.query_mesh3d(3)}")

    # Make sure ZBrush knows where the exchange folder is.
    try:
        zbc.set_next_filename(os.path.join(GOZ_DIR, f"{TARGET}.GoZ"))
        say("set_next_filename for the exchange folder")
    except Exception as error:
        say(f"NOTE set_next_filename: {type(error).__name__}: {error}")

    state_before = snapshot()
    say("")
    say(f"files before the export: {len(state_before)}")

    say("")
    say("--- pressing Tool:GoZ (export to the GoZ application) ---")
    pressed = False
    for item in ("Tool:GoZ", "Tool:Tool:GoZ", "Tool:GoZ:GoZ", "Tool:GoZ:Export"):
        try:
            if zbc.exists(item):
                zbc.press(item)
                say(f"pressed {item}")
                pressed = True
                break
        except Exception as error:
            say(f"press {item} EXC {type(error).__name__}: {error}")
    if not pressed:
        say("FAIL: no GoZ export item could be pressed")
        return

    time.sleep(6)   # let ZBrush finish writing

    state_after = snapshot()
    say("")
    say("--- what changed ---")
    new_files = [f for f in state_after if f not in state_before]
    changed = [
        f for f in state_after
        if f in state_before and state_after[f] != state_before[f]
    ]
    for f in new_files:
        say(f"NEW     {f} ({state_after[f][0]} bytes)")
    for f in changed:
        say(f"CHANGED {f} ({state_before[f][0]} -> {state_after[f][0]} bytes)")

    if not new_files and not changed:
        say("FAIL: the export produced no new or changed file")

    say("")
    say(f"object list after: {read_object_list()}")

    # Record the file Blender should read back.
    goz_after = [
        f for f in (new_files + changed)
        if f.lower().endswith(".goz")
    ]
    if goz_after:
        say(f"RESULT_GOZ={goz_after[0]}")
    say("DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        say("UNHANDLED")
        say(traceback.format_exc())
    flush()
