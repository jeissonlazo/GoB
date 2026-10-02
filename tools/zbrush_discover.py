"""Discover what ZBrush 2026 exposes for a GoB-imported tool.

Answers three questions at once:
  1. does the imported mesh report the same counts Blender wrote?
  2. does ZBrush report UVs, so the UV section really arrived?
  3. where is the GoZ export button, so the return trip can be driven?
"""
import os
import sys
import traceback

REPORT = os.path.join(os.path.expanduser("~"), "gob_zbrush_discover.txt")
GOZ_DIR = r"C:\Users\Public\Pixologic\GoZProjects\Default"
TARGET = "GoBExportProbe"
LINES = []


def say(m):
    LINES.append(str(m))
    print(m, flush=True)


def flush():
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(LINES) + "\n")


def main():
    import zbrush.commands as zbc

    say(f"zbrush version: {zbc.zbrush_info(0)}")
    zbc.show_actions(0)
    zbc.config(2026)
    say("configured")

    path = os.path.join(GOZ_DIR, f"{TARGET}.GoZ")
    say(f"importing {path}")
    say(f"tools before: {zbc.get_tool_count()}")
    zbc.set_next_filename(path)
    zbc.press("Tool:Import")
    say(f"tools after : {zbc.get_tool_count()}")
    say(f"subtools    : {zbc.get_subtool_count()}")
    say(f"title       : {zbc.get_title('Tool:ItemInfo')!r}")
    say(f"tool path   : {zbc.get_active_tool_path()}")

    say("")
    say("--- query_mesh3d ---")
    for prop, label in ((0, "point count"), (1, "face count"),
                        (2, "bounding box"), (3, "UV bounding box"),
                        (8, "mesh area")):
        try:
            say(f"{label:16} ({prop}): {zbc.query_mesh3d(prop)}")
        except Exception as error:
            say(f"{label:16} ({prop}): EXC {type(error).__name__}: {error}")

    for prop, label in ((4, "first UV tile"), (5, "next UV tile")):
        try:
            say(f"{label:16} ({prop}): {zbc.query_mesh3d(prop, 0)}")
        except Exception as error:
            say(f"{label:16} ({prop}): EXC {type(error).__name__}: {error}")

    say("")
    say("--- interface items of interest ---")
    candidates = [
        "Tool:Import", "Tool:Export", "Tool:GoZ", "Tool:GoZ:GoZ",
        "Tool:GoZBrush", "Tool:GoZ:Export", "Tool:Tool:GoZ",
        "ZScript:GoZ", "ZScript:GoZBrush", "Tool:Subtool:GoZ",
        "Tool:Polypaint:Polypaint", "Tool:Masks:View Mask",
        "Tool:UV Map:UV Map", "Tool:Geometry:SDiv",
        "Tool:Texture Map:TextureMap", "Tool:Displacement Map:DisplacementMap",
        "Tool:Normal Map:Normal Map",
    ]
    for item in candidates:
        try:
            exists = zbc.exists(item)
            enabled = zbc.is_enabled(item) if exists else None
            say(f"{item:45} exists={exists} enabled={enabled}")
        except Exception as error:
            say(f"{item:45} EXC {type(error).__name__}: {error}")

    say("")
    say("--- searching the Tool palette for GoZ ---")
    try:
        # get_info returns the bubble help; use it to probe likely GoZ paths.
        for item in ("Tool:GoZ", "Tool:Tool:GoZ", "Tool:GoZBrush"):
            try:
                say(f"info({item!r}): {zbc.get_info(item)!r}")
            except Exception as error:
                say(f"info({item!r}): EXC {type(error).__name__}")
    except Exception:
        say(traceback.format_exc())

    say("DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        say("UNHANDLED")
        say(traceback.format_exc())
    flush()
