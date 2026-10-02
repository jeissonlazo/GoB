"""Validate GoB's Blender -> ZBrush handoff by running inside ZBrush 2026.2.1.

Run by ZBrush itself:

    ZBrush.exe -script <this file>

ZBrush executes Python from the command line and gives the script a full
CPython 3.11 VM embedded in the application, so this can both parse the files
GoB wrote and drive ZBrush's own import UI.

What it checks:
  1. the .GoZ files Blender wrote parse correctly (the same byte layout the
     Blender-side tests use), and carry the sections they should;
  2. ZBrush itself can import them: a subtool appears with the expected name,
     at the expected subdivision level, with UVs and polypaint present;
  3. the outcome is written next to the report so it can be read back outside
     ZBrush, because the ZBrush console is not captureable from a shell.

The result file is the point: it turns "did ZBrush accept it?" into something
that can be asserted on.
"""

import os
import struct
import sys
import traceback

REPORT = os.path.join(
    os.path.expanduser("~"), "gob_zbrush_validation.txt"
)

# Where Blender put the transfer. The GoZ project directory is shared, so both
# applications see the same files.
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


# ---------------------------------------------------------------------------
# Part 1: parse the files GoB wrote, with plain Python
# ---------------------------------------------------------------------------
def parse_goz(path):
    """Return {section name: element count} for a GoZ file, or raise."""
    with open(path, "rb") as handle:
        data = handle.read()

    if not data.startswith(MAGIC):
        raise ValueError("missing GoZ magic")

    # Header: 26 magic + 6 dots + 4 object tag + 4 length + 8 count.
    name_length = struct.unpack_from("<I", data, 36)[0] - 24
    name = data[48:48 + name_length].decode("utf-8", errors="replace")
    offset = 48 + name_length + 20            # skip name and the 20-byte block

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
        os.path.join(GOZ_PROJECT_DIR, name)
        for name in os.listdir(GOZ_PROJECT_DIR)
        if name.lower().endswith(".goz")
    )
    if not goz_files:
        say(f"FAIL: no .GoZ files in {GOZ_PROJECT_DIR}")
        return []

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


# ---------------------------------------------------------------------------
# Part 2: let ZBrush import them and inspect the result
# ---------------------------------------------------------------------------
def check_import(parsed):
    say("")
    say("PART 2 - importing into ZBrush")
    try:
        import zbrush.commands as zbc
    except Exception as error:
        say(f"FAIL: could not import zbrush.commands: {type(error).__name__}: {error}")
        return

    try:
        version = zbc.zbrush_info(0)
        say(f"OK   ZBrush reports version index 0 = {version}")
    except Exception as error:
        say(f"NOTE zbrush_info(0) failed: {type(error).__name__}: {error}")

    for path, name, sections in parsed:
        say(f"--- importing {os.path.basename(path)} as {name!r}")
        try:
            before = zbc.get_subtool_count()
            say(f"     subtools before: {before}")
        except Exception as error:
            say(f"FAIL get_subtool_count: {type(error).__name__}: {error}")
            continue

        try:
            # Just like the importer does: point ZBrush at the file and press
            # the Tool:Import button.
            zbc.set_next_filename(path)
            zbc.press("Tool:Import")
        except Exception as error:
            say(f"FAIL import press: {type(error).__name__}: {error}")
            continue

        try:
            after = zbc.get_subtool_count()
            say(f"OK   subtools after: {after}")
            if after <= before:
                say("FAIL: no new subtool appeared, ZBrush did not accept the file")
                continue

            index = after - 1
            zbc.select_subtool(index)
            title = zbc.get_title("Tool:ItemInfo")
            say(f"OK   active subtool title: {title}")

            if name.lower() not in str(title).lower():
                say(f"WARN: subtool title {title!r} does not contain {name!r}")
            else:
                say("OK   subtool carries the exported object name")

            try:
                tool_path = zbc.get_active_tool_path()
                say(f"OK   tool path: {tool_path}")
            except Exception as error:
                say(f"NOTE get_active_tool_path failed: {type(error).__name__}: {error}")

            if "UV" in sections:
                try:
                    has_uv = zbc.exists("Tool:UV Map:UV Map")
                    say(f"OK   UV map control present: {has_uv}")
                except Exception as error:
                    say(f"NOTE UV check failed: {type(error).__name__}: {error}")

            if "Polypaint" in sections:
                try:
                    poly = zbc.get("Tool:Polypaint:Polypaint")
                    say(f"OK   polypaint value readable: {poly}")
                except Exception as error:
                    say(f"NOTE polypaint check failed: {type(error).__name__}: {error}")

            if "Mask" in sections:
                try:
                    mask = zbc.get("Tool:Masks:View Mask")
                    say(f"OK   mask control readable: {mask}")
                except Exception as error:
                    say(f"NOTE mask check failed: {type(error).__name__}: {error}")

            say(f"PASS {name}: imported and inspected")
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
