"""SHF's unit tables are PNGs of a ruled grid: find the grid in the pixels and OCR every cell on its own
(tesseract alone mis-reads whole rows of this table). Decodes the PNG itself, so no image library is needed."""

import os
import re
import struct
import subprocess
import tempfile
import zlib


def png_gray(data: bytes) -> tuple[int, int, list[bytes]]:
    """Decode an 8-bit non-interlaced PNG to grayscale rows."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, pal = 8, b"", b""
    w = h = depth = ctype = inter = 0
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat += body
        elif typ == b"PLTE":
            pal = body
    if depth != 8 or inter:
        raise ValueError(f"unsupported PNG (depth {depth}, interlace {inter})")
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * ch
    prev = bytearray(stride)
    rows, i = [], 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1 : i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - ch] if x >= ch else 0
            b = prev[x]
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                c = prev[x - ch] if x >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        if ctype == 3:
            rows.append(bytes((pal[3 * v] * 299 + pal[3 * v + 1] * 587 + pal[3 * v + 2] * 114) // 1000 for v in line))
        elif ch >= 3:
            rows.append(
                bytes((line[k] * 299 + line[k + 1] * 587 + line[k + 2] * 114) // 1000 for k in range(0, stride, ch))
            )
        else:
            rows.append(bytes(line[0::ch]))
    return w, h, rows


def _runs(flags: list[bool], off: int = 0) -> list[tuple[int, int]]:
    out, s = [], None
    for i, f in enumerate([*flags, False]):
        if f and s is None:
            s = i
        elif not f and s is not None:
            out.append((s + off, i + off))
            s = None
    return out


def _gaps(lines: list[tuple[int, int]], lo: int, hi: int, minlen: int = 8) -> list[tuple[int, int]]:
    edges = [lo] + [x for r in lines for x in r] + [hi]
    return [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2) if edges[i + 1] - edges[i] >= minlen]


def _ocr_cell(rows: list[bytes], x0: int, x1: int, y0: int, y1: int, d: str, scale: int = 3) -> str:
    pad = 6
    w = x1 - x0 + 2 * pad
    blank = bytes([255] * w)
    seg = [blank] * pad + [bytes([255] * pad) + r[x0:x1] + bytes([255] * pad) for r in rows[y0:y1]] + [blank] * pad
    buf = bytearray()
    for r in seg:
        buf += bytes(v for v in r for _ in range(scale)) * scale
    p = os.path.join(d, "c.pgm")
    with open(p, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (w * scale, len(seg) * scale) + bytes(buf))
    out = subprocess.run(["tesseract", p, "-", "--psm", "6"], capture_output=True, text=True, timeout=60).stdout
    return re.sub(r"[ \t]+", " ", out.replace("|", " ")).strip()


def ocr_table(png: bytes) -> list[list[list[str]]]:
    """A ruled table image -> blocks (one per bordered row group) -> columns -> sub-rows of cell text."""
    w, h, rows = png_gray(png)

    def dark(v: int) -> bool:
        return v < 160

    wide = _runs([sum(map(dark, rows[y])) / w > 0.5 for y in range(h)])
    if len(wide) < 2:
        return []
    top, bottom = wide[0][1], wide[-1][0]
    vl = _runs([sum(dark(rows[y][x]) for y in range(top, bottom)) / max(1, bottom - top) > 0.9 for x in range(w)])
    if len(vl) < 3:
        return []
    left, right = vl[0][0], vl[-1][1]
    cols = _gaps(vl, left, right)
    # a block border runs across the whole table; household sub-row rules only cross the right columns
    borders = [
        r for r in _runs([sum(map(dark, rows[y][left:right])) / (right - left) > 0.9 for y in range(h)]) if r[0] >= top
    ]
    blocks = _gaps(borders, top, bottom, 12)
    out = []
    with tempfile.TemporaryDirectory() as d:
        for a, b in blocks:
            cells = []
            for x0, x1 in cols:
                sub = _runs([sum(map(dark, rows[y][x0:x1])) / (x1 - x0) > 0.8 for y in range(a, b)], a)
                parts = _gaps(sub, a, b) or [(a, b)]
                cells.append([_ocr_cell(rows, x0, x1, p, q, d) for p, q in parts])
            out.append(cells)
    return out
