"""Blender 5.2 tests for the GoZ binary parser.

    blender --background --python tests/test_parser_robustness.py

GoZ is a proprietary binary format, so the parser's failure modes are the ones
that matter: a truncated or malformed section must be reported and skipped, and
must never be silently consumed as if it were the next section. These tests
build GoZ files byte by byte, so they need no fixtures checked into the repo and
they can deliberately produce the corruption a real file would only suffer on a
failed transfer.

The name record is special. It packs three things into one section, so it does
not follow the mesh sections' rule:

    offset 36   <I  length = len(name) + 24
    offset 40   <Q  count
    offset 48   b"GoZMesh_" + name        8 + len(name) bytes
    offset 71   <4B 0x89 0x13 0x00 0x00   trailer
                <I 20, <Q 1, <I 0          a fixed 20-byte block
    offset 91   first mesh section tag

The parser has to consume the name as ``length - 24`` bytes and then the
20-byte block. Reading the whole ``length - 16`` as the name swallowed the
trailer into the object name ("PM3D_Cylinder3D" became "PM3D_Cy"), and leaving
the block unread made it be treated as the first mesh section. That only
realigned because the block's own length field happens to advance the stream by
exactly the right amount.

A mesh section's stored length counts its own 4-byte tag, the 4-byte length and
the 8-byte count, so its payload is ``length - 16`` bytes.

Everything below is checked against a real ZBrush-written ``.GoZ`` file when one
is present (see the last checks in main()).
"""

import io
import os
import struct
import sys

import addon_utils
import bpy

EXT = os.environ.get("GOB_EXT", "bl_ext.user_default.gob")
FAILURES = []
CHECKS = 0

MAGIC = b"GoZb 1.0 ZBrush GoZ Binary"

TAG_VERTICES = b"\x11\x27\x00\x00"
TAG_FACES = b"\x21\x4e\x00\x00"
TAG_NAME = b"\x89\x13\x00\x00"


def check(label, condition, detail=""):
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"PASS | {label}", flush=True)
    else:
        FAILURES.append(label)
        print(f"FAIL | {label} | {detail}", flush=True)


class RecordingOperator:
    """Stands in for the import operator so warnings can be asserted on."""

    def __init__(self):
        self.warnings = []

    def report(self, level, message):
        self.warnings.append(message)

    def warning_text(self):
        return " | ".join(self.warnings)


def section(tag: bytes, payload: bytes, element_count: int | None = None,
            length_override: int | None = None) -> bytes:
    """Encode one GoZ section."""
    if element_count is None:
        element_count = len(payload)
    length = len(payload) + 16 if length_override is None else length_override
    return tag + struct.pack("<I", length) + struct.pack("<Q", element_count) + payload


def goz_file(name: str = "Cube", vertices: bytes | None = None,
             faces: bytes | None = None, *, extra_sections=b"") -> bytes:
    """Build a minimal but valid GoZ file using the exporter's byte layout."""
    if vertices is None:
        # Three vertices of three float32 each.
        vertices = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    if faces is None:
        faces = struct.pack("<4I", 0, 1, 2, 0xFFFFFFFF)

    out = io.BytesIO()
    out.write(MAGIC)                                   # 26 bytes
    out.write(struct.pack("<6B", *([0x2E] * 6)))       # 6  -> 32
    out.write(struct.pack("<I", 1))                    # 4  -> 36  object tag
    # length = len(name) + 24 so that the name itself is length - 24.
    out.write(struct.pack("<I", len(name) + 24))
    out.write(struct.pack("<Q", 1))                    # -> 48  payload starts
    out.write(b"GoZMesh_" + name.encode("utf-8"))      # -> 48 + 8 + len(name)
    out.write(struct.pack("<4B", 0x89, 0x13, 0x00, 0x00))   # trailer tag
    out.write(struct.pack("<I", 20))                        # 20-byte block
    out.write(struct.pack("<Q", 1))
    out.write(struct.pack("<I", 0))

    out.write(section(TAG_VERTICES, vertices, len(vertices) // 12))
    out.write(section(TAG_FACES, faces, len(faces) // 16))
    out.write(extra_sections)
    out.write(b"\x00" * 16)
    return out.getvalue()


def name_blob(name_bytes: bytes) -> bytes:
    """Build a file whose object name is exactly ``name_bytes``."""
    return goz_file_from_name_payload(name_bytes)


def goz_file_from_name_payload(name_payload: bytes) -> bytes:
    """Encode an arbitrary object-name payload, for corrupt-name tests."""
    out = io.BytesIO()
    out.write(MAGIC)
    out.write(struct.pack("<6B", *([0x2E] * 6)))
    out.write(struct.pack("<I", 1))
    # Keep the length consistent with len(name)+24 of the *intended* name, so
    # the parser's length arithmetic is exercised rather than short-circuited.
    out.write(struct.pack("<I", len(name_payload) + 24))
    out.write(struct.pack("<Q", 1))
    out.write(name_payload)
    out.write(struct.pack("<4B", 0x89, 0x13, 0x00, 0x00))
    out.write(struct.pack("<I", 20))
    out.write(struct.pack("<Q", 1))
    out.write(struct.pack("<I", 0))
    out.write(section(TAG_VERTICES, b"\x00" * 12, 1))
    return out.getvalue()


# ZBrush writes this file itself when a GoZ transfer runs; when it is present it
# is the authority on the layout, so it is used as a fixture rather than
# trusting this test's own encoder.
REAL_GOZ = os.path.join(
    os.environ.get("PUBLIC", r"C:\Users\Public"),
    "Pixologic",
    "GoZProjects",
    "Default",
)


def real_goz_files():
    if not os.path.isdir(REAL_GOZ):
        return []
    return sorted(
        os.path.join(REAL_GOZ, name)
        for name in os.listdir(REAL_GOZ)
        if name.lower().endswith(".goz")
    )


def main():
    print(f"TEST blender={bpy.app.version_string} python={sys.version.split()[0]}")
    print("=" * 72)

    addon_utils.enable(EXT, default_set=False, persistent=True)
    gob_import = sys.modules.get(f"{EXT}.gob_import")
    if gob_import is None:
        print(f"FAIL | extension {EXT!r} is not available")
        return 1

    read_section = gob_import._read_goz_section
    read_name = gob_import._read_goz_object_name
    skip_unknown = gob_import._skip_unknown_goz_section

    # --- the format assumptions these tests rely on ------------------------
    data = goz_file("Cube")
    check("synthetic file starts with the magic", data.startswith(MAGIC))

    # The name record must leave the stream on the first mesh tag. Getting this
    # wrong was a real bug: the parser read the trailer into the name and left
    # the 20-byte block to be misread as a mesh section.
    stream = io.BytesIO(data)
    check("object name round trips",
          read_name(stream, RecordingOperator()) == "Cube")
    check("name record leaves the stream on the first mesh tag",
          stream.read(4) == TAG_VERTICES,
          f"landed on {data[stream.tell()-4:stream.tell()].hex(' ')}")

    # A longer name must not shift the alignment either.
    for probe in ("A", "Ab", "Cylinder3D", "a_very_long_object_name_indeed"):
        stream = io.BytesIO(goz_file(probe))
        got = read_name(stream, RecordingOperator())
        check(f"name {probe!r} round trips and realigns",
              got == probe and stream.read(4) == TAG_VERTICES,
              f"name={got!r} next={stream.read(4)!r}")

    # --- a well-formed section ---------------------------------------------
    payload = b"\x01\x02\x03\x04"
    stream = io.BytesIO(section(TAG_VERTICES, payload, 7))
    operator = RecordingOperator()
    stream.read(4)  # the caller consumes the tag
    count, body = read_section(stream, operator, "Vertices")
    check("well-formed section returns its count", count == 7, f"count={count}")
    check("well-formed section returns its payload", body == payload)
    check("well-formed section warns about nothing", not operator.warnings,
          operator.warning_text())

    # --- truncated header ---------------------------------------------------
    operator = RecordingOperator()
    stream = io.BytesIO(b"\x01\x02\x03")           # fewer than 12 header bytes
    _, body = read_section(stream, operator, "Vertices")
    check("truncated header yields an empty payload", body == b"")
    check("truncated header is reported",
          any("truncated header" in w for w in operator.warnings),
          operator.warning_text())

    # --- length smaller than the fixed header ------------------------------
    operator = RecordingOperator()
    stream = io.BytesIO(struct.pack("<I", 8) + struct.pack("<Q", 5) + b"junk")
    _, body = read_section(stream, operator, "Vertices")
    check("impossible length yields an empty payload", body == b"")
    check("impossible length is reported",
          any("invalid length" in w for w in operator.warnings),
          operator.warning_text())

    # --- payload shorter than the declared length --------------------------
    operator = RecordingOperator()
    stream = io.BytesIO(
        struct.pack("<I", 100) + struct.pack("<Q", 3) + b"only-a-few-bytes"
    )
    count, body = read_section(stream, operator, "Vertices")
    check("short payload is reported as truncated",
          any("truncated" in w for w in operator.warnings),
          operator.warning_text())
    check("short payload returns what was available",
          body == b"only-a-few-bytes", f"body={body!r}")
    check("short payload still reports the declared count", count == 3)

    # --- an empty payload is not an error -----------------------------------
    operator = RecordingOperator()
    stream = io.BytesIO(struct.pack("<I", 16) + struct.pack("<Q", 0))
    count, body = read_section(stream, operator, "Vertices")
    check("empty payload is accepted", count == 0 and body == b"")
    check("empty payload warns about nothing", not operator.warnings,
          operator.warning_text())

    # --- section length must not swallow the following tag ------------------
    # This is the failure the length-minus-16 rule exists to prevent: reading
    # too far would eat the next section's tag and desynchronise the parser.
    body_bytes = b"AB"
    first = section(TAG_VERTICES, body_bytes, 1)
    following = section(TAG_FACES, b"CD", 1)
    stream = io.BytesIO(first + following)
    stream.read(4)
    operator = RecordingOperator()
    _, body = read_section(stream, operator, "Vertices")
    check("a section does not consume the next tag", body == body_bytes,
          f"body={body!r}")
    check("the next tag is still readable", stream.read(4) == TAG_FACES,
          "the parser would desynchronise here")

    # --- the fixed header must be complete ---------------------------------
    operator = RecordingOperator()
    result = read_name(io.BytesIO(MAGIC + b"\x00" * 5), operator)
    check("object name rejected: truncated header", result is None,
          f"got {result!r}")
    check("object name reported: truncated header",
          any("truncated" in w.lower() for w in operator.warnings),
          operator.warning_text())

    # --- object name validation --------------------------------------------
    # Each payload must be long enough for a full 24-byte name record, so the
    # validation being tested is the one that actually fails.
    cases = [
        ("missing prefix", b"NotAGoZMesh_" + b"\x00" * 12, "prefix"),
        ("empty name", b"GoZMesh_" + b"\x00" * 16, "empty"),
    ]
    for label, payload, expected in cases:
        operator = RecordingOperator()
        result = read_name(
            io.BytesIO(goz_file_from_name_payload(payload)), operator
        )
        check(f"object name rejected: {label}", result is None, f"got {result!r}")
        check(f"object name reported: {label}",
              any(expected in w.lower() for w in operator.warnings),
              operator.warning_text())

    # --- a valid name with unsupported bytes is sanitised, not fatal --------
    operator = RecordingOperator()
    result = read_name(
        io.BytesIO(goz_file_from_name_payload(b"GoZMesh_Cu\xffbe" + b"\x00" * 8)),
        operator,
    )
    check("invalid UTF-8 in the name is recovered", result == "Cube",
          f"got {result!r}")

    # --- unknown tags are skipped using the right length base --------------
    # Unknown sections are length-prefixed without the count field, so the
    # payload is length - 8.
    unknown_payload = b"unknown-payload"
    unknown = (b"\xde\xad\xbe\xef" + struct.pack("<I", len(unknown_payload) + 8)
               + unknown_payload)
    stream = io.BytesIO(unknown)
    stream.read(4)
    operator = RecordingOperator()
    skipped = skip_unknown(stream, operator, b"\xde\xad\xbe\xef", "Test")
    check("unknown section is skipped", skipped)
    check("skipping leaves the stream at the next tag",
          stream.tell() == len(unknown), f"tell={stream.tell()}")

    # --- a full file parses section by section ------------------------------
    fixture = goz_file(
        "Named",
        extra_sections=section(
            b"\x32\x75\x00\x00", struct.pack("<3H", 0, 32768, 65535), 3
        ),
    )
    stream = io.BytesIO(fixture)
    check("full fixture has the expected object name",
          read_name(stream, RecordingOperator()) == "Named")
    operator = RecordingOperator()
    tag = stream.read(4)
    check("first mesh tag is Vertices", tag == TAG_VERTICES, f"tag={tag!r}")
    count, body = read_section(stream, operator, "Vertices")
    check("vertices section has three records", count == 3, f"count={count}")
    check("vertices payload is 36 bytes", len(body) == 36, f"len={len(body)}")

    # --- real ZBrush output is the authority on the layout -------------------
    # Generated by ZBrush itself during a GoZ transfer. When present it proves
    # the parser agrees with the real world, not just with this test's encoder.
    real_files = real_goz_files()
    if not real_files:
        print("INFO | no ZBrush-written .GoZ files found; skipping the real-file check")
    for path in real_files:
        with open(path, "rb") as handle:
            raw = handle.read()
        operator = RecordingOperator()
        parsed = read_name(io.BytesIO(raw), operator)
        expected = os.path.splitext(os.path.basename(path))[0]
        check(f"parses the real file {os.path.basename(path)}",
              parsed == expected,
              f"name={parsed!r} expected={expected!r} warnings={operator.warnings}")
        check(f"real file parses without warnings: {os.path.basename(path)}",
              not operator.warnings, operator.warning_text())

    print("=" * 72)
    print(f"RESULT checks={CHECKS} failures={len(FAILURES)}")
    for failure in FAILURES:
        print(f"  FAILED: {failure}")
    print("VERDICT", "ALL_PASS" if not FAILURES else "FAILURES_PRESENT")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
