"""Reconstruction de snapshots exodus-web depuis les rapports publiés. Pur.

Entre du HTML déjà téléchargé, sort des snapshots. Aucune requête ici : le réseau est
dans exodusweb, comme le git est dans gitlog et non dans backfill_git.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .catalog import name_trackers
from .corpus import CorpusEntry
from .models import Snapshot

MONTHS_FR = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12,
}

# Étiquette de source du binaire chez Exodus -> valeur d'acquisition du schéma. Une
# étiquette inconnue donne null plutôt qu'une valeur inventée : le schéma n'accepte que
# trois valeurs et un poids venu d'une source non identifiée n'est comparable à rien.
ACQUISITION_BY_STORE = {
    "google play": "google-play",
    "google": "google-play",
    "apkpure": "apkpure",
}

# La liste : <a href="/fr/reports/775051/" class="stretched-link report-link">. La classe
# report-link est explicite et stable, contrairement au .link.black qui a rendu le
# scraper du site fragile.
_ANCHOR = re.compile(r'href="/fr/reports/(\d+)/"[^>]*\bclass="[^"]*\breport-link\b')
_VERSION_LINE = re.compile(r'<div class="small"><b>\s*Version\s+(.*?)\s*</b>\s*</div>')
_NEXT_PAGE = re.compile(r'href="\?page=(\d+)"\s*>\s*Suivant\s*<')

# Le détail : le modal « détails techniques » porte <li><b>Label&nbsp;:</b> <samp>valeur
# </samp></li>. C'est la seule partie de la page qui donne le handle, le code de version
# et la somme de contrôle de l'APK sans dépendre d'une tournure de phrase.
_MODAL_ROW = re.compile(r"<li><b>(.*?)(?:&nbsp;)?\s*:</b>\s*<samp>(.*?)</samp></li>")
_CREATED = re.compile(r"Rapport créé le\s+(\d{1,2}\s+\S+\s+\d{4}\s+\d{2}:\d{2})")
_UPDATED = re.compile(r"mis à jour le\s+(\d{1,2}\s+\S+\s+\d{4}\s+\d{2}:\d{2})")
_STORE = re.compile(r"Source(?:&nbsp;)?\s*:\s*(.*?)<br>", re.DOTALL)
_TRACKER_ID = re.compile(r"/fr/trackers/(\d+)/")
_PERMISSION = re.compile(r'<span [^>]*title="([^"]+)"')
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ParseError(Exception):
    """Le HTML ne porte pas ce qu'un rapport doit porter."""


@dataclass
class ReportRef:
    """Une ligne de la liste des rapports : ce qu'on sait avant d'ouvrir le détail."""
    report_id: int
    version_name: str | None
    store: str | None


@dataclass
class Report:
    report_id: int
    handle: str | None
    version_name: str | None
    version_code: int | None
    apk_sha256: str | None
    created_at: str | None
    updated_at: str | None
    store: str | None
    permissions: list[str]
    tracker_ids: list[int]


@dataclass
class Issue:
    ref: str
    reason: str
    detail: str


def parse_french_datetime(text: str) -> str | None:
    """« 31 août 2026 07:31 » -> « 2026-08-31T07:31:00+00:00 ».

    L'heure affichée est prise pour de l'UTC. C'est exactement l'hypothèse que fait déjà
    update-exodus.py côté site, et c'est elle qui compte : les report_date issus de git
    et ceux issus du web doivent être comparables entre eux. Un décalage éventuel d'une
    ou deux heures est sans effet sur des fenêtres d'incertitude qui font des semaines.
    """
    match = re.match(r"(\d{1,2})\s+(\S+)\s+(\d{4})\s+(\d{2}):(\d{2})", text.strip())
    if not match:
        return None
    day, month_fr, year, hour, minute = match.groups()
    month = MONTHS_FR.get(month_fr.lower())
    if not month:
        return None
    return datetime(int(year), month, int(day), int(hour), int(minute),
                    tzinfo=timezone.utc).isoformat()


def parse_list(html: str) -> tuple[list[ReportRef], int | None]:
    """Une page de /fr/reports/search/<handle>/ -> ses rapports et la page suivante.

    Le découpage se fait par ancre : le bloc d'un rapport va de son lien au lien
    suivant. Une regex unique reliant le lien à sa ligne de version sauterait dans le
    bloc d'après dès qu'un rapport n'affiche pas de version, et attribuerait à un
    rapport la version d'un autre.
    """
    refs: list[ReportRef] = []
    anchors = list(_ANCHOR.finditer(html))
    for n, anchor in enumerate(anchors):
        end = anchors[n + 1].start() if n + 1 < len(anchors) else len(html)
        block = html[anchor.end():end]
        version = store = None
        line = _VERSION_LINE.search(block)
        if line:
            version, store = _split_version(line.group(1))
        refs.append(ReportRef(int(anchor.group(1)), version, store))

    next_page = _NEXT_PAGE.search(html)
    return refs, int(next_page.group(1)) if next_page else None


def _split_version(text: str) -> tuple[str | None, str | None]:
    """« 577.0.0.50.72 - google » -> (« 577.0.0.50.72 », « google »).

    Le suffixe est le magasin d'où Exodus a tiré le binaire. Il n'est découpé que s'il
    ne contient pas d'espace : sans cette garde, une version du type « 2.1 - beta 3 »
    perdrait sa fin.
    """
    head, sep, tail = text.rpartition(" - ")
    if sep and head and " " not in tail:
        return head.strip() or None, tail.strip().lower() or None
    return text.strip() or None, None


def dedupe_by_version(refs: list[ReportRef]) -> tuple[list[ReportRef], list[Issue]]:
    """Un rapport par version distincte, le plus ancien de sa version.

    Exodus re-scanne souvent une même version — 576.0.0.42.73 apparaît quatre fois sur
    la première page de Facebook. Sans ce filtre on multiplie par trois ou quatre le
    nombre de requêtes de détail pour zéro information nouvelle.

    Le plus ancien est retenu parce que c'est lui qui date le mieux l'apparition de la
    version : un re-scan de 2024 sur une version de 2019 placerait le point six ans trop
    tard dans la série. Les ids Exodus sont croissants dans le temps, le plus petit id
    est donc le premier scan.

    Un rapport sans version lisible n'est pas dédoublonnable et passe tel quel : c'est un
    point de mesure réel, la dédup du store le rattrapera si son empreinte est déjà vue.
    """
    kept: dict[str, ReportRef] = {}
    unversioned: list[ReportRef] = []
    issues: list[Issue] = []

    for ref in refs:
        if ref.version_name is None:
            unversioned.append(ref)
            continue
        seen = kept.get(ref.version_name)
        if seen is None:
            kept[ref.version_name] = ref
            continue
        older, dropped = ((ref, seen) if ref.report_id < seen.report_id
                          else (seen, ref))
        kept[ref.version_name] = older
        issues.append(Issue(str(dropped.report_id), "duplicate_version",
                            f"version {dropped.version_name} déjà couverte par le "
                            f"rapport {older.report_id}"))

    ordered = sorted([*kept.values(), *unversioned], key=lambda r: r.report_id)
    return ordered, issues


def parse_report(html: str, report_id: int) -> Report:
    """Une page /fr/reports/<id>/ -> les faits qu'elle porte.

    Les permissions et les pisteurs sont lus dans leur section, délimitée par les ancres
    #trackers et #permissions. Un balayage de toute la page ramènerait les liens de
    pisteur du bandeau de navigation et les infobulles de niveau de protection des
    icônes de permission.
    """
    fields = {_clean(k): _clean(v) for k, v in _MODAL_ROW.findall(html)}
    created = _CREATED.search(html)
    updated = _UPDATED.search(html)
    store_label = _STORE.search(html)

    trackers_at = html.find('id="trackers"')
    permissions_at = html.find('id="permissions"')
    if permissions_at < 0:
        raise ParseError(f"rapport {report_id} : pas de section permissions")

    tracker_ids = sorted({int(i) for i in
                          _TRACKER_ID.findall(html[max(trackers_at, 0):permissions_at])})
    permissions = sorted(set(_PERMISSION.findall(html[permissions_at:])))

    checksum = fields.get("Somme de contrôle")
    version_code = fields.get("Code de version")
    store_name = _clean(store_label.group(1)) if store_label else None

    return Report(
        report_id=report_id,
        handle=fields.get("Identifiant"),
        version_name=fields.get("Version"),
        version_code=int(version_code) if version_code and version_code.isdigit() else None,
        apk_sha256=checksum if checksum and _SHA256.match(checksum) else None,
        created_at=parse_french_datetime(created.group(1)) if created else None,
        updated_at=parse_french_datetime(updated.group(1)) if updated else None,
        store=store_name.lower() if store_name else None,
        permissions=permissions,
        tracker_ids=tracker_ids,
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("&nbsp;", " ")).strip()


def snapshot_for(entry: CorpusEntry,
                 report: Report,
                 tracker_names: dict[int, str],
                 ref: ReportRef | None = None) -> tuple[Snapshot | None, Issue | None]:
    """Un rapport -> un snapshot exodus-web, ou un rejet consigné.

    Deux rejets. Un handle différent de celui du corpus : la page de recherche ne devrait
    jamais renvoyer un autre paquet, mais c'est le même garde-fou qu'au backfill git, où
    il a écarté 94 révisions. Une date de création illisible : sans elle le snapshot n'a
    pas de place dans la série, et un point mal daté fabrique des fenêtres fausses.

    catalog_version reste null. La page publie ses pisteurs, pas la version du catalogue
    qui les a détectés, et pour un rapport de 2019 ce catalogue n'existe plus. Les deltas
    de pisteurs de cette source sortiront donc catalog_unknown, comme ceux du backfill
    git ; ses deltas de permissions, eux, sont exploitables.

    Le nom d'application vient du corpus : le titre de la page peut renvoyer une
    référence de ressource non résolue du type @7F1400C5, ou « Open in app ».
    """
    if report.handle and report.handle != entry.handle:
        return None, Issue(str(report.report_id), "handle_mismatch",
                           f"{report.handle!r} au lieu de {entry.handle!r}")
    if not report.created_at:
        return None, Issue(str(report.report_id), "missing_report_date",
                           "date de création illisible")

    version_name = report.version_name or (ref.version_name if ref else None)
    flags: list[str] = []
    if not version_name:
        flags.append("missing_version_name")
    if not report.tracker_ids:
        flags.append("empty_trackers")
    if report.updated_at:
        # Rapport re-scanné après coup : ses pisteurs viennent du catalogue du jour de la
        # mise à jour, pas de celui de la création. L'écart de pisteurs avec le point
        # précédent peut donc venir du catalogue seul.
        flags.append("report_updated")
    if ref and ref.version_name and report.version_name \
            and ref.version_name != report.version_name:
        flags.append("version_mismatch")

    store = report.store or (ref.store if ref else None)

    return Snapshot(
        handle=entry.handle,
        slug=entry.slug,
        app_name=entry.app_name,
        version_name=version_name,
        version_code=report.version_code,
        source="exodus-web",
        acquisition=ACQUISITION_BY_STORE.get(store or ""),
        apk_sha256=report.apk_sha256,
        analyzer="exodus",
        catalog_version=None,
        observed_at=report.created_at,
        report_date=report.created_at,
        report_updated_at=report.updated_at,
        permissions=report.permissions,
        trackers=name_trackers(report.tracker_ids, tracker_names),
        quality_flags=flags,
        provenance_ref=str(report.report_id),
    ), None
