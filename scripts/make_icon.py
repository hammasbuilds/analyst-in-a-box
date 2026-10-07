"""Draw the app icon (four white bars on a teal tile) and write assets/analyst-in-a-box.ico.

Pure standard library: pixels are computed here and stored as PNG entries inside the .ico.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SS = 3  # supersampling per axis
BARS = [(0.22, 0.36), (0.38, 0.22), (0.54, 0.46), (0.70, 0.30)]  # x centre, top (fraction of height)


def inside_tile(x: float, y: float, s: float) -> bool:
    r = s * 0.22
    cx, cy = min(max(x, r), s - r), min(max(y, r), s - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def in_bar(x: float, y: float, s: float) -> bool:
    w = s * 0.095
    for cx, top in BARS:
        x0, x1, y0, y1 = cx * s - w, cx * s + w, top * s, s * 0.78
        rr = w
        if x0 <= x <= x1 and y0 + rr <= y <= y1 - rr:
            return True
        for ex, ey in ((cx * s, y0 + rr), (cx * s, y1 - rr)):
            if (x - ex) ** 2 + (y - ey) ** 2 <= rr * rr:
                return True
    return False


def render(size: int) -> bytes:
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            cov = bars = 0
            for sy in range(SS):
                for sx in range(SS):
                    x, y = px + (sx + 0.5) / SS, py + (sy + 0.5) / SS
                    if inside_tile(x, y, size):
                        cov += 1
                        bars += in_bar(x, y, size)
            n = SS * SS
            if cov == 0:
                row += bytes([0, 0, 0, 0])
            else:
                t = bars / cov
                r, g, b = (round(15 + (255 - 15) * t), round(118 + (255 - 118) * t), round(110 + (255 - 110) * t))
                row += bytes([r, g, b, round(255 * cov / n)])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> None:
    sizes = [16, 32, 48, 64, 128, 256]
    pngs = [render(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, p in zip(sizes, pngs, strict=True):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(p), offset)
        offset += len(p)
    out = Path(__file__).resolve().parents[1] / "assets" / "analyst-in-a-box.ico"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(head + entries + b"".join(pngs))
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
