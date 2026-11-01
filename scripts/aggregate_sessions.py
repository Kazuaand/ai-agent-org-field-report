#!/usr/bin/env python3
"""Aggregate token usage from Claude Code session transcripts (JSONL).

Reads one or more `projects` directories written by the Claude Code CLI
(`<root>/<project>/<session>.jsonl` for main sessions and
`<root>/<project>/<session>/subagents/agent-*.jsonl` for subagents) and writes
ONLY aggregated CSV tables. No per-session, per-day or per-path rows are
written, and no identifiers (session ids, paths, branch names) leave this
script.

Usage:
    python3 aggregate_sessions.py --since 2026-10-01 --until 2026-11-01 \
        --out ../data  ROOT [ROOT ...]

Definitions (see report.md, "Method"):
  * request   = one API call, identified by requestId (several JSONL lines can
                share one usage object; the record with the largest
                output_tokens is kept).
  * gap       = start-to-start time between consecutive requests of the same
                main session file, in time order.
  * TTL       = 1 h if the previous request wrote any 1-hour cache entries,
                5 min if it wrote only 5-minute entries; if it wrote nothing,
                the most recent TTL seen in that session is carried forward
                (default 1 h when none seen yet).
  * class     = "session_start" for the first request of a main session file,
                "warm" if gap <= TTL, "cold" if gap > TTL.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

# Standard first-party list prices, USD per million tokens (input, output).
# Source: platform.claude.com/docs/en/about-claude/pricing (rates as published
# in the 2026-10 model table). Cache write = 1.25x input (5 min) / 2x input
# (1 h). Cache read multiplier is model-specific (see READ_MULT).
PRICES = {
    "fable-5-1": (10.0, 50.0),
    "fable-5": (10.0, 50.0),
    "opus-5-5": (4.0, 20.0),
    "opus-5": (5.0, 25.0),
    "opus-4-8": (5.0, 25.0),
    "opus-4-7": (5.0, 25.0),
    "opus-4-6": (5.0, 25.0),
    "sonnet-5-5": (2.0, 10.0),
    "sonnet-5": (2.0, 10.0),
    "sonnet-4-6": (3.0, 15.0),
    "haiku-5-5": (0.10, 0.50),
    "haiku-4-5": (1.0, 5.0),
}
READ_MULT = {"fable-5-1": 0.025, "opus-5-5": 0.05}  # others: 0.1
WRITE_5M, WRITE_1H = 1.25, 2.0

GAP_BINS = [  # (label, upper bound in seconds, inclusive)
    ("0-5m", 300),
    ("5-60m", 3600),
    ("1-2h", 7200),
    ("2-6h", 21600),
    ("6-24h", 86400),
    (">24h", float("inf")),
]


def model_key(model: str) -> str | None:
    m = (model or "").replace("claude-", "")
    for k in sorted(PRICES, key=len, reverse=True):
        if m.startswith(k):
            return k
    return None


def family(model: str) -> str:
    m = (model or "").lower()
    for f in ("fable", "opus", "sonnet", "haiku"):
        if f in m:
            return f.capitalize()
    return "other"


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def read_requests(path: str, t0: float, t1: float) -> list[dict]:
    """Return deduplicated requests in [t0, t1) from one JSONL file."""
    by_req: dict[str, dict] = {}
    with open(path, "rb") as fh:
        for raw in fh:
            if b'"cache_creation_input_tokens"' not in raw:
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            u = msg.get("usage") or {}
            model = msg.get("model") or ""
            if not u or model.startswith("<"):
                continue
            ts = d.get("timestamp")
            if not ts:
                continue
            t = parse_ts(ts)
            if not (t0 <= t < t1):
                continue
            rid = d.get("requestId") or d.get("uuid")
            cc = u.get("cache_creation") or {}
            rec = {
                "rid": rid,
                "t": t,
                "model": model,
                "effort": d.get("effort") or "unknown",
                "input": int(u.get("input_tokens") or 0),
                "write": int(u.get("cache_creation_input_tokens") or 0),
                "w1h": int(cc.get("ephemeral_1h_input_tokens") or 0),
                "w5m": int(cc.get("ephemeral_5m_input_tokens") or 0),
                "read": int(u.get("cache_read_input_tokens") or 0),
                "output": int(u.get("output_tokens") or 0),
            }
            prev = by_req.get(rid)
            if prev is None or rec["output"] >= prev["output"]:
                if prev is not None:
                    rec["t"] = min(rec["t"], prev["t"])
                by_req[rid] = rec
    return sorted(by_req.values(), key=lambda r: r["t"])


def cost(r: dict) -> dict:
    k = model_key(r["model"])
    if k is None:
        return {"input": None, "write": None, "read": None, "output": None}
    pin, pout = PRICES[k]
    rm = READ_MULT.get(k, 0.1)
    w5 = r["w5m"]
    w1 = r["w1h"]
    other = max(r["write"] - w5 - w1, 0)  # unlabeled writes priced as 5 min
    return {
        "input": r["input"] * pin / 1e6,
        # unlabeled writes are priced at the 5-minute rate
        "write": ((w5 + other) * WRITE_5M + w1 * WRITE_1H) * pin / 1e6,
        "read": r["read"] * rm * pin / 1e6,
        "output": r["output"] * pout / 1e6,
    }


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="UTC date, inclusive")
    ap.add_argument("--until", required=True, help="UTC date, exclusive")
    ap.add_argument("--out", required=True)
    ap.add_argument("roots", nargs="+")
    a = ap.parse_args()
    t0 = datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc).timestamp()
    t1 = datetime.fromisoformat(a.until).replace(tzinfo=timezone.utc).timestamp()
    os.makedirs(a.out, exist_ok=True)

    seen_req: set[str] = set()
    seen_file: set[str] = set()  # session file basenames (dedupe copies)
    rows_class = defaultdict(lambda: {"write": [], "read": [], "hit": []})
    rows_bin = defaultdict(lambda: {"write": [], "read": [], "hit": []})
    comp = defaultdict(lambda: defaultdict(float))
    cov = defaultdict(int)

    def files(root):
        for proj in sorted(os.listdir(root)):
            pdir = os.path.join(root, proj)
            if not os.path.isdir(pdir):
                continue
            for name in sorted(os.listdir(pdir)):
                p = os.path.join(pdir, name)
                if name.endswith(".jsonl"):
                    yield "main", p, name
                elif os.path.isdir(os.path.join(p, "subagents")):
                    sd = os.path.join(p, "subagents")
                    for s in sorted(os.listdir(sd)):
                        if s.endswith(".jsonl"):
                            yield "subagent", os.path.join(sd, s), s

    for root in a.roots:
        for scope, path, base in files(root):
            try:
                if os.path.getmtime(path) < t0:
                    continue
            except OSError:
                continue
            if base in seen_file:
                cov["duplicate_files_skipped"] += 1
                continue
            seen_file.add(base)
            new = []
            for r in read_requests(path, t0, t1):
                if r["rid"] in seen_req:
                    cov["duplicate_requests_skipped"] += 1
                    continue
                seen_req.add(r["rid"])
                new.append(r)
            if not new:
                continue
            cov[f"{scope}_files"] += 1
            cov[f"{scope}_requests"] += len(new)

            # composition (all scopes)
            for r in new:
                g = (scope, family(r["model"]), r["effort"])
                c = cost(r)
                comp[g]["requests"] += 1
                for k in ("input", "w5m", "w1h", "read", "output"):
                    comp[g][k] += r[k]
                comp[g]["write_unlabeled"] += max(r["write"] - r["w5m"] - r["w1h"], 0)
                if c["input"] is None:
                    comp[g]["unpriced_requests"] += 1
                else:
                    for k, v in c.items():
                        comp[g][f"usd_{k}"] += v

            if scope != "main":
                continue
            ttl = 3600.0
            prev_t = None
            for i, r in enumerate(new):
                denom = r["input"] + r["write"] + r["read"]
                hit = r["read"] / denom if denom else float("nan")
                if prev_t is None:
                    cls, label = "session_start", "start"
                else:
                    gap = r["t"] - prev_t
                    cls = "cold" if gap > ttl else "warm"
                    label = next(lbl for lbl, ub in GAP_BINS if gap <= ub)
                for bucket in (rows_class[cls], rows_bin[label]):
                    bucket["write"].append(r["write"])
                    bucket["read"].append(r["read"])
                    bucket["hit"].append(hit)
                if r["w1h"] > 0:
                    ttl = 3600.0
                elif r["w5m"] > 0:
                    ttl = 300.0
                prev_t = r["t"]

    def summarize(name, table, order):
        tw = sum(sum(b["write"]) for b in table.values()) or float("nan")
        tr = sum(sum(b["read"]) for b in table.values()) or float("nan")
        with open(os.path.join(a.out, name), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["group", "n_requests", "write_median", "write_p90",
                        "write_share_pct", "read_median", "read_p90", "read_share_pct",
                        "hit_ratio_median"])
            for k in order:
                if k not in table:
                    continue
                b = table[k]
                hits = [h for h in b["hit"] if h == h]
                w.writerow([k, len(b["write"]),
                            int(statistics.median(b["write"])), int(pct(b["write"], 0.9)),
                            f"{sum(b['write']) / tw * 100:.3f}",
                            int(statistics.median(b["read"])), int(pct(b["read"], 0.9)),
                            f"{sum(b['read']) / tr * 100:.3f}",
                            round(statistics.median(hits), 4) if hits else ""])

    summarize("cache_by_class.csv", rows_class, ["session_start", "warm", "cold"])
    summarize("cache_by_gap.csv", rows_bin, ["start"] + [l for l, _ in GAP_BINS])

    # Shares only: each row's share (%) of all requests, of each token category
    # and of the estimated cost. Absolute token totals and amounts are not written,
    # so the total spend cannot be reconstructed from the table.
    cols = ["requests", "input", "w5m", "w1h", "write_unlabeled", "read", "output"]
    usd = ["usd_input", "usd_write", "usd_read", "usd_output"]
    tot = {k: sum(comp[g][k] for g in comp) for k in cols + usd}
    grand = sum(tot[k] for k in usd) or float("nan")
    share = lambda v, t: f"{v / t * 100:.3f}" if t else "0.000"
    with open(os.path.join(a.out, "composition.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scope", "model_family", "effort"]
                   + [f"pct_of_all_{k}" for k in cols]
                   + [f"est_cost_pct_{k[4:]}" for k in usd] + ["unpriced_requests"])
        for g in sorted(comp):
            w.writerow(list(g) + [share(comp[g][k], tot[k]) for k in cols]
                       + [share(comp[g][k], grand) for k in usd]
                       + [int(comp[g]["unpriced_requests"])])
    with open(os.path.join(a.out, "token_mix.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["token_category", "pct_of_all_tokens", "est_cost_pct"])
        alltok = sum(tot[k] for k in cols[1:]) or float("nan")
        cost_of = {"input": "usd_input", "read": "usd_read", "output": "usd_output"}
        for k in cols[1:]:
            c = share(tot[cost_of[k]], grand) if k in cost_of else ""
            w.writerow([k, f"{tot[k] / alltok * 100:.4f}", c])
        w.writerow(["write_all", f"{(tot['w5m'] + tot['w1h'] + tot['write_unlabeled']) / alltok * 100:.4f}",
                    share(tot["usd_write"], grand)])

    with open(os.path.join(a.out, "coverage.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "value"])
        w.writerow(["window_start_utc", a.since])
        w.writerow(["window_end_utc_exclusive", a.until])
        for k in sorted(cov):
            if not k.startswith("duplicate_"):  # internal bookkeeping only
                w.writerow([k, cov[k]])
    print(json.dumps(dict(cov)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
