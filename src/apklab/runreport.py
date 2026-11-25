"""Rapport de run. Aucune panne silencieuse : chaque app a un statut."""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path


def new(command: str, args: dict) -> dict:
    return {
        "command": command,
        "args": args,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "host": platform.node(),
        "apps": [],
        "totals": {},
    }


def add_app(report: dict, slug: str, handle: str, status: str, **extra) -> None:
    """status : ok | no_revision | no_snapshot | error."""
    report["apps"].append({"slug": slug, "handle": handle, "status": status, **extra})


def finish(report: dict, totals: dict, runs_dir: Path, write: bool) -> Path | None:
    report["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["totals"] = totals
    if not write:
        return None
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["started_at"].replace(":", "").replace("-", "")
    path = runs_dir / f"{stamp}.json"
    # Deux runs partis dans la même seconde — un `run --app-list` qui ne trouve rien à
    # faire dure moins d'une seconde — ne doivent pas se recouvrir : un rapport écrasé
    # est exactement la panne silencieuse que le projet s'interdit.
    n = 1
    while path.exists():
        path = runs_dir / f"{stamp}-{n}.json"
        n += 1
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return path


def summary(totals: dict) -> str:
    return (f"{totals['apps']} app(s) | "
            f"{totals['snapshots']} snapshot(s) | "
            f"{totals['written']} écrit(s) | "
            f"{totals['skipped']} ignoré(s) | "
            f"{totals['errors']} erreur(s)")
