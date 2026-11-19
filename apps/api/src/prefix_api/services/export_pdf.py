"""A readable PDF of a patient's data export, written without a PDF library.

It uses the standard Helvetica font, which every PDF reader has, so the file carries no
embedded font. Helvetica covers Latin script only (Windows-1252). A value in another script,
such as Hindi, is replaced by a pointer to the JSON export, which keeps every character.
"""

import zlib
from collections.abc import Iterable, Iterator
from typing import Any

PAGE_WIDTH, PAGE_HEIGHT = 595, 842  # A4 in points
MARGIN = 50
FONT_SIZE, HEADING_SIZE, TITLE_SIZE = 9.5, 12, 16
LEADING = 13
# Helvetica averages about half an em per character; wrap a little short of the margin.
CHARS_PER_LINE = 100
OTHER_SCRIPT = "[text in another script: see the JSON export]"

SECTION_TITLES = {
    "patient": "Patient",
    "consent_history": "Consent history",
    "appointments": "Procedures",
    "prep_timeline": "Prep timeline",
    "check_ins": "Check-ins",
    "messages": "Messages",
    "assistant_questions": "Questions to the assistant",
    "escalations": "Escalations to the care team",
    "waitlist": "Waitlist",
}


def _latin(value: object) -> str:
    text = "" if value is None else str(value)
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return OTHER_SCRIPT
    return text


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _wrap(text: str, width: int = CHARS_PER_LINE) -> Iterator[str]:
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split(" "):
            while len(word) > width:
                if line:
                    yield line
                    line = ""
                yield word[:width]
                word = word[width:]
            candidate = f"{line} {word}" if line else word
            if len(candidate) > width:
                yield line
                line = word
            else:
                line = candidate
        yield line


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _lines(data: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """(style, text) pairs: style is "title", "heading", "text" or "gap"."""
    meta = data.get("export", {})
    yield "title", "Personal data held by " + _latin(meta.get("organization", "the hospital"))
    generated, version = _latin(meta.get("generated_at")), meta.get("format_version")
    yield "text", f"Generated {generated}. Format version {version}."
    yield "text", "The JSON file from the same export holds the same data in full."
    for key, title in SECTION_TITLES.items():
        section = data.get(key)
        yield "gap", ""
        yield "heading", title
        if isinstance(section, dict):
            for field, value in section.items():
                for i, part in enumerate(_wrap(f"{_label(field)}: {_latin(value)}")):
                    yield "text", part if i == 0 else "    " + part
        elif not section:
            yield "text", "None."
        else:
            for n, item in enumerate(section, start=1):
                yield "text", f"{n}."
                for field, value in item.items():
                    for i, part in enumerate(_wrap(f"{_label(field)}: {_latin(value)}", 96)):
                        yield "text", ("    " if i == 0 else "        ") + part


def _pages(lines: Iterable[tuple[str, str]]) -> list[bytes]:
    pages: list[bytes] = []
    ops: list[bytes] = []
    y: float = PAGE_HEIGHT - MARGIN

    def flush() -> None:
        nonlocal ops, y
        pages.append(b"\n".join(ops))
        ops = []
        y = PAGE_HEIGHT - MARGIN

    for style, text in lines:
        size = {"title": TITLE_SIZE, "heading": HEADING_SIZE}.get(style, FONT_SIZE)
        step = LEADING / 2 if style == "gap" else LEADING + (size - FONT_SIZE)
        if y - step < MARGIN:
            flush()
        y -= step
        if style == "gap":
            continue
        font = b"/F2" if style in ("title", "heading") else b"/F1"
        ops.append(
            b"BT "
            + font
            + b" %.1f Tf %d %.1f Td (" % (size, MARGIN, y)
            + _escape(text)
            + b") Tj ET"
        )
    if ops or not pages:
        flush()
    return pages


def render_patient_export(data: dict[str, Any]) -> bytes:
    pages = _pages(_lines(data))
    total = len(pages)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, filled in once the page objects are numbered
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    kids: list[int] = []
    for number, content in enumerate(pages, start=1):
        footer = b"BT /F1 8 Tf %d %d Td (Page %d of %d) Tj ET" % (
            MARGIN,
            MARGIN - 20,
            number,
            total,
        )
        stream = zlib.compress(content + b"\n" + footer)
        objects.append(
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
            + stream
            + b"\nendstream"
        )
        content_id = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] " % (PAGE_WIDTH, PAGE_HEIGHT)
            + b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>" % content_id
        )
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        total,
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
