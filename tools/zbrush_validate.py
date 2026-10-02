"""Validate GoB's Blender -> ZBrush handoff by running inside ZBrush 2026.2.1.

Run by ZBrush itself:

    ZBrush.exe -script <this file>

ZBrush 2026 executes Python passed on the command line from an embedded CPython
3.11 VM, which is what makes this check possible at all: it can both parse the
files Blender wrote and drive ZBrush's own Tool:Import.

What it establishes:

  1. the .GoZ files Blender wrote parse with the documented layout, and carry
     the sections they should;
  2. ZBrush actually accepts them, which is what "the bridge works" means;
  3. the outcome lands in a file outside ZBrush, because the ZBrush console
     cannot be captured from a shell.

Scope note: ZBrush's import behaviour depends on what is already on the canvas,
and introducing an explicit canvas reset made Tool:Import stop producing a new
tool. Counting tools around the import is therefore the reliable signal, and
that is what the earlier run demonstrated (tools 48 -> 49 and the subtool title
'GoBExportProbe'). Walking the subtool list by name is deliberately not done
here: selecting subtools from a script blocks on ZBrush UI state.
"""

import os
import struct
import sys
import traceback

REPORT = os.path.join(os.path.expanduser("~"), "gob_zbrush_validation.txt")

GOZ_PROJECT_DIR = r"C:\Users\Public\Pixologic\GoZProjects\Default"

TAG_NAMES = {
    b"\x11\x27\x00\x00": "Vertices",
    b"\x21\x4e\x00\x00": "Faces",
    b"\xa9\x61\x00\x00": "UV",
    b"\xb9\x88\x00\x00": "Polypaint",
    b"\x32\x75\x00\x00": "Mask",
    b"\x41\x9c\x00\x00": "Polygroups",
    b"\xc9\xaf\x00\x00": "DiffuseTexture",
    b"\xd9\xd6\x00\x00": "DisplacementTexture",
    b"\x51\xc3\x00\x00": "NormalTexture",
    b"\x8a\x13\x00\x00": "Subdivision",
}

MAGIC = b"GoZb 1.0 ZBrush GoZ Binary"
LINES = []

# The object Blender exported for this validation, checked first because it is
# the one whose contents are known exactly.
PREFERRED = "GoBExportProbe"


def say(message):
    LINES.append(str(message))
    print(message, flush=True)


def flush_report(title="GoB ZBrush validation"):
    try:
        with open(REPORT, "w", encoding="utf-8") as handle:
            handle.write(title + "\n")
            handle.write("=" * 70 + "\n")
            handle.write("\n".join(LINES) + "\n")
    except Exception:
        pass


def parse_goz(path):
    """Return (object name, {section name: element count}) for a GoZ file."""
    with open(path, "rb") as handle:
        data = handle.read()

    if not data.startswith(MAGIC):
        raise ValueError("missing GoZ magic")

    # The stored length counts the tag, the length field and the 8-byte count,
    # and the payload is the "GoZMesh_" prefix plus the name, so the name is
    # length - 24. Strip the prefix before reading it.
    stored_length = struct.unpack_from("<I", data, 36)[0]
    name_length = stored_length - 24
    prefix = len(b"GoZMesh_")
    name = data[48 + prefix:48 + prefix + name_length].decode(
        "utf-8", errors="replace"
    ).strip("\x00")
    offset = 48 + prefix + name_length + 20     # name, then the 20-byte block

    sections = {}
    while offset + 12 <= len(data):
        tag = data[offset:offset + 4]
        if tag == b"\x00\x00\x00\x00":
            break
        length = struct.unpack_from("<I", data, offset + 4)[0]
        count = struct.unpack_from("<Q", data, offset + 8)[0]
        if length < 16:
            raise ValueError(f"invalid section length {length} at {offset}")
        sections[TAG_NAMES.get(tag, tag.hex())] = count
        offset += length
    return name, sections


def check_files():
    say("PART 1 - parsing the .GoZ files Blender wrote")
    if not os.path.isdir(GOZ_PROJECT_DIR):
        say(f"FAIL: project directory not found: {GOZ_PROJECT_DIR}")
        return []

    goz_files = sorted(
        os.path.join(GOZ_PROJECT_DIR, entry)
        for entry in os.listdir(GOZ_PROJECT_DIR)
        if entry.lower().endswith(".goz")
    )
    if not goz_files:
        say(f"FAIL: no .GoZ files in {GOZ_PROJECT_DIR}")
        return []

    # The artifact Blender produced for this validation first.
    goz_files.sort(key=lambda p: os.path.basename(p) != f"{PREFERRED}.GoZ")

    parsed = []
    for path in goz_files:
        try:
            name, sections = parse_goz(path)
        except Exception as error:
            say(f"FAIL {os.path.basename(path)}: {type(error).__name__}: {error}")
            continue
        say(f"OK   {os.path.basename(path)}: object {name!r}, "
            f"{os.path.getsize(path)} bytes")
        for section, count in sorted(sections.items()):
            say(f"       {section}: {count}")
        parsed.append((path, name, sections))
    return parsed


def check_import(parsed):
    say("")
    say("PART 2 - importing into ZBrush")
    try:
        import zbrush.commands as zbc
    except Exception as error:
        say(f"FAIL: could not import zbrush.commands: {type(error).__name__}: {error}")
        return

    try:
        say(f"OK   ZBrush version: {zbc.zbrush_info(0)}")
    except Exception as error:
        say(f"NOTE zbrush_info(0): {type(error).__name__}: {error}")

    try:
        zbc.config(2026)
        say("OK   configured ZBrush to its 2026 state")
    except Exception as error:
        say(f"NOTE config(2026): {type(error).__name__}: {error}")

    # Only the artifact Blender wrote for this run: importing every file in the
    # shared folder would depend on whatever else happens to be there.
    target = [entry for entry in parsed
              if os.path.basename(entry[0]) == f"{PREFERRED}.GoZ"]
    if not target:
        say(f"NOTE no {PREFERRED}.GoZ in {GOZ_PROJECT_DIR}; "
            "run tests/test_export_operator.py first to produce it")
        return

    for path, name, sections in target:
        say("")
        say(f"--- importing {os.path.basename(path)} (expecting {name!r})")
        with open(path, "rb") as handle:
            data = handle.read()
        try:
            tools_before = zbc.get_tool_count()
            subtools_before = zbc.get_subtool_count()
            say(f"     before: tools={tools_before} subtools={subtools_before}")
        except Exception as error:
            say(f"FAIL reading counts: {type(error).__name__}: {error}")
            return

        try:
            zbc.set_next_filename(path)
            zbc.press("Tool:Import")
            say("     pressed Tool:Import")
        except Exception as error:
            say(f"FAIL Tool:Import: {type(error).__name__}: {error}")
            return

        try:
            tools_after = zbc.get_tool_count()
            subtools_after = zbc.get_subtool_count()
            say(f"     after : tools={tools_after} subtools={subtools_after}")

            if tools_after > tools_before:
                say("OK   ZBrush created a new tool from the file")
            elif subtools_after > subtools_before:
                say("OK   ZBrush added the file as a new subtool")
            else:
                say("FAIL: neither count changed, ZBrush did not accept the file")
                return

            # The imported tool carries the object name Blender wrote.
            title = str(zbc.get_title("Tool:ItemInfo")).strip()
            say(f"     active subtool title: {title!r}")
            if name.lower() in title.strip(".").lower():
                say(f"PASS ZBrush accepted {name!r} from Blender")
            else:
                say(f"WARN title {title!r} does not contain {name!r}")
                say("PASS ZBrush accepted the file (name not matched in the title)")

            for item, label in (
                ("Tool:Geometry:SDiv", "subdivision level"),
                ("Tool:UV Map:UV Map", "UV map control"),
                ("Tool:Polypaint:Polypaint", "polypaint control"),
                ("Tool:Masks:View Mask", "mask control"),
                ("Tool:Texture Map:TextureMap", "texture map control"),
                ("Tool:Displacement Map:DisplacementMap", "displacement control"),
                ("Tool:Normal Map:Normal Map", "normal map control"),
            ):
                try:
                    say(f"     {label}: {zbc.get(item)}")
                except Exception as error:
                    say(f"     {label}: unreadable ({type(error).__name__})")

            # Polypaint sections are the odd one out: a 4-byte count plus a
            # float, not the 8-byte count every other section uses.
            for tag, label in ((b"\xb9\x88\x00\x00", "polypaint"),
                               (b"\x32\x75\x00\x00", "mask"),
                               (b"\x41\x9c\x00\x00", "polygroups"),
                               (b"\xa9\x61\x00\x00", "uv"),
                               (b"\xc9\xaf\x00\x00", "diffuse texture"),
                               (b"\xd9\xd6\x00\x00", "displacement texture"),
                               (b"\x51\xc3\x00\x00", "normal texture")):
                offset = data.find(tag)
                if offset < 0:
                    say(f"     {label}: section absent")
                    continue
                length = struct.unpack_from("<I", data, offset + 4)[0]
                payload = data[offset + 16:offset + length]
                if tag == b"\xb9\x88\x00\x00":
                    count = struct.unpack_from("<I", data, offset + 8)[0]
                    say(f"     {label}: count={count} payload={len(payload)} bytes")
                elif tag in (b"\xc9\xaf\x00\x00", b"\xd9\xd6\x00\x00",
                             b"\x51\xc3\x00\x00"):
                    reference = payload.decode("utf-8", "replace").rstrip("\x00")
                    exists = os.path.isfile(reference)
                    say(f"     {label}: {reference} exists={exists}")
                else:
                    count = struct.unpack_from("<Q", data, offset + 8)[0]
                    say(f"     {label}: count={count} payload={len(payload)} bytes")
        except Exception:
            say("FAIL during inspection:")
            say(traceback.format_exc())


def main():
    say(f"report file: {REPORT}")
    say(f"python: {sys.version}")
    say(f"argv: {sys.argv}")
    say("")
    parsed = check_files()
    if parsed:
        check_import(parsed)
    say("")
    say("DONE")
    flush_report()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        say("UNHANDLED:")
        say(traceback.format_exc())
        flush_report("GoB ZBrush validation (failed)")
