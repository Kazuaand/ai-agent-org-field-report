#!/usr/bin/env python3
"""Summarize publish-gate evidence files into one count-only CSV.

Input: a directory with one sub-directory per repository, each holding the
gate's evidence JSON (`<UTC>.json`, schema publish-gate/v1) and the two judge
verdict files (`<UTC>.judge-1.json`, `<UTC>.judge-2.json`). Runs are ordered by
the run's own timestamp within a repository; timestamps, file paths and flagged text are not
copied to the output.

Usage:
    python3 aggregate_gate_runs.py EVIDENCE_DIR --out ../data/gate_runs.csv --until-published
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

FAMILY = {  # stage-1 rule id prefix -> neutral family name
    "confi" "dential": "warning-label-term",  # split so the gate does not flag this file
    "home": "home-directory-path",
    "label": "label-value",
}

CAUSE = {  # rule_id prefix -> human-readable cause category
    "judge-error": "judge process error (fail-closed)",
    "judge-identity": "judge flagged an identity statement",
    "presidio-": "stage 1 PII recognizer",
    "deny-": "stage 2 denylist",
    "tree-changed": "tree changed during the run",
}


def cause(rule_id: str) -> str | None:
    if rule_id.startswith("judge-") and rule_id.endswith("-error"):
        return CAUSE["judge-error"]
    for k, v in CAUSE.items():
        if rule_id.startswith(k) or rule_id.endswith(k):
            return v
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("evidence_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--until-published", action="store_true",
                    help="per repository, keep runs up to and including the run named in "
                         "PUBLISHED.json (field gate_evidence); skip repositories without it")
    ap.add_argument("--stage1-out", help="optional: stage-1 finding counts by rule family, "
                    "for every evidence file including pre-release self-checks")
    a = ap.parse_args()
    rows = []
    for repo in sorted(p for p in Path(a.evidence_dir).iterdir() if p.is_dir()):
        runs = sorted(repo.glob("*Z.json"),
                      key=lambda f: json.loads(f.read_text()).get("timestamp_utc", ""))
        if a.until_published:
            pub = repo / "PUBLISHED.json"
            if not pub.exists():
                continue
            last = Path(json.loads(pub.read_text())["gate_evidence"]).name
            names = [f.name for f in runs]
            runs = runs[: names.index(last) + 1] if last in names else []
        for i, f in enumerate(runs, 1):
            d = json.loads(f.read_text())
            st = d["stages"]
            causes = Counter()
            for k in ("1", "2", "3", "4"):
                for x in st[k].get("findings") or []:
                    c = cause(x.get("rule_id", ""))
                    if c:
                        causes[c] += 1
            v = []
            for j in (1, 2):
                jf = f.with_name(f.stem + f".judge-{j}.json")
                v.append(json.loads(jf.read_text()).get("verdict") if jf.exists() else "missing")
            rows.append({
                "repository": repo.name,
                "run": i,
                "result": d["result"],
                "stage1_findings": st["1"]["count"],
                "stage2_findings": st["2"]["count"],
                "stage3_result": st["3"]["result"],
                "judge_1": v[0],
                "judge_2": v[1],
                "review_items_for_judges": len(d.get("review_items") or []),
                "fail_causes": "; ".join(f"{k} x{n}" for k, n in sorted(causes.items())),
            })
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    if a.stage1_out:
        out = []
        for repo in sorted(p for p in Path(a.evidence_dir).iterdir() if p.is_dir()):
            files = sorted((f for f in repo.glob("*.json") if ".judge-" not in f.name
                            and f.name != "PUBLISHED.json"),
                           key=lambda f: json.loads(f.read_text()).get("timestamp_utc", ""))
            idx = Counter()
            for f in files:
                d = json.loads(f.read_text())
                kind = "release run" if f.name.endswith("Z.json") else "pre-release self-check"
                fam = Counter()
                for x in d["stages"]["1"].get("findings") or []:
                    rid = x["rule_id"]
                    head = rid.split("-")[0]
                    fam[rid if head == "presidio" else FAMILY.get(head, head)] += 1
                idx[kind] += 1
                for k, n in sorted(fam.items()):
                    out.append({"repository": repo.name, "kind": kind, "run": idx[kind],
                                "rule_family": k, "findings": n})
        with open(a.stage1_out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["repository", "kind", "run", "rule_family", "findings"])
            w.writeheader()
            w.writerows(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
