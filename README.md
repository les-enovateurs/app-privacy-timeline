# app-privacy-timeline

Static analysis of Android apps, tracked over time. For each APK: permissions, tracker
SDKs, size broken down by dex / resources / assets / native code, network endpoints and
SDK account identifiers. Every measurement is a dated snapshot tied to the `sha256` of
the binary it came from, so a change between two versions becomes a delta you can read.

Built for [Unlock My Data](https://unlock-my-data.com), a French non-profit project that
explains in plain language what apps do with personal data. The figures published on
the site come from this tool.

**No APK is stored or redistributed.** You bring the binary; the tool keeps only the
facts derived from it.

## Install

```bash
uv sync --locked
uv run pytest -q          # no network, no external binary
```

Python 3.11+, [uv](https://docs.astral.sh/uv/). Analysis relies on
[androguard](https://github.com/androguard/androguard).

## Analyse an APK

```bash
uv run python scripts/demo-analyze.py              # offline demo on the test fixture
uv run apklab signatures fetch --write             # tracker signatures, from Exodus Privacy
uv run apklab analyze ~/Downloads/app.xapk --slug <slug> --compare
```

`analyze` reads an `.apk` or an `.xapk` (splits are merged before measuring) obtained by
any means you are entitled to use: your own device (`adb pull`), F-Droid, the publisher's
site. `--compare` checks the result against the Exodus Privacy report of the **same
version**: identical permissions mean the analyser agrees with Exodus; a divergence means
the analyser is wrong.

Without a signature catalogue the command refuses to run: an empty tracker list would
read as "no trackers" when it means "not looked for".

## Two rules for every command

- **Nothing is written without `--write`.** Without it, the result goes to stdout.
- **Scope is always explicit**: `--app <slug>` or `--all`.

The corpus of followed apps is built from the public site repository
([les-enovateurs/unlock-my-data](https://github.com/les-enovateurs/unlock-my-data)),
expected in `../reboot` and overridable with `--site-repo <path>`:

```bash
uv run apklab corpus seed --write
```

## Commands

| Command | What it does | Network |
|---|---|---|
| `corpus seed` / `list` | builds `corpus.json` from the site's `public/data/compare/*.json` | no |
| `backfill-git` | snapshots from the site's git history | no |
| `backfill-exodus` | snapshots from reports published by Exodus Privacy | read-only |
| `store ingest` | appends JSONL snapshots read from stdin | no |
| `diff` | deltas between consecutive snapshots | no |
| `analyze` | static analysis of an APK or XAPK you provide | no |
| `signatures fetch` | freezes the tracker signature catalogue | read-only |
| `candidates` | packages unknown to the catalogue, ranked by prevalence | no |
| `endpoints` | hosts found in binaries, third-party endpoints by prevalence | no |
| `keys` | SDK account identifiers found in binaries | no |
| `project` | writes the site's `compare/*.json` from the store | no |
| `export` / `publish` | derived dataset, one document per app | no |
| `show` | prints a series and its deltas, never writes | no |
| `validate` | checks snapshots against `schema/snapshot.schema.json` | no |

Commands chain: `uv run apklab backfill-git --app carrefour | uv run apklab store ingest --write`.

CLI output is in French: the people running it are.

## SDK keys

A tracker tells *which* SDK is embedded; an account identifier tells *which account*
at that SDK, which is what a GDPR request needs. Secrets (API keys, tokens, DSNs) are
never stored in full: only a six-character prefix and a twelve-character sha256, enough
to prove presence and see rotation between versions, not enough to use the key.

## Contributing

Conventions are in `CONTRIBUTING.md`: English code, short comments that say *why*, one test
per behaviour, external binaries and network injected and never touched by the suite.

## Licence

MIT. Tracker signatures fetched by `signatures fetch` come from
[Exodus Privacy](https://exodus-privacy.eu.org) and remain under their terms.
