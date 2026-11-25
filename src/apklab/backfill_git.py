"""Reconstruction de snapshots exodus-git depuis l'historique du site. Pur."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from .catalog import name_trackers
from .corpus import CorpusEntry
from .models import Snapshot


@dataclass
class RevisionBlob:
    sha: str
    authored_at: str
    path: str
    raw: bytes


@dataclass
class Issue:
    sha: str
    reason: str
    detail: str


def snapshots_for(entry: CorpusEntry,
                  revs: Iterable[RevisionBlob],
                  tracker_names: dict[int, str]) -> tuple[list[Snapshot], list[Issue]]:
    """Snapshots exodus-git d'une application, dans l'ordre chronologique.

    Quatre rejets, tous consignés — aucune panne silencieuse :

    - JSON illisible ;
    - blob qui n'est pas un objet : d'anciennes révisions de ces chemins portaient une
      liste ;
    - handle différent de celui du corpus. C'est le garde-fou important. --follow
      détecte les renommages par similarité de contenu, et deux fiches Exodus ont une
      structure quasi identique : la première révision de carrefour.json est en réalité
      fnac.json. Sans ce rejet, la série de Carrefour démarrerait sur les permissions de
      la Fnac et le premier diff serait une pure fabrication. 94 révisions sur l'ensemble
      du corpus sont dans ce cas ;
    - point de mesure déjà vu, à (version, report_date) égal : le site recommite des
      fiches inchangées.

    catalog_version reste null : le catalogue en vigueur au scan Exodus n'est pas
    récupérable depuis git. Conséquence assumée — toutes les deltas de trackers issues
    de ce backfill sortiront marquées catalog_unknown, seules leurs deltas de
    permissions sont exploitables.
    """
    snaps: list[Snapshot] = []
    issues: list[Issue] = []
    seen: set[tuple] = set()

    for rev in revs:
        try:
            doc = json.loads(rev.raw)
        except json.JSONDecodeError as exc:
            issues.append(Issue(rev.sha, "invalid_json", str(exc)))
            continue
        if not isinstance(doc, dict):
            issues.append(Issue(rev.sha, "not_an_object", type(doc).__name__))
            continue
        if doc.get("handle") != entry.handle:
            issues.append(Issue(rev.sha, "handle_mismatch",
                                f"{doc.get('handle')!r} au lieu de {entry.handle!r}"))
            continue

        key = (doc.get("version"), doc.get("report_date"))
        if key in seen:
            issues.append(Issue(rev.sha, "duplicate_point", repr(key)))
            continue
        seen.add(key)

        flags: list[str] = []
        if not doc.get("version"):
            flags.append("missing_version_name")
        if not doc.get("report_date"):
            flags.append("missing_report_date")
        if not doc.get("trackers"):
            flags.append("empty_trackers")

        snaps.append(Snapshot(
            handle=entry.handle,
            slug=entry.slug,
            app_name=entry.app_name,
            version_name=doc.get("version"),
            source="exodus-git",
            analyzer="exodus",
            catalog_version=None,
            observed_at=rev.authored_at,
            previous_observed_at=snaps[-1].observed_at if snaps else None,
            report_date=doc.get("report_date"),
            permissions=sorted(doc.get("permissions") or []),
            trackers=name_trackers(doc.get("trackers") or [], tracker_names),
            quality_flags=flags,
            provenance_ref=rev.sha,
        ))

    return snaps, issues
