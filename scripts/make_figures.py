#!/usr/bin/env python3
"""Regenerate the report figures (plain SVG, standard library only) from the
aggregated CSV files in ../data.

Usage:
    python3 make_figures.py --data ../data --out ../figures
"""
from __future__ import annotations

import argparse
import csv
import os
from html import escape

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"  # categorical slots 1-4
FONT = "font-family='Helvetica, Arial, sans-serif'"


def svg(w, h, body, title):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}' "
            f"role='img' aria-label='{escape(title)}'><title>{escape(title)}</title>"
            f"<rect width='{w}' height='{h}' fill='{SURFACE}'/>{body}</svg>\n")


def text(x, y, s, size=12, color=INK, anchor="start", weight="normal"):
    return (f"<text x='{x:.1f}' y='{y:.1f}' {FONT} font-size='{size}' fill='{color}' "
            f"text-anchor='{anchor}' font-weight='{weight}'>{escape(str(s))}</text>")


def fmt_k(v):
    return f"{v/1000:.0f}k" if v >= 1000 else str(int(v))


def fig_gap(data, out):
    rows = list(csv.DictReader(open(os.path.join(data, "cache_by_gap.csv"))))
    labels = {"start": "session\nstart"}
    W, H, L, R, T, B = 760, 400, 70, 20, 60, 70
    vmax = max(int(r["write_p90"]) for r in rows) * 1.08
    pw = (W - L - R) / len(rows)
    y = lambda v: T + (H - T - B) * (1 - v / vmax)
    body = [text(L, 24, "Cache-write tokens per request, by time since the previous request", 15, weight="bold"),
            text(L, 42, "Main sessions, one month. Bar = median, tick = 90th percentile. n under each bar.", 12, INK2)]
    for g in range(0, int(vmax) + 1, 50000):
        body.append(f"<line x1='{L}' x2='{W-R}' y1='{y(g):.1f}' y2='{y(g):.1f}' stroke='{GRID}' stroke-width='1'/>")
        body.append(text(L - 8, y(g) + 4, fmt_k(g), 11, INK2, "end"))
    for i, r in enumerate(rows):
        cx = L + pw * (i + 0.5)
        med, p90, n = int(r["write_median"]), int(r["write_p90"]), int(r["n_requests"])
        bw = pw * 0.5
        top = y(med)
        h = y(0) - top
        rad = min(4, h / 2)
        body.append(f"<path d='M{cx-bw/2:.1f},{y(0):.1f} V{top+rad:.1f} Q{cx-bw/2:.1f},{top:.1f} {cx-bw/2+rad:.1f},{top:.1f} "
                    f"H{cx+bw/2-rad:.1f} Q{cx+bw/2:.1f},{top:.1f} {cx+bw/2:.1f},{top+rad:.1f} V{y(0):.1f} Z' fill='{S1}'/>")
        body.append(f"<line x1='{cx-bw/2:.1f}' x2='{cx+bw/2:.1f}' y1='{y(p90):.1f}' y2='{y(p90):.1f}' stroke='{S2}' stroke-width='2'/>")
        ly = top - 6
        if abs(y(p90) - top) < 16:  # keep the label clear of the p90 tick
            ly = min(top, y(p90)) - 6
        body.append(text(cx, ly, fmt_k(med), 11, INK, "middle"))
        lab = labels.get(r["group"], r["group"]).split("\n")
        for j, part in enumerate(lab):
            body.append(text(cx, H - B + 18 + 13 * j, part, 12, INK, "middle"))
        body.append(text(cx, H - B + 18 + 13 * len(lab), f"n={n:,}", 10, INK2, "middle"))
    body.append(f"<line x1='{L}' x2='{W-R}' y1='{y(0):.1f}' y2='{y(0):.1f}' stroke='{INK2}' stroke-width='1'/>")
    body.append(f"<rect x='{W-R-210}' y='{T-6}' width='10' height='10' fill='{S1}' rx='2'/>")
    body.append(text(W - R - 195, T + 3, "median", 11, INK2))
    body.append(f"<line x1='{W-R-130}' x2='{W-R-118}' y1='{T-1}' y2='{T-1}' stroke='{S2}' stroke-width='2'/>")
    body.append(text(W - R - 113, T + 3, "90th percentile", 11, INK2))
    open(os.path.join(out, "fig1_cache_write_by_gap.svg"), "w").write(svg(W, H, "".join(body), "Cache-write tokens by gap"))


def fig_cost(data, out):
    rows = list(csv.DictReader(open(os.path.join(data, "composition.csv"))))
    cats = [("est_cost_pct_input", "uncached input", S1), ("est_cost_pct_write", "cache write", S2),
            ("est_cost_pct_read", "cache read", S3), ("est_cost_pct_output", "output", S4)]
    groups = {"main": "main sessions", "subagent": "subagents", "all": "all"}
    agg = {g: {c: 0.0 for c, _, _ in cats} for g in groups}
    for r in rows:
        for c, _, _ in cats:
            agg[r["scope"]][c] += float(r[c])
            agg["all"][c] += float(r[c])
    W, H, L, R, T = 760, 300, 120, 20, 70
    bh, gap = 40, 26
    pw = W - L - R
    body = [text(20, 24, "Estimated standard-rate cost share by token category", 15, weight="bold"),
            text(20, 42, "Share of each bar's estimate (list prices, no discounts). Labels shown for shares >= 3%.", 12, INK2)]
    lx = L
    for c, name, col in cats:
        body.append(f"<rect x='{lx}' y='{T-14}' width='10' height='10' fill='{col}' rx='2'/>")
        body.append(text(lx + 14, T - 5, name, 11, INK2))
        lx += 130
    for i, g in enumerate(groups):
        y0 = T + 10 + i * (bh + gap)
        tot = sum(agg[g].values())
        body.append(text(L - 10, y0 + bh / 2 + 4, groups[g], 12, INK, "end"))
        x = L
        for c, name, col in cats:
            w = pw * agg[g][c] / tot
            if w > 0.5:
                body.append(f"<rect x='{x:.1f}' y='{y0}' width='{max(w-2,0.5):.1f}' height='{bh}' fill='{col}'/>")
            share = agg[g][c] / tot * 100
            if share >= 3:
                body.append(text(x + w / 2 - 1, y0 + bh / 2 + 4, f"{share:.0f}%", 12, INK, "middle", "bold"))
            x += w
    open(os.path.join(out, "fig2_cost_share.svg"), "w").write(svg(W, H, "".join(body), "Estimated cost share by token category"))


def fig_detectors(data, out):
    rows = list(csv.DictReader(open(os.path.join(data, "detector_control.csv"))))
    dets = [k for k in rows[0] if k not in ("sample", "form_group")]
    names = {"publish_gate_stages_1_2": "gate (stages 1-2)"}
    W, L, T, cw, ch = 760, 230, 80, 120, 20
    H = T + ch * len(rows) + 60
    body = [text(20, 24, "Which detector reports each planted sample (one sample per file)", 15, weight="bold"),
            text(20, 42, "Y = reported, R = not a finding but queued for LLM-judge review, blank = not reported.", 12, INK2)]
    for j, d in enumerate(dets):
        body.append(text(L + cw * (j + 0.5), T - 10, names.get(d, d), 12, INK, "middle", "bold"))
    prev_group = None
    for i, r in enumerate(rows):
        yy = T + ch * i
        if r["form_group"] != prev_group:
            body.append(f"<line x1='20' x2='{L+cw*len(dets)}' y1='{yy}' y2='{yy}' stroke='{INK2}' stroke-width='1'/>")
            prev_group = r["form_group"]
        body.append(text(L - 10, yy + 14, r["sample"], 11, INK, "end"))
        for j, d in enumerate(dets):
            v = r[d]
            x = L + cw * j
            fill = {"1": S1, "review": "#a9c8ef"}.get(v, "#efeeea")
            body.append(f"<rect x='{x+1}' y='{yy+1}' width='{cw-2}' height='{ch-2}' fill='{fill}' rx='2'/>")
            mark = {"1": "Y", "review": "R", "not_run": "n/a"}.get(v, "")
            if mark:
                body.append(text(x + cw / 2, yy + 14, mark, 11, "#ffffff" if v == "1" else INK, "middle", "bold"))
    yy = T + ch * len(rows) + 22
    for j, d in enumerate(dets):
        n = sum(1 for r in rows if r[d] == "1")
        body.append(text(L + cw * (j + 0.5), yy, f"{n}/{len(rows)}", 12, INK, "middle", "bold"))
    body.append(text(L - 10, yy, "reported", 12, INK2, "end"))
    groups = []
    for r in rows:
        if r["form_group"] not in groups:
            groups.append(r["form_group"])
    body.append(text(20, yy + 22, "Row groups, top to bottom: " + "; ".join(groups) + ".", 11, INK2))
    open(os.path.join(out, "fig3_detector_control.svg"), "w").write(svg(W, H + 10, "".join(body), "Detector positive-control matrix"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    fig_gap(a.data, a.out)
    fig_cost(a.data, a.out)
    fig_detectors(a.data, a.out)


if __name__ == "__main__":
    main()
