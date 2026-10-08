"""The chart code runs in node, so its label handling is tested without a browser."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "src" / "analyst_in_a_box" / "static"
NODE = shutil.which("node")


def _run(js: str):
    if not NODE:
        pytest.skip("node is not installed")
    code = (STATIC / "charts.js").read_text(encoding="utf-8") + "\n;console.log(JSON.stringify((() => {" + js + "})()));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    return json.loads(out.stdout)


def _labels(svg: str) -> list[tuple[str, str]]:
    """(shown text, full label from its <title>) for every chart label that has a title."""
    import re

    return re.findall(r"<text[^>]*>([^<]*)<title>([^<]*)</title></text>", svg)


def test_clip_never_cuts_mid_word_when_a_break_is_close():
    got = _run('return ["United Kingdom", "Channel Islands", "European Community", "Denmark", "RSA"].map((s) => Charts.clip(s, 10));')
    assert got == ["United…", "Channel…", "European…", "Denmark", "RSA"]


def test_country_columns_keep_the_start_of_each_label():
    """The owner's screenshot: 32 countries as columns showed 'ited Kin', 'nmark', 'annel Is'
    because every label was sliced as if it were a date (characters 2 to 10)."""
    rows = [["United Kingdom", 9e6], ["Channel Islands", 2e4], ["Denmark", 1.8e4], ["2011-03", 5e3]]
    svg = _run(f"return Charts.columns({{ rows: {json.dumps(rows)} }});")
    for shown, full in _labels(svg):
        if full.startswith("2011-"):
            assert shown == full[2:]
        else:
            assert shown == full or (shown.endswith("…") and full.startswith(shown[:-1])), (shown, full)


def test_bar_labels_are_whole_or_ellipsised_with_the_full_label_as_tooltip():
    rows = [["Pack Of 72 Retrospot Cake Cases With A Very Long Name", 100], ["Mug", 5]]
    svg = _run(f"return Charts.bars({{ rows: {json.dumps(rows)}, labelW: 180 }});")
    labels = _labels(svg)
    assert ("Mug", "Mug") in labels
    long = [x for x in labels if x[1].startswith("Pack")][0]
    assert long[0].endswith("…") and long[1].startswith(long[0][:-1]) and not long[0][:-1].endswith(" ")


def test_line_chart_x_labels_never_overlap_and_keep_both_ends():
    """The forecast chart drew '2011-06-06' over '2011-07-18': the first label was anchored at the
    edge while the spacing still assumed centred labels."""
    import re

    for width in (520, 800, 1100):
        js = ("const labels = [...Array(39)].map((_, i) => '2011-' + String(1 + (i % 12)).padStart(2, '0') + '-0' + ((i % 9) + 1));"
              f"return Charts.line({{ labels, series: [{{ name: 'a', values: labels.map((_, i) => i) }}], width: {width} }});")
        svg = _run(js)
        ticks = re.findall(r'<text x="([\d.]+)" y="222" text-anchor="(\w+)">([^<]*)</text>', svg)
        assert ticks[0][1] == "start" and ticks[-1][1] == "end"
        boxes = []
        for x, anchor, label in ticks:
            x, w = float(x), len(label) * 6.8
            boxes.append((x, x + w) if anchor == "start" else (x - w, x) if anchor == "end" else (x - w / 2, x + w / 2))
        assert all(a[1] <= b[0] for a, b in zip(boxes, boxes[1:], strict=False)), (width, ticks)
        assert boxes[-1][1] <= width and boxes[0][0] >= 0
