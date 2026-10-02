# Baltic media monitor

How do Russian/Belarusian outlets cover Lithuania, Latvia and Estonia compared with the Baltics' neighbours,
other foreign outlets and the Baltic press itself, and can we watch it live? A research prototype for
*Cloud Computing and Big Data* (VilniusTech, FMITM24301).

**Data:** [GDELT GKG 2.1](https://www.gdeltproject.org/). Every 15 minutes it publishes metadata (themes, tone,
places, names, headline) for every article it indexed in that window, about 300k articles a day (Sept 2026: 9.08M in 30 days) in two
feeds: English, and ~65 languages machine-translated into English. Only metadata is used; no article text
is downloaded or stored.

**Hypotheses** (`src/baltic/analysis/hypotheses.py`):
- **H1:** ru_by outlets give the Baltics a larger share of their output than the neighbours and other foreign press.
- **H2a:** on non-security stories, ru_by tone is lower.
- **H2b:** on security stories, ru_by tone is the same as everyone else's (±0.5 tone points).
- **H3:** multilingual embeddings find "the same story" more precisely than shared names do, at equal coverage.

Each is decided by a rule fixed before the tests ran, with domain-clustered bootstrap CIs. Disclosed: an
exploratory 30-day pass (per-group counts and overall tone, no security split, no CIs) came before the rules.

## Architecture: Lambda, on one VM plus one serverless model

```
GDELT ──► producer ──► Kafka (1 topic, 1 partition) ──► monitor ──► slots.jsonl, alerts.jsonl   speed layer
  │          └─► raw/ zips (after Kafka acknowledged)        ▲ warm start (seed)
  └─► mirror ──► raw/ ──► Spark: map → reduceByKey → SQL ──► lake/ silver + gold      batch layer
                                         lake/ ──► match ──► Cloud Run embedder (MiniLM, private)
                     lake/, monitor/, ai/, bench/ ──► report ──► summary.json + figures   serving layer
```

On Google Cloud: one Compute Engine VM runs the Docker Compose stack, the model runs on private Cloud Run,
and a private bucket mirrors the data tree. Everything is created by `infra/up.sh` and deleted by
`infra/down.sh` (only `ccbd-*` resources; the shared project is never changed).

## Where everything lives

| Code | What it is |
|---|---|
| `src/baltic/article.py` | one GKG row → one `Article` (group, about the Baltics?, security?, tone): pure |
| `src/baltic/gdelt.py` | GDELT as a source: 15-minute slot timestamps, index, download, zip rows |
| `src/baltic/layout.py` | **the file-system schema**: every path below is built here and nowhere else |
| `src/baltic/detector.py`, `stats.py` | spike detector (median/MAD); cluster bootstrap and Wilson intervals |
| `src/baltic/stream/` | `producer` (GDELT → Kafka), `monitor` (close rule → detector → files), `seed` |
| `src/baltic/batch/` | `mirror` (backfill), `mapper` (the map step), `etl` (Spark), `baseline` + `bench` (study) |
| `src/baltic/ai/` | `match` (embedding service vs shared names), `evaluate` (blind labels, H3) |
| `src/baltic/analysis/` | `hypotheses` (H1, H2), `checks` (reconcile, stream = batch), `report` (figures) |
| `src/baltic/__main__.py` | the one command line: `baltic <command>`, one thin adapter per command |
| `ai/embedder/` | the model service (FastAPI + sentence-transformers), its own image |
| `infra/` | `up.sh` (create), `vm.sh` (the VM's boot script), `down.sh` (delete), `crash-test.sh` |
| `tests/` | unit tests per module with fakes for GDELT and Kafka, `red_check.py` (mutations) |

| Data (`data/`, mirrored to `gs://<bucket>/`) | Written by |
|---|---|
| `raw/feed={en,tr}/dt=YYYYMMDD/TS.zip` (+ `TS.missing`) | producer, mirror |
| `lake/silver/articles/dt=…/`, `lake/gold/{domain_day,theme_group,series_15m}/` | etl |
| `lake-live/…` (the same tables over the live window) | etl --live |
| `monitor/{slots,alerts}.jsonl`, `monitor/state.pkl` | monitor, seed |
| `ai/{pairs.parquet,labels_*.csv,evaluation.json}` | match, sample, evaluate |
| `bench/TAG-rN.json` | bench |
| `report/{summary.json,*.png,stream_vs_batch.json}` | report, stream-vs-batch |

## Coursework requirements → evidence

| Req. | Evidence | Command → pass looks like |
|---|---|---|
| 1.1 problem, research | H1–H3 with fixed rules | `just report` → `summary.json` decides each |
| 1.2 cloud options | report §1.2 (VM vs Dataproc vs BigQuery; Kafka vs Pub/Sub; Cloud Run vs VM) | — |
| 1.3 architecture, security | diagram above, `infra/up.sh` (private bucket, 2 least-privilege identities, IAP-only SSH, private Cloud Run) | — |
| 2.1 big-data tech | Kafka (KRaft, manual commits, resume from the committed offset) + Spark (map/reduce, SQL, Parquet) | `just up`, `just etl` |
| 2.2 ingest, transform, analyse | Spark = independent collector; stream = batch | `just reconcile` → N/N days; `just stream-vs-batch` → `ok` |
| 2.3 performance, scalability | E1 data size, E2 cores, E3 same map in a process pool; Kafka lag; crash recovery | `just bench` → `bench/*.json`; `just crash-test` |
| 3.1 containers, cloud | one image for every step, Compose, VM + Cloud Run + bucket | `infra/up.sh`; `gs://…/logs/ccbd.log` |
| 3.2 demo, evaluation | blind labels: matcher precision per stratum, detector vs control hours | `just sample` → label → `just evaluate` |
| 3.3 summary, future work | report §3.3 | — |

## Run it

```bash
just ci                                     # gates: lint, strict types, tests + coverage, mutations
just test-image                             # the tests inside the image (Java: the Spark test runs too)
SINCE=20261001000000 just up                # Kafka + producer
just mirror 2026-09-01 30 && just etl 2026-09-01 30 && just reconcile
just seed && just monitor                   # warm-started speed layer
just embedder && just match && just sample  # AI; then fill in data/ai/labels_*.csv
just evaluate && just report                # H3, figures, summary.json
```

On Google Cloud the VM runs the same recipes itself on every boot (`infra/vm.sh`); the operator runs
`PROJECT=… INVOKER=user:… bash infra/up.sh` once, and `bash infra/down.sh` after grading.

## Ethics and data protection

Public metadata only; no article text stored; no access to blocked sites attempted; a private bucket and
a private model endpoint; every resource is deleted after grading.
