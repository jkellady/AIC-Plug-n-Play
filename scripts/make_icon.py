"""Generate the HR Attendance app icon (multi-size .ico, PNG-compressed entries).

Standard library only (struct + zlib). Draws a rounded gradient square with a
simple clock glyph pixel by pixel, with 3x3 supersampling for smooth edges.

    python scripts/make_icon.py                 -> scripts/assets/hr_attendance.ico
    python scripts/make_icon.py path/to/out.ico
"""
import math
import struct
import sys
import zlib
from pathlib import Path

SIZES = (16, 32, 48, 256)
SS = 3  # supersampling per axis


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _sample(u, v):
    """Colour at unit-square point (u, v) in 0..1. Returns (r, g, b, a) floats 0..255."""
    # Rounded square, corner radius 0.22.
    r = 0.22
    qx, qy = abs(u - 0.5) - (0.5 - r), abs(v - 0.5) - (0.5 - r)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
    if outside > 0:
        return (0.0, 0.0, 0.0, 0.0)
    # Diagonal gradient: deep blue (top-left) to teal (bottom-right).
    t = (u + v) / 2
    base = (30, int(64 + 112 * t), int(175 - 20 * t))
    # Clock face: white ring + white hands.
    cx, cy = 0.5, 0.5
    d = math.hypot(u - cx, v - cy)
    ring = abs(d - 0.30) < 0.045
    hand_min = _seg_dist(u, v, cx, cy, cx, cy - 0.22) < 0.035
    hand_hr = _seg_dist(u, v, cx, cy, cx + 0.15, cy + 0.06) < 0.035
    hub = d < 0.05
    if ring or hand_min or hand_hr or hub:
        return (255.0, 255.0, 255.0, 255.0)
    return (float(base[0]), float(base[1]), float(base[2]), 255.0)


def render_rgba(size):
    rows = []
    n = SS * SS
    for y in range(size):
        row = bytearray([0])  # PNG filter type 0
        for x in range(size):
            ar = ag = ab = aa = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    r, g, b, a = _sample((x + (sx + 0.5) / SS) / size, (y + (sy + 0.5) / SS) / size)
                    ar += r * a
                    ag += g * a
                    ab += b * a
                    aa += a
            if aa == 0:
                row += bytes((0, 0, 0, 0))
            else:
                row += bytes((round(ar / aa), round(ag / aa), round(ab / aa), round(aa / n)))
        rows.append(bytes(row))
    return b"".join(rows)


def png_bytes(size):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(render_rgba(size), 9)) + chunk(b"IEND", b""))


def build_ico(path):
    images = [png_bytes(s) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for s, data in zip(SIZES, images):
        dim = 0 if s >= 256 else s  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + b"".join(images))
    return path


if __name__ == "__main__":
    default = Path(__file__).resolve().parent / "assets" / "hr_attendance.ico"
    out = build_ico(sys.argv[1] if len(sys.argv) > 1 else default)
    print(f"Wrote {out} ({out.stat().st_size} bytes, sizes {', '.join(map(str, SIZES))})")
