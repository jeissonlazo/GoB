"""Snapshot every file involved in the GoB <-> ZBrush handshake.

    python tools/goz_handshake_snapshot.py            # print
    python tools/goz_handshake_snapshot.py --save     # also write .probe/handshake_<time>.txt

Run it twice -- once before exporting from Blender and once right after -- and
compare. That answers, with evidence, where the export stops:

    GoZ_ObjectList.txt unchanged      -> Blender never wrote the handshake
    <name>.GoZ has the new counts     -> Blender wrote the mesh and the handshake
    a second ZBrush process appeared  -> GoB started a new ZBrush instead of
                                         handing the file to the open one
    <name>.GoZ name != subtool name   -> ZBrush will append a new subtool

Plain CPython, no Blender required.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import time

PUBLIC = os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Pixologic")
GOZ_BRUSH = os.path.join(PUBLIC, "GoZBrush")
PROJECT = os.path.join(PUBLIC, "GoZProjects", "Default")
GOZ_APPS_BLENDER = os.path.join(PUBLIC, "GoZApps", "Blender")
MAXON = os.path.join(
    os.environ.get("APPDATA", ""), "Maxon", "Maxon ZBrush 2026_F3C8B4C4"
)
BLENDER_EXT = os.path.join(
    os.environ.get("APPDATA", ""), "Blender Foundation", "Blender", "5.2",
    "extensions", "user_default", "gob",
)

MAGIC = b"GoZb 1.0 ZBrush GoZ Binary"
TAGS = {
    b"\x11\x27\x00\x00": "Vertices",
    b"\x21\x4e\x00\x00": "Faces",
    b"\xa9\x61\x00\x00": "UV",
    b"\xb9\x88\x00\x00": "Polypaint",
    b"\x32\x75\x00\x00": "Mask",
    b"\x41\x9c\x00\x00": "Polygroups",
    b"\x8a\x13\x00\x00": "Subdivision",
}

TEXT_FILES = [
    os.path.join(GOZ_BRUSH, "GoZ_ObjectList.txt"),
    os.path.join(GOZ_BRUSH, "GoZ_ProjectPath.txt"),
    os.path.join(GOZ_BRUSH, "GoZ_Application.txt"),
    os.path.join(GOZ_BRUSH, "GoZ_Config.txt"),
    os.path.join(GOZ_APPS_BLENDER, "GoZ_Config.txt"),
]

WATCHED_DIRS = [
    GOZ_BRUSH,
    PROJECT,
    GOZ_APPS_BLENDER,
    os.path.join(MAXON, "ZStartup", "ZPlugs64"),
    os.path.join(BLENDER_EXT, "ZScripts"),
]

LINES: list[str] = []


def say(message=""):
    print(message, flush=True)
    LINES.append(str(message))


def stamp(path):
    try:
        info = os.stat(path)
    except OSError:
        return "MISSING"
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info.st_mtime))
    return f"{info.st_size:>10} bytes  {when}"


def zbrush_processes():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ZBrush.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as error:  # pragma: no cover - diagnostics only
        return [f"<could not list processes: {error}>"]
    rows = [line for line in out.splitlines() if "ZBrush.exe" in line]
    return rows or ["<no ZBrush.exe running>"]


def parse_goz(path):
    """Return (name, {section: count}) or an error string."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        if not data.startswith(MAGIC):
            return "<not a GoZ file>"
        stored = struct.unpack_from("<I", data, 36)[0]
        name_length = stored - 24
        prefix = len(b"GoZMesh_")
        name = data[48 + prefix:48 + prefix + name_length].decode(
            "utf-8", "replace"
        ).strip("\x00")
        offset = 48 + prefix + name_length + 20
        sections = {}
        while offset + 12 <= len(data):
            tag = data[offset:offset + 4]
            if tag == b"\x00\x00\x00\x00":
                break
            length = struct.unpack_from("<I", data, offset + 4)[0]
            count = struct.unpack_from("<Q", data, offset + 8)[0]
            if length < 16 or offset + length > len(data):
                sections["<corrupt>"] = offset
                break
            sections[TAGS.get(tag, tag.hex())] = count
            offset += length
        return name, sections
    except Exception as error:
        return f"<unreadable: {type(error).__name__}: {error}>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", action="store_true",
                        help="write the report under .probe/ as well")
    args = parser.parse_args()

    say("GoB handshake snapshot")
    say(f"taken: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    say("=" * 72)

    say("")
    say("ZBrush processes (a second one means GoB launched a new instance)")
    for row in zbrush_processes():
        say(f"  {row}")

    say("")
    say("Handshake values")
    for path in TEXT_FILES:
        say(f"  {os.path.basename(path):26} {stamp(path)}")
        if os.path.isfile(path) and path.endswith(".txt"):
            try:
                with open(path, "rt", encoding="utf-8", errors="replace") as handle:
                    for line in handle.read().splitlines():
                        say(f"      | {line}")
            except OSError as error:
                say(f"      | <unreadable: {error}>")

    say("")
    say("The mesh files Blender writes")
    if os.path.isdir(PROJECT):
        for name in sorted(os.listdir(PROJECT)):
            if name.lower().endswith((".goz", ".ztn")):
                path = os.path.join(PROJECT, name)
                say(f"  {name:32} {stamp(path)}")
                if name.lower().endswith(".goz"):
                    parsed = parse_goz(path)
                    say(f"      -> {parsed}")
                else:
                    try:
                        with open(path, "rt", encoding="utf-8", errors="replace") as handle:
                            say(f"      | {handle.read().strip()}")
                    except OSError:
                        pass

    say("")
    say("Directory contents")
    for directory in WATCHED_DIRS:
        say(f"  {directory}")
        if not os.path.isdir(directory):
            say("      <missing>")
            continue
        for name in sorted(os.listdir(directory)):
            full = os.path.join(directory, name)
            if os.path.isfile(full):
                say(f"      {name:34} {stamp(full)}")

    say("")
    say("ZScript Blender passes to ZBrush")
    for label, directory in (("repo", r"D:\projectos\gob_jeisson\GoB"),
                             ("installed", BLENDER_EXT)):
        for name in ("GoB_Import.txt", "GoB_Import.zsc"):
            path = os.path.join(directory, "ZScripts", name)
            say(f"  {label:10} {name:16} {stamp(path)}")

    say("")
    say("GoZ app registration for Blender (ZBrush -> Blender direction)")
    for name in ("GoZ_Info.txt", "GoZ_Config.txt", "GoZBrushFromApp.exe"):
        candidates = [os.path.join(GOZ_APPS_BLENDER, name),
                      os.path.join(GOZ_BRUSH, name)]
        for path in candidates:
            if os.path.exists(path) or name != "GoZBrushFromApp.exe":
                say(f"  {name:22} {path}")
                say(f"      {stamp(path)}")

    say("")
    say("Official hand-off helper present?")
    helper = os.path.join(GOZ_BRUSH, "GoZBrushFromApp.exe")
    say(f"  {helper}: {os.path.isfile(helper)}")
    say("  GoB never calls it (gob_export.py:808 launches ZBrush instead),")
    say("  which is why an already-open ZBrush never receives the export.")

    if args.save:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_dir = os.path.join(repo, ".probe")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir,
                           f"handshake_{time.strftime('%H%M%S')}.txt")
        with open(out, "w", encoding="utf-8") as handle:
            handle.write("\n".join(LINES) + "\n")
        print(f"\nsaved: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
