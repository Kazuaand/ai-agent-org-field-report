# AI agent organization — field report

[日本語](README.ja.md)

A short technical report with aggregate measurements from one operator running day-to-day work through an organization of AI coding agents (a lead session per task, delegating to subagents and checking their results), October 2026.

- **Report:** [report.md](report.md) (English) · [report.ja.md](report.ja.md) (Japanese)
- **Topics:** prompt-cache rewrite after idle gaps · token and estimated-cost composition, lead vs. subagents · secret/PII detector positive controls and a four-stage pre-publication gate
- **Data:** aggregated CSV tables in [`data/`](data/) — no per-session, per-day or identifying rows
- **Figures:** [`figures/`](figures/), regenerated from `data/` by `scripts/make_figures.py`
- **Scripts:** [`scripts/`](scripts/) — run them on your own logs to get your own numbers (see the report's "Reproducing" section)

## Key numbers (October 2026, one operator, one workstation)

| | |
|---|---|
| Median cache write, request after the cache expired | 55,961 tokens (n = 434) |
| Median cache write, request while the cache was warm | 1,268 tokens (n = 83,236) |
| Share of main-session cache-write tokens from cold requests | 12.1% |
| Subagent share of requests / of estimated standard-rate cost | 71% / 59% |
| Synthetic samples reported by none of gitleaks, TruffleHog, Presidio (run alone) | 7 of 22 |

Costs are estimates at standard list prices, given as shares only; no billing data is used.

## Citing

See [CITATION.cff](CITATION.cff). A DOI is assigned on release.

## License

Text, data and figures: [CC BY 4.0](LICENSE). Scripts in `scripts/`: [MIT](scripts/LICENSE).
