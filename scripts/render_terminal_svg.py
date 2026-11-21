#!/usr/bin/env python3
"""Render captured terminal output as an SVG, for the README. The text is whatever you pass in - nothing is drawn
that a run did not print - so the picture cannot drift from the tool.

    scripts/render_terminal_svg.py out.svg --title "voicerig run" part1.txt part2.txt

Each input file is one block; a line starting with "$ " is shown as a typed command. Colours follow the verdicts.
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

CHAR_W, LINE_H, PAD_X, PAD_Y, BAR = 9.3, 20, 22, 18, 36
COLORS = {"PASS": "#3fb950", "FAIL": "#f85149", "INCONCLUSIVE": "#d29922"}


def colour(line: str) -> tuple[str, bool]:
    for verdict, c in COLORS.items():
        if line.startswith(f"[{verdict}]"):
            return c, True
    if line.startswith("===="):
        return ("#3fb950" if " 0 failed, 0 inconclusive" in line else "#f85149"), True
    if line.startswith("$ "):
        return "#e6edf3", True
    if line.lstrip().startswith("- "):
        return "#8b949e", False
    return "#c9d1d9", False


def render(blocks: list[list[str]], title: str, width_chars: int) -> str:
    lines: list[str] = []
    for i, b in enumerate(blocks):
        if i:
            lines.append("")
        lines += b
    width = int(PAD_X * 2 + CHAR_W * width_chars)
    height = BAR + PAD_Y * 2 + LINE_H * len(lines)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">',
           f'<rect width="{width}" height="{height}" rx="10" fill="#0d1117"/>',
           f'<rect width="{width}" height="{BAR}" rx="10" fill="#161b22"/><rect y="{BAR - 10}" width="{width}" height="10" fill="#161b22"/>',
           '<circle cx="20" cy="18" r="6" fill="#f85149"/><circle cx="40" cy="18" r="6" fill="#d29922"/><circle cx="60" cy="18" r="6" fill="#3fb950"/>',
           f'<text x="{width / 2}" y="23" fill="#8b949e" font-family="ui-monospace,Menlo,Consolas,monospace" font-size="13" text-anchor="middle">{html.escape(title)}</text>']
    y = BAR + PAD_Y + 14
    for line in lines:
        if line:
            c, bold = colour(line)
            weight = ' font-weight="bold"' if bold else ""
            out.append(f'<text x="{PAD_X}" y="{y}" fill="{c}"{weight} font-family="ui-monospace,Menlo,Consolas,monospace" '
                       f'font-size="14" xml:space="preserve">{html.escape(line)}</text>')
        y += LINE_H
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--title", default="terminal")
    a = ap.parse_args()
    blocks = [Path(p).read_text().rstrip("\n").split("\n") for p in a.inputs]
    width = max(len(l) for b in blocks for l in b)
    Path(a.out).write_text(render(blocks, a.title, min(max(width, 60), 120)) + "\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
