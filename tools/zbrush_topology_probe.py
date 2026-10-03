"""Measure what ZBrush does with a mesh GoB re-sends after an edit.

Run by ZBrush as a startup plugin. The command line route

    ZBrush.exe -script tools/zbrush_topology_probe.py

does **not** work on this machine: ZBrush 2026.2.1 opens its Home Page and never
reaches the script (the Home Page is also a separate process, and closing it
quits ZBrush). The documented plugin hook does work:

    $env:ZBRUSH_PLUGIN_PATH = "<repo>\tools\zbrush_plugins"
    & "C:\\Program Files\\Maxon ZBrush 2026\\ZBrush.exe"

ZBrush executes every ``*.py`` in that directory on startup, inside its embedded
CPython 3.11 VM, with ``zbrush.commands`` available. The plugin must live alone
in that directory: everything there runs on every ZBrush start while the
variable is set.

What it measures, all inside one session (see
``tools/topology_verification_plan.md`` for the interpretation):

    A. Tool:Import of an 8v/6f cube into a plain subtool
    B. the same subtool after two Tool:Geometry:Divide presses (SDiv 3)
    C. a re-import with the same point count but moved vertices
    D. a re-import with a changed point count (26v/24f) -- the reported failure
    E. Tool:SubTool:Insert and Tool:SubTool:Duplicate on their own
    F. a changed-count import while subtool 0 of a two-subtool tool is selected
    G. the exact string arithmetic ZScripts/GoB_Import.txt:298-299 performs on
       the measured subtool titles

It writes ``.probe/zbrush_side_report.txt`` (and the same text to the user's
home directory), because the ZBrush console cannot be captured from a shell.
"""

import os
import sys
import traceback

REPO = os.environ.get("GOB_PROBE_DIR") or r"D:\projectos\gob_jeisson\GoB"
PROBE = os.path.join(REPO, ".probe")
REPORTS = [
    os.path.join(PROBE, "zbrush_side_report.txt"),
    os.path.join(os.path.expanduser("~"), "gob_topology_probe.txt"),
]

VARIANTS = os.path.join(PROBE, "variants")
WORK = os.path.join(PROBE, "work")
TARGET = os.path.join(WORK, "GoBTopoProbe.GoZ")
OBJECT_NAME = "GoBTopoProbe"


def say(message):
    print(message, flush=True)
    for path in REPORTS:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(str(message) + "\n")
        except Exception as error:
            print(f"report write failed for {path}: {error}", flush=True)


def safe(label, call, *args):
    try:
        return call(*args)
    except Exception as error:
        say(f"        {label}: EXC {type(error).__name__}: {error}")
        return None


def num(value):
    """query_mesh3d returns one-element lists; the number is what matters."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def state(zbc, label):
    points = num(safe("points", zbc.query_mesh3d, 0))
    faces = num(safe("faces", zbc.query_mesh3d, 1))
    subtools = safe("subtools", zbc.get_subtool_count)
    title = safe("title", zbc.get_title, "Tool:ItemInfo")
    sdiv = safe("sdiv", zbc.get, "Tool:Geometry:SDiv")
    say(f"     {label}: {points}v/{faces}f  subtools={subtools} SDiv={sdiv} "
        f"title={title!r}")
    return {"points": points, "faces": faces, "subtools": subtools,
            "title": title, "sdiv": sdiv}


def walk(zbc, label):
    total = safe("subtools", zbc.get_subtool_count) or 0
    for index in range(int(total)):
        safe(f"select {index}", zbc.select_subtool, index)
        entry = state(zbc, f"{label} subtool {index}")
        title = entry["title"] if isinstance(entry["title"], str) else ""
        trimmed = title[:len(title) - 2]
        say(f"        ZScript trims 2 chars -> {trimmed!r} (len {len(trimmed)}) "
            f"vs object name {OBJECT_NAME!r} (len {len(OBJECT_NAME)}) -> "
            f"{'MATCH' if trimmed == OBJECT_NAME else 'NO MATCH'}")


def stage(name):
    with open(os.path.join(VARIANTS, f"{name}.GoZ"), "rb") as src, \
            open(TARGET, "wb") as dst:
        dst.write(src.read())
    return TARGET


def do_import(zbc, name, label):
    stage(name)
    zbc.set_next_filename(TARGET)
    zbc.press("Tool:Import")
    return state(zbc, label)


def run():
    say("")
    say("=" * 70)
    say("ZBrush topology probe")
    say(f"argv={sys.argv!r}")

    import zbrush.commands as zbc

    say(f"zbrush.commands ok, version={zbc.zbrush_info(0)}")
    safe("show_actions", zbc.show_actions, 0)
    safe("config", zbc.config, 2026)
    os.makedirs(WORK, exist_ok=True)

    for item in ("Tool:Import", "Tool:Geometry:Divide", "Tool:Geometry:SDiv",
                 "Tool:SubTool:Insert", "Tool:SubTool:Duplicate",
                 "Tool:SubTool:Delete", "PopUp:PolyMesh3D", "PopUp:Cube3D",
                 "ZScript:Load", "ZScript:Reload"):
        say(f"     item {item!r} exists={safe('exists', zbc.exists, item)}")

    say("")
    say("--- A. import the cube (8v/6f)")
    a = do_import(zbc, "base", "A after ")

    say("")
    say("--- B. subdivide it inside ZBrush (SDiv 3)")
    for press in range(2):
        safe("divide", zbc.press, "Tool:Geometry:Divide")
    b = state(zbc, "B after ")
    walk(zbc, "B")

    say("")
    say("--- C. re-import the same point count, vertices moved")
    c = do_import(zbc, "moved", "C after ")

    say("")
    say("--- D. re-import a changed point count (26v/24f): the reported failure")
    d = do_import(zbc, "added", "D after ")

    say("")
    say("--- E. Tool:SubTool:Insert and Tool:SubTool:Duplicate on their own")
    before = safe("subtools", zbc.get_subtool_count)
    safe("insert", zbc.press, "Tool:SubTool:Insert")
    after_insert = safe("subtools", zbc.get_subtool_count)
    say(f"     Tool:SubTool:Insert   subtools {before} -> {after_insert}")
    safe("duplicate", zbc.press, "Tool:SubTool:Duplicate")
    after_duplicate = safe("subtools", zbc.get_subtool_count)
    say(f"     Tool:SubTool:Duplicate subtools {after_insert} -> {after_duplicate}")
    walk(zbc, "E")

    say("")
    say("--- F. changed point count with subtool 0 of a multi-subtool tool active")
    safe("select 0", zbc.select_subtool, 0)
    f = do_import(zbc, "removed", "F after ")
    walk(zbc, "F")

    say("")
    say("SUMMARY (query_mesh3d returns lists; the numbers above are the truth)")
    for label, entry in (("A plain import", a), ("B after dividing", b),
                         ("C same count moved", c), ("D changed count", d),
                         ("F changed count, subtool 0", f)):
        say(f"  {label:28} {entry['points']}v/{entry['faces']}f SDiv={entry['sdiv']}")
    say("ZBrush probe done")


try:
    run()
except Exception:
    say("ZBRUSH PROBE UNHANDLED")
    say(traceback.format_exc())
