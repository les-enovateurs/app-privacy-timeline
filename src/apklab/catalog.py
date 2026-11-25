"""Catalogue de signatures Exodus : noms de trackers, identifiant de version."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

CATALOG_RELPATH = "public/data/compare/trackers.json"


def load_names(site_repo: Path) -> dict[int, str]:
    """id de tracker -> nom, depuis le catalogue courant du site (432 entrées)."""
    entries = json.loads((site_repo / CATALOG_RELPATH).read_text("utf-8"))
    return {int(e["id"]): e["name"] for e in entries}


def catalog_version(site_repo: Path, date: str) -> str:
    """Identifiant de version de catalogue, format AAAA-MM-JJ/<nb>/sha256:<court>.

    Non utilisé par le backfill git — pour lequel le catalogue en vigueur au moment du
    scan Exodus est irrécupérable, donc null — mais posé ici pour analyze et
    backfill-exodus, qui en auront besoin sans que le format ait à être réinventé.
    """
    raw = (site_repo / CATALOG_RELPATH).read_bytes()
    entries = json.loads(raw.decode("utf-8"))
    return f"{date}/{len(entries)}/sha256:{hashlib.sha256(raw).hexdigest()[:12]}"


def name_trackers(ids: Iterable[int], names: dict[int, str]) -> list[dict]:
    """Ids bruts d'une fiche -> [{id, name}] trié et dédoublonné.

    Le nom vient du catalogue *courant*, pas de celui du scan — d'où catalog_version à
    null sur ces snapshots. Un id absent du catalogue donne un nom nul plutôt qu'une
    erreur : l'id reste la clé de jointure, le nom n'est qu'un confort de lecture.
    """
    return [{"id": i, "name": names.get(i)} for i in sorted({int(i) for i in ids})]
