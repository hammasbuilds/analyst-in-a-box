"""Draw the app icon (lavender, amber and money-green bars on a violet-to-indigo tile) and write assets/analyst-in-a-box.ico.

Pure standard library: pixels are computed here and stored as PNG entries inside the .ico.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SS = 3  # supersampling per axis
BARS = [(0.28, 0.54), (0.50, 0.38), (0.72, 0.22)]  # x centre, top (fraction of height); lavender, amber, money green


def inside_tile(x: float, y: float, s: float) -> bool:
    r = s * 0.22
    cx, cy = min(max(x, r), s - r), min(max(y, r), s - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def in_bar(x: float, y: float, s: float) -> int:
    """0 outside the bars, 1 lavender bar, 3 amber bar, 2 money-green bar."""
    w = s * 0.075
    for n, (cx, top) in enumerate(BARS):
        x0, x1, y0, y1 = cx * s - w, cx * s + w, top * s, s * 0.78
        rr = w
        hit = x0 <= x <= x1 and y0 + rr <= y <= y1 - rr
        for ex, ey in ((cx * s, y0 + rr), (cx * s, y1 - rr)):
            hit = hit or (x - ex) ** 2 + (y - ey) ** 2 <= rr * rr
        if hit:
            return 2 if n == len(BARS) - 1 else (3 if n == 1 else 1)
    return 0


def render(size: int) -> bytes:
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            cov = white = gold = amber = 0
            for sy in range(SS):
                for sx in range(SS):
                    x, y = px + (sx + 0.5) / SS, py + (sy + 0.5) / SS
                    if inside_tile(x, y, size):
                        cov += 1
                        hit = in_bar(x, y, size)
                        white += hit == 1
                        gold += hit == 2
                        amber += hit == 3
            n = SS * SS
            if cov == 0:
                row += bytes([0, 0, 0, 0])
            else:
                u = (px + py) / (2 * size)  # violet (139,92,255) to deep indigo (36,20,119) along the diagonal
                base = [139 - 103 * u, 92 - 72 * u, 255 - 136 * u]
                hl = max(0.0, 1 - (((px / size - 0.2) ** 2 + (py / size - 0.1) ** 2) ** 0.5) / 0.8) * 0.35  # top-left sheen
                base = [c + (255 - c) * hl for c in base]
                tw, tg, ta = white / cov, gold / cov, amber / cov
                r, g, b = (round(base[i] * (1 - tw - tg - ta) + (217, 208, 255)[i] * tw + (62, 232, 165)[i] * tg + (255, 180, 84)[i] * ta) for i in range(3))
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
