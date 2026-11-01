#!/usr/bin/env python3
"""Positive-control run: which planted credential/PII shapes does each
off-the-shelf detector report when run on its own?

Builds a throw-away fixture of synthetic (fake, non-working) samples, one per
file, then runs each available detector separately and records, per sample,
whether that detector reported anything in that file.

Detectors (each optional; a missing tool is recorded as "not_run", never as 0):
  * gitleaks   (`gitleaks dir`, default rules)
  * trufflehog (`trufflehog filesystem --no-verification`, no network calls)
  * publish_gate_stages_1_2 (optional, --gate-dir): stages 1-2 of the
               publish-gate tool with a two-line fixture denylist; no LLM judge;
               "review" = not a finding, but queued for the stage-3 judges
  * presidio   (Presidio Analyzer, spaCy en_core_web_sm, default recognizers;
                needs `presidio-analyzer` + `en_core_web_sm` importable)

Usage:
    python3 detector_control.py --out ../data/detector_control.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import string
import subprocess
import tempfile
from pathlib import Path

RNG = random.Random(7)  # fixed seed: the fixture is identical on every run
AN = string.ascii_letters + string.digits


def rnd(n: int, alphabet: str = AN) -> str:
    return "".join(RNG.choice(alphabet) for _ in range(n))


# (sample id, form group, text). Values are synthetic, random and non-functional,
# but follow the published length/charset of each format so that entropy- and
# format-based rules get a fair chance.
SAMPLES = [
    ("openai-project-key", "key shape", "sk-" + "proj-" + rnd(74) + "T3Blbk" + "FJ" + rnd(74)),
    ("anthropic-key", "key shape", "sk-" + "ant-api03-" + rnd(93, AN + "_-") + "AA"),
    ("github-fine-grained-pat", "key shape", "github_" + "pat_" + rnd(22) + "_" + rnd(59)),
    ("github-classic-pat", "key shape", "gh" + "p_" + rnd(36)),
    ("telegram-bot-token", "key shape", "bot_token = " + rnd(9, string.digits) + ":" + "AA" + rnd(33, AN + "_-")),
    ("bearer-header", "key shape", "Authorization: Bear" + "er " + rnd(40)),
    ("url-userinfo", "key shape", "https://" + "demo:" + rnd(16) + "@" + "db.example.invalid/"),
    ("bcrypt-hash", "key shape", "$2" + "y$12$" + rnd(53, AN + "./")),
    ("aws-access-key-id", "key shape", "AK" + "IA" + rnd(16, string.ascii_uppercase + "234567")),
    ("basic-auth-header", "key shape", "Authorization: Ba" + "sic " + rnd(28)),
    ("private-key-block", "key shape", "-----BE" + "GIN PRIVATE KEY-----\n" + "\n".join(rnd(64, AN + "+/") for _ in range(6)) + "\n-----END PRIVATE KEY-----"),
    ("slack-bot-token", "key shape", "xo" + "xb-" + rnd(12, string.digits) + "-" + rnd(13, string.digits) + "-" + rnd(24)),
    ("generic-sk-key", "key shape", "api" + "_key = s" + "k-" + rnd(40)),
    ("prose-label-value", "credential in prose", "The pass" + "word for the demo account is " + rnd(12) + "."),
    ("html-label-value", "credential in prose", "<dt>Pass" + "word</dt><dd>" + rnd(12) + "</dd>"),
    ("capability-url", "credential in prose", "Invite link: https://example.invalid/join?token=" + rnd(40)),
    ("email-address", "personal data", "Contact: someone" + "@" + "example.com"),
    ("phone-number", "personal data", "Call 202-555-" + "0142 after 6pm."),
    ("private-ipv4", "personal data", "Server at " + ".".join(["192", "168", "20", "30"])),
    ("person-name-en", "personal data", "Prepared by Jonathan Example of Springfield."),
    ("person-name-ja", "personal data", "担当: 架空" + "太郎"),
    ("address-ja", "personal data", "住所: 架空県" + "架空市1-2-3"),
]


def run_gitleaks(tree: Path, work: Path) -> set[str] | None:
    if not shutil.which("gitleaks"):
        return None
    rep = work / "gitleaks.json"
    subprocess.run(["gitleaks", "dir", str(tree), "--no-banner", "--report-format", "json",
                    "--report-path", str(rep), "--exit-code", "0"],
                   capture_output=True, check=False)
    data = json.loads(rep.read_text() or "[]")
    return {Path(x["File"]).stem for x in data}


def run_trufflehog(tree: Path) -> set[str] | None:
    if not shutil.which("trufflehog"):
        return None
    p = subprocess.run(["trufflehog", "filesystem", str(tree), "--json", "--no-verification",
                        "--no-update"], capture_output=True, text=True, check=False)
    hits = set()
    for line in p.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        f = (((d.get("SourceMetadata") or {}).get("Data") or {}).get("Filesystem") or {}).get("file")
        if f:
            hits.add(Path(f).stem)
    return hits


NOT_PII = {"URL", "DATE_TIME", "NRP"}


def run_presidio(tree: Path) -> set[str] | None:
    try:
        from presidio_analyzer import AnalyzerEngine
    except ImportError:
        return None
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    nlp = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy", "models": [
        {"lang_code": "en", "model_name": "en_core_web_sm"}]}).create_engine()
    eng = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
    hits = set()
    for f in tree.iterdir():
        found = eng.analyze(text=f.read_text(), language="en")
        # URL, DATE_TIME and NRP are not personal data; count the rest.
        if any(r.entity_type not in NOT_PII for r in found):
            hits.add(f.stem)
    return hits


def run_gate(tree: Path, work: Path, gate_dir: str | None) -> set[str] | None:
    """Stages 1-2 of the publish-gate tool (pattern + PII + denylist), no LLM judges."""
    if not gate_dir:
        return None
    import sys
    sys.path.insert(0, gate_dir)
    from publish_gate import analyzer_load, deny_rules, scan
    deny = work / "deny.txt"
    deny.write_text("person\t" + "架空" + "太郎" + "\naddress\t" + "架空県" + "\n")
    review: list[dict] = []
    first, second, _ = scan(tree, deny_rules(deny), analyzer_load(), review_items=review)
    hits = {Path(f["file"]).stem for f in first + second}
    queued = {Path(f["file"]).stem for f in review} - hits
    return hits, queued


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--gate-dir", help="directory containing publish_gate.py (optional)")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="detector-control-") as td:
        work = Path(td)
        tree = work / "tree"
        tree.mkdir()
        for sid, _, text in SAMPLES:
            (tree / f"{sid}.txt").write_text("Fixture line.\n" + text + "\n")
        results = {
            "gitleaks": run_gitleaks(tree, work),
            "trufflehog": run_trufflehog(tree),
            "presidio": run_presidio(tree),
            "publish_gate_stages_1_2": run_gate(tree, work, a.gate_dir),
        }
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample", "form_group"] + list(results))
        for sid, grp, _ in SAMPLES:
            row = []
            for r in results.values():
                if r is None:
                    row.append("not_run")
                elif isinstance(r, tuple):  # (hard findings, queued for judge review)
                    row.append(1 if sid in r[0] else ("review" if sid in r[1] else 0))
                else:
                    row.append(int(sid in r))
            w.writerow([sid, grp] + row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
