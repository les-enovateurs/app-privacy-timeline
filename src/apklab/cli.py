"""apklab — CLI. Aucune commande n'écrit sans --write."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from . import (analyze, backfill_exodus, backfill_git, candidates, catalog, corpus,
               diff, endpoints, evidence, exodusweb, export, gitlog, keys, project,
               publish, render, runreport, signatures, store, xapk)
from .models import Snapshot

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "corpus.json"
SNAPSHOT_ROOT = ROOT / "snapshots"
DELTA_ROOT = ROOT / "deltas"
RUNS_ROOT = ROOT / "runs"
CACHE_ROOT = ROOT / "cache"
SIGNATURES_PATH = ROOT / "signatures" / "trackers.json"
ALLOWLIST_PATH = ROOT / "signatures" / "allowlist.txt"
HOST_CLASSES_PATH = ROOT / "signatures" / "host-classes.txt"
CORRECTIONS_PATH = ROOT / "signatures" / "corrections.json"
EVIDENCE_ROOT = ROOT / "evidence"
CANDIDATES_PATH = ROOT / "candidates.json"
ENDPOINTS_PATH = ROOT / "endpoints.json"
KEYS_PATH = ROOT / "keys.json"
SCHEMA_PATH = ROOT / "schema" / "snapshot.schema.json"
DIST_ROOT = ROOT / "dist"
PUBLIC_ROOT = ROOT / "dist-public"
DEFAULT_SITE_REPO = Path("../reboot")


def _err(msg: str) -> int:
    print(f"erreur : {msg}", file=sys.stderr)
    return 1


def _select(entries, app: str | None, all_: bool):
    """--app <slug> ou --all. Jamais tout le corpus par défaut."""
    if app:
        found = corpus.by_slug(entries).get(app)
        if not found:
            raise SystemExit(_err(f"slug inconnu dans le corpus : {app}"))
        return [found]
    if all_:
        return sorted(entries, key=lambda e: e.slug)
    raise SystemExit(_err("préciser --app <slug> ou --all"))


def _scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--app", help="limiter à un slug")
    parser.add_argument("--all", action="store_true", help="tout le corpus")
    parser.add_argument("--write", action="store_true", help="écrire sur le disque")



def cmd_corpus_seed(args) -> int:
    entries, issues = corpus.seed(args.site_repo)
    if CORPUS_PATH.exists():
        entries = corpus.merge(entries, corpus.load(CORPUS_PATH))
    for issue in issues:
        print(f"  ! {issue['slug']} : {issue['reason']} — {issue['detail']}", file=sys.stderr)
    print(f"{len(entries)} application(s), {len(issues)} anomalie(s)")
    if args.write:
        corpus.write(entries, CORPUS_PATH)
        print(f"écrit : {CORPUS_PATH}")
    else:
        print("dry-run — relancer avec --write pour écrire corpus.json")
    return 0


def cmd_corpus_list(args) -> int:
    for entry in sorted(corpus.load(CORPUS_PATH), key=lambda e: e.slug):
        alias = f"  (alias : {', '.join(entry.aliases)})" if entry.aliases else ""
        print(f"{entry.slug:<32} {entry.handle}{alias}")
    return 0


# --------------------------------------------------------------- backfill-git

def cmd_backfill_git(args) -> int:
    entries = _select(corpus.load(CORPUS_PATH), args.app, args.all)
    names = catalog.load_names(args.site_repo)
    report = runreport.new("backfill-git", {"app": args.app, "all": args.all,
                                            "write": args.write,
                                            "site_repo": str(args.site_repo),
                                            "site_head": gitlog.head(args.site_repo)})
    totals = {"apps": 0, "snapshots": 0, "written": 0, "skipped": 0, "errors": 0}

    for entry in entries:
        totals["apps"] += 1
        try:
            revs = [rev for relpath in entry.compare_relpaths
                    for rev in gitlog.revisions(args.site_repo, relpath)]
        except gitlog.GitError as exc:
            totals["errors"] += 1
            runreport.add_app(report, entry.slug, entry.handle, "error", detail=str(exc))
            continue
        # Le store suppose des insertions chronologiques ; deux fichiers fusionnés
        # arrivent forcément entrelacés.
        revs.sort(key=lambda rev: rev.authored_at)
        if not revs:
            runreport.add_app(report, entry.slug, entry.handle, "no_revision")
            continue

        blobs = []
        for rev in revs:
            try:
                raw = gitlog.blob(args.site_repo, rev.sha, rev.path)
            except gitlog.GitError as exc:
                totals["errors"] += 1
                runreport.add_app(report, entry.slug, entry.handle, "error",
                                  sha=rev.sha, detail=str(exc))
                continue
            blobs.append(backfill_git.RevisionBlob(rev.sha, rev.authored_at, rev.path, raw))

        snaps, issues = backfill_git.snapshots_for(entry, blobs, names)
        totals["snapshots"] += len(snaps)
        totals["skipped"] += len(issues)

        for snap in snaps:
            print(json.dumps(snap.to_dict(), ensure_ascii=False, sort_keys=True))

        stats = {}
        if args.write and snaps:
            stats = store.ingest(SNAPSHOT_ROOT, snaps, write=True)
            totals["written"] += stats.get("appended", 0)

        runreport.add_app(report, entry.slug, entry.handle,
                          "ok" if snaps else "no_snapshot",
                          revisions=len(revs), snapshots=len(snaps),
                          rejected=[{"sha": i.sha, "reason": i.reason, "detail": i.detail}
                                    for i in issues],
                          store=stats)

    path = runreport.finish(report, totals, RUNS_ROOT, args.write)
    print(runreport.summary(totals), file=sys.stderr)
    if path:
        print(f"rapport : {path}", file=sys.stderr)
    else:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    return 0


# ------------------------------------------------------------ backfill-exodus

# Garde-fou de pagination. Facebook, l'application la plus scannée du corpus, tient en
# 12 pages ; 100 pages signifierait que la pagination boucle et non qu'une application
# a 2500 rapports.
MAX_LIST_PAGES = 100


def _report_refs(client, handle: str, max_pages: int) -> list:
    """Toutes les lignes de rapport d'une application, page après page.

    On suit le lien « Suivant » plutôt que de compter jusqu'au numéro de la dernière
    page : le nombre de pages change entre deux requêtes dès qu'Exodus publie un scan.
    Un numéro de page qui ne progresse pas arrête la boucle — une pagination qui
    revient sur elle-même ferait tourner le run sans fin.
    """
    limit = max_pages or MAX_LIST_PAGES
    refs, page, pages = [], 1, 0
    while page and pages < limit:
        page_refs, next_page = backfill_exodus.parse_list(client.list_page(handle, page))
        refs.extend(page_refs)
        pages += 1
        page = next_page if next_page and next_page > page else None
    return refs


def cmd_backfill_exodus(args) -> int:
    entries = _select(corpus.load(CORPUS_PATH), args.app, args.all)
    names = catalog.load_names(args.site_repo)
    client = exodusweb.Client(CACHE_ROOT / "exodus", delay=args.delay)
    report = runreport.new("backfill-exodus", {"app": args.app, "all": args.all,
                                               "write": args.write,
                                               "delay": args.delay,
                                               "max_pages": args.max_pages,
                                               "max_versions": args.max_versions})
    totals = {"apps": 0, "snapshots": 0, "written": 0, "skipped": 0, "errors": 0}

    for entry in entries:
        totals["apps"] += 1
        try:
            refs = _report_refs(client, entry.handle, args.max_pages)
        except exodusweb.HttpError as exc:
            totals["errors"] += 1
            runreport.add_app(report, entry.slug, entry.handle, "error", detail=str(exc))
            continue

        if not refs:
            runreport.add_app(report, entry.slug, entry.handle, "no_report")
            continue

        refs, issues = backfill_exodus.dedupe_by_version(refs)
        if args.max_versions:
            # Les plus récentes d'abord : un run volontairement partiel doit ramener le
            # présent, le passé se complète au run suivant sans rien re-télécharger.
            refs = sorted(refs, key=lambda r: r.report_id, reverse=True)[:args.max_versions]
            refs.sort(key=lambda r: r.report_id)

        # Un rapport déjà dans la série n'est pas redemandé, même absent du cache : le
        # backfill se relance sur une autre machine sans repayer 300 requêtes.
        known = {row.get("provenance_ref") for row in store.read(SNAPSHOT_ROOT, entry.handle)
                 if row.get("source") == "exodus-web"}
        todo = [ref for ref in refs if str(ref.report_id) not in known]

        snaps = []
        for ref in todo:
            try:
                html = client.report(ref.report_id)
            except exodusweb.NotFound as exc:
                issues.append(backfill_exodus.Issue(str(ref.report_id), "not_found", str(exc)))
                continue
            except exodusweb.HttpError as exc:
                totals["errors"] += 1
                issues.append(backfill_exodus.Issue(str(ref.report_id), "http_error", str(exc)))
                continue
            try:
                parsed = backfill_exodus.parse_report(html, ref.report_id)
            except backfill_exodus.ParseError as exc:
                issues.append(backfill_exodus.Issue(str(ref.report_id), "parse_error", str(exc)))
                continue
            snap, issue = backfill_exodus.snapshot_for(entry, parsed, names, ref)
            if issue:
                issues.append(issue)
                continue
            snaps.append(snap)

        snaps.sort(key=lambda s: s.observed_at)
        totals["snapshots"] += len(snaps)
        totals["skipped"] += len(issues)

        for snap in snaps:
            print(json.dumps(snap.to_dict(), ensure_ascii=False, sort_keys=True))

        stats = {}
        if args.write and snaps:
            stats = store.ingest(SNAPSHOT_ROOT, snaps, write=True)
            totals["written"] += stats.get("appended", 0)

        runreport.add_app(report, entry.slug, entry.handle,
                          "ok" if snaps else "no_snapshot",
                          reports=len(refs), fetched=len(todo), snapshots=len(snaps),
                          rejected=[{"ref": i.ref, "reason": i.reason, "detail": i.detail}
                                    for i in issues],
                          store=stats)

    path = runreport.finish(report, {**totals, "http": client.stats}, RUNS_ROOT, args.write)
    print(runreport.summary(totals), file=sys.stderr)
    print(f"réseau : {client.stats['requests']} requête(s), "
          f"{client.stats['cached']} depuis le cache, "
          f"{client.stats['retries']} réessai(s)", file=sys.stderr)
    if path:
        print(f"rapport : {path}", file=sys.stderr)
    else:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    return 0


# ----------------------------------------------------------------------- store

def cmd_store_ingest(args) -> int:
    stream = sys.stdin if args.input == "-" else open(args.input, encoding="utf-8")
    snaps = [Snapshot.from_dict(json.loads(line)) for line in stream if line.strip()]
    stats = store.ingest(SNAPSHOT_ROOT, snaps, write=args.write)
    print(json.dumps(stats, ensure_ascii=False), file=sys.stderr)
    if not args.write:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    return 0


# ------------------------------------------------------------------------ diff

def cmd_diff(args) -> int:
    entries = _select(corpus.load(CORPUS_PATH), args.app, args.all)
    total = 0
    for entry in entries:
        deltas = diff.deltas_for(SNAPSHOT_ROOT, entry.handle)
        total += len(deltas)
        for delta in deltas:
            print(json.dumps(delta, ensure_ascii=False, sort_keys=True))
        if args.write:
            _write_deltas(entry.handle, deltas)
    print(f"{len(entries)} app(s) | {total} delta(s)", file=sys.stderr)
    if not args.write:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    return 0


def _write_deltas(handle: str, deltas: list[dict]) -> None:
    """Le fichier de deltas d'un handle, réécrit en entier.

    Réécrit et non complété : un snapshot inséré au milieu de la série change les
    fenêtres des deltas qui l'entourent, et un fichier complété par la fin garderait
    les anciennes à côté des nouvelles.
    """
    if not deltas:
        return
    DELTA_ROOT.mkdir(parents=True, exist_ok=True)
    (DELTA_ROOT / f"{handle}.json").write_text(
        json.dumps(deltas, ensure_ascii=False, indent=2) + "\n", "utf-8")


# --------------------------------------------------------------------- analyze

def cmd_analyze(args) -> int:
    """Analyse d'un APK déposé à la main. Point de contrôle du projet.

    Aucune automatisation ne se construit sur un analyseur non validé : --compare
    confronte le résultat au snapshot Exodus de la *même version*, quand il existe. Une
    divergence de permissions signifie que l'analyseur est faux, et on l'apprend avant
    d'avoir écrit une ligne de téléchargement.
    """
    entry = _select(corpus.load(CORPUS_PATH), args.slug, False)[0]
    apk_path = Path(args.apk)
    if not apk_path.is_file():
        return _err(f"fichier introuvable : {apk_path}")

    catalogue = _signatures(args.without_signatures)
    if catalogue is None:
        return _err(_SANS_CATALOGUE.format(path=SIGNATURES_PATH))
    trackers, catalog_version = catalogue

    work = CACHE_ROOT / "parts" / entry.handle
    try:
        apk, flags = _facts(apk_path, work, args.size_scope or "single-apk")
    except xapk.XapkError as exc:
        return _err(str(exc))
    finally:
        _rmtree(work)

    snap, refus = _snapshot(entry, apk, flags, trackers, catalog_version,
                            args.site_repo, args.acquisition)
    if refus:
        return _err(refus)

    print(json.dumps(snap.to_dict(), ensure_ascii=False, sort_keys=True))
    print(_facts_line(apk, snap), file=sys.stderr)

    if args.compare:
        _compare_to_exodus(entry, snap)

    if args.write:
        stats = store.ingest(SNAPSHOT_ROOT, [snap], write=True)
        print(json.dumps(stats, ensure_ascii=False), file=sys.stderr)
        _dit_le_refus(stats)
    else:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    _write_evidence(apk, snap, args.write)
    return 0


def _dit_le_refus(stats: dict) -> None:
    """Un point refusé par le store doit se lire, pas se déduire d'un compteur à zéro.

    Le seul motif de refus est `trackers_not_analyzed`, donc `--without-signatures` :
    l'analyse reste valable pour les poids, mais elle n'a pas sa place dans une série
    où elle se lirait comme un retrait de tous les pisteurs.
    """
    if stats.get("refused"):
        print(f"  · {stats['refused']} point(s) non versé(s) à la série : pisteurs non "
              "analysés, la liste vide se lirait comme un retrait", file=sys.stderr)


_SANS_CATALOGUE = ("catalogue de signatures absent : {path}\n"
                   "  le récupérer avec `apklab signatures fetch --write`, ou passer\n"
                   "  --without-signatures pour produire un snapshot sans pisteurs")


def _signatures(without: bool) -> tuple[list, str | None] | None:
    """Le catalogue, ou rien du tout si son absence n'a pas été assumée.

    Sans catalogue, une liste de pisteurs vide se lit « aucun pisteur » alors qu'elle
    veut dire « pas cherché ». Les deux commandes qui produisent un snapshot
    `apk-static` — `analyze` et `fetch` — refusent donc de tourner par défaut.
    """
    if SIGNATURES_PATH.exists():
        chargé = signatures.load(SIGNATURES_PATH, CORRECTIONS_PATH)
        if signatures.LAST_ORPHANS:
            print(f"  ! correction(s) sans pisteur au catalogue, ignorée(s) : "
                  f"{signatures.LAST_ORPHANS} — les retirer de {CORRECTIONS_PATH.name}",
                  file=sys.stderr)
        return chargé
    return ([], None) if without else None


def _facts(path: Path, work: Path, size_scope: str) -> tuple[analyze.ApkFacts, list[str]]:
    """Faits d'un binaire, XAPK fusionné ou APK unique.

    Le périmètre de poids n'est pas demandé pour un XAPK, il est déduit : ses parts sont
    là, on les fusionne, le périmètre est `merged-xapk`. `--size-scope` ne sert qu'à
    déclarer le périmètre d'un APK unique, que rien dans le fichier ne permet de
    connaître — un `base.apk` extrait à la main d'un bundle est indistinguable d'un APK
    monolithique.
    """
    if xapk.is_xapk(path):
        return analyze.facts_xapk(path, work)
    return analyze.facts(path, size_scope=size_scope), []


def _snapshot(entry, apk, flags: list[str], trackers: list, catalog_version: str | None,
              site_repo: Path, acquisition: str) -> tuple[Snapshot | None, str | None]:
    """Détection des pisteurs puis snapshot `apk-static`, ou un refus motivé."""
    detection = signatures.detect(apk.class_names, trackers)
    for tracker_id, detail in detection.broken:
        print(f"  ! signature illisible, pisteur {tracker_id} : {detail}", file=sys.stderr)

    snap, refus = analyze.snapshot_for(
        entry, apk, detection.tracker_ids, catalog.load_names(site_repo),
        catalog_version, _now(), acquisition=acquisition)
    if refus:
        return None, refus

    marques = set(snap.quality_flags) | set(flags)
    if not trackers:
        # « Pas cherché » et « aucun trouvé » se contredisent, et les poser ensemble
        # laissait un lecteur choisir la mauvaise lecture. Seul le premier est vrai.
        marques.add("trackers_not_analyzed")
        marques.discard("empty_trackers")
    snap.quality_flags = sorted(marques)
    return snap, None


def _write_evidence(apk: analyze.ApkFacts, snap: Snapshot, write: bool) -> None:
    """Les preuves du binaire, à côté du snapshot et jamais dedans.

    Écrites sous le même `--write` que le snapshot et seulement avec lui : une preuve
    sans point de série décrirait une version dont rien ne dit qu'on l'a retenue, et
    `candidates` compterait la prévalence d'un SDK sur des analyses écartées.

    L'écriture est indexée par version : réanalyser la même version réécrit le même
    fichier, une nouvelle version en ajoute un. C'est `evidence.load_all` qui décide
    ensuite de ne garder que la plus récente par application — la plus récente **en
    version**, ce qui autorise à remonter un vieux binaire sans qu'il se fasse passer
    pour l'état courant de l'application.
    """
    if not write:
        return
    doc = evidence.build(handle=snap.handle, version_name=snap.version_name,
                         apk_sha256=snap.apk_sha256, observed_at=snap.observed_at,
                         analyzer_version=snap.analyzer_version or "",
                         class_names=apk.class_names, domains=apk.domains,
                         credentials=apk.credentials)
    path = evidence.write(EVIDENCE_ROOT, doc)
    print(f"  · preuves : {_court(path)} — {len(doc['packages'])} package(s), "
          f"{len(doc['domains'])} domaine(s), "
          f"{len(doc['credentials'])} identifiant(s) de compte", file=sys.stderr)
    for found in doc["credentials"]:
        shown = found.get("value") or f"{found['value_prefix']}… ({found['value_sha256']})"
        print(f"    · {found['label']} : {shown}", file=sys.stderr)


def _court(path: Path) -> str:
    """Un chemin relatif au dépôt quand il en fait partie, absolu sinon.

    `relative_to` lève sur un chemin extérieur — un répertoire temporaire de test, un
    `--out` ailleurs — et un message d'avancement n'a pas le droit de faire échouer la
    commande qu'il décrit.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _facts_line(apk: analyze.ApkFacts, snap: Snapshot) -> str:
    return (f"{apk.handle} {apk.version_name} — {len(snap.permissions)} permission(s), "
            f"{len(snap.trackers)} pisteur(s), {apk.total_bytes} o "
            f"({apk.size_scope}), {len(apk.class_names)} classes, "
            f"natif {apk.native_bytes} o {apk.native_abis or '—'}")


def _rmtree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def _reference(rows: list[dict], snap: Snapshot) -> tuple[dict, str]:
    """Le snapshot Exodus auquel se comparer, et ce que vaut son identité de binaire.

    Trois rangs, du plus solide au plus faible, et le rang retenu est rendu avec la
    référence : une comparaison dont on ignore la solidité ne vaut rien, et c'est
    exactement ce qui a failli faire condamner l'analyseur sur Grindr le 2026-09-09.

    À rang égal on prend le plus récent : deux rapports Exodus d'une même version
    décrivent le même binaire, et le second a vu le catalogue le plus proche du nôtre.
    """
    if snap.version_code:
        same_build = [row for row in rows if row.get("version_code") == snap.version_code]
        if same_build:
            return same_build[-1], (f"version_code {snap.version_code} des deux côtés — "
                                    "même build, comparaison solide")
    web = [row for row in rows if row["source"] == "exodus-web"]
    if web:
        return web[-1], (f"version_name {snap.version_name} des deux côtés, version_code "
                         "absent du rapport — même build probable, non garanti")
    return rows[-1], (f"version_name {snap.version_name} seulement, snapshot issu du git "
                      "du site — un écart de pisteurs peut venir d'un autre build")


def _compare_to_exodus(entry, snap) -> None:
    """Confronte le snapshot produit au rapport Exodus du même binaire.

    **Le même `version_name` ne garantit pas le même binaire**, et s'y fier a failli
    faire condamner l'analyseur. Mesuré le 2026-09-09 sur Grindr : la référence choisie
    était un snapshot `exodus-git`, qui ne porte ni `version_code` ni `apk_sha256` — sa
    liste de pisteurs venait d'un autre build que celui qu'on venait de télécharger, et
    l'écart de 12 pisteurs se lisait comme une faute de détection alors qu'il n'y en
    avait pas.

    D'où l'ordre de préférence : un `version_code` identique d'abord — c'est la seule
    identité de build dont on dispose des deux côtés — puis `exodus-web`, seule source à
    le porter, et `exodus-git` en dernier recours. La base retenue est annoncée, parce
    qu'une comparaison dont on ignore la solidité ne vaut rien.
    """
    même_version = [row for row in store.read(SNAPSHOT_ROOT, entry.handle)
                    if row["source"].startswith("exodus")
                    and row["version_name"] == snap.version_name]
    if not même_version:
        print(f"\ncomparaison impossible : aucun snapshot Exodus en version "
              f"{snap.version_name}", file=sys.stderr)
        return

    reference, base = _reference(même_version, snap)
    delta = diff.build_delta(reference, snap.to_dict())
    print(f"\nidentité du binaire : {base}", file=sys.stderr)
    print(f"\ncomparaison avec {reference['source']} "
          f"({reference.get('provenance_ref')}) en version {snap.version_name} :")
    for line in render.delta(delta):
        print(line)
    verdict = ("identiques" if not delta["permissions"]["added"]
               and not delta["permissions"]["removed"] else "DIVERGENTES")
    print(f"  permissions {verdict} — c'est le critère : elles sont lues du manifeste "
          f"des deux côtés.")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------- project

def cmd_project(args) -> int:
    """Dérive les fiches `compare/*.json` du site depuis le store.

    Cette commande écrit **chez quelqu'un d'autre**, et chez celui-là même dont on a lu
    l'histoire pour construire le store. Trois conséquences dans le code.

    1. **Elle n'écrit que dans un checkout local et ne commit jamais.** La validation
       promise par le design est un `git diff` lu par un humain ; la commande finit donc
       en le rappelant, et rien d'autre ne part vers le site.
    2. **Une fiche n'est pas réécrite, elle est complétée.** Seuls les quatre champs que
       le dépôt possède sont remplacés ; l'ordre des clés et tous les autres champs du
       site survivent. Voir `project.OWNED`.
    3. **Un fichier identique octet pour octet n'est pas touché.** C'est ce qui rend la
       projection relançable et, surtout, ce qui rend la revue possible : le diff ne
       montre que les applications où un fait a bougé.

    La propriété qui valide l'étage : le store vient en majorité du git du site, donc
    projeter doit rendre ces fiches-là **inchangées**. Toute fiche qui bouge est soit une
    correction réelle, soit un défaut de la projection — et dans les deux cas ça se lit.
    """
    entries = _select(corpus.load(CORPUS_PATH), args.app, args.all)
    out = args.out or (args.site_repo / corpus.COMPARE_RELDIR)
    if not out.is_dir():
        return _err(f"répertoire de sortie introuvable : {out}")

    report = runreport.new("project", {"app": args.app, "all": args.all,
                                       "write": args.write, "out": str(out),
                                       "force": args.force})
    totals = {"apps": 0, "files": 0, "changed": 0, "written": 0, "unchanged": 0,
              "no_snapshot": 0, "trackers_not_analyzed": 0, "site_ahead": 0,
              "held_removals": 0, "errors": 0}

    for entry in entries:
        totals["apps"] += 1
        série = sorted(store.read(SNAPSHOT_ROOT, entry.handle), key=diff._sort_key)
        fields, refus = project.projection(série)
        if refus:
            totals[refus] += 1
            runreport.add_app(report, entry.slug, entry.handle, refus,
                              detail=_MOTIFS[refus])
            continue

        fichiers = []
        for slug in [entry.slug, *entry.aliases]:
            # Deux slugs peuvent porter le même paquet — alibaba et aliexpress. Le store
            # est clé par handle, donc une seule série alimente les deux fiches ; n'en
            # écrire qu'une laisserait l'autre dériver sans que rien ne le signale.
            totals["files"] += 1
            fichiers.append(_project_one(out / f"{slug}.json", entry, fields, args,
                                         totals, série))

        runreport.add_app(report, entry.slug, entry.handle,
                          "changed" if any(f["status"] == "changed" for f in fichiers)
                          else "unchanged",
                          version=fields["version"], files=fichiers)

    path = runreport.finish(report, totals, RUNS_ROOT, args.write)
    print(f"{totals['apps']} app(s) | {totals['files']} fiche(s) | "
          f"{totals['changed']} à changer | {totals['unchanged']} inchangée(s) | "
          f"{totals['no_snapshot']} sans snapshot | "
          f"{totals['trackers_not_analyzed']} sans analyse de pisteurs | "
          f"{totals['site_ahead']} site en avance | "
          f"{totals['held_removals']} retrait(s) de pisteur retenu(s) | "
          f"{totals['errors']} erreur(s)",
          file=sys.stderr)
    if args.write:
        print(f"écrit : {totals['written']} fiche(s) dans {out}", file=sys.stderr)
    else:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    if path:
        print(f"rapport : {path}", file=sys.stderr)
    # La projection ne commit pas et ne poussera jamais : c'est ici que la validation
    # humaine promise par le design commence.
    print(f"\nrevue avant tout commit :\n  git -C {args.site_repo} diff --stat -- "
          f"{corpus.COMPARE_RELDIR}", file=sys.stderr)
    return 0


_MOTIFS = {
    "no_snapshot": "aucun snapshot dans le store",
    "trackers_not_analyzed": ("le dernier point n'a pas cherché de pisteurs — publier sa "
                              "liste vide écrirait « aucun pisteur » sur la fiche"),
}


def _project_one(path: Path, entry, fields: dict, args, totals: dict,
                 série: list[dict]) -> dict:
    """Une fiche : lecture, fusion, comparaison octet à octet, écriture éventuelle."""
    ligne = {"file": path.name, "status": "changed"}
    try:
        existing = json.loads(path.read_text("utf-8")) if path.exists() else None
    except json.JSONDecodeError as exc:
        totals["errors"] += 1
        return {**ligne, "status": "error", "detail": f"fiche illisible : {exc}"}

    fields, retenus = project.hold_removals(existing, fields, série)
    if retenus:
        totals["held_removals"] += len(retenus)
        print(f"  · {path.name} : {len(retenus)} pisteur(s) gardé(s) sur la fiche — "
              f"leur dernière présence connue n'a pas été mesurée avec le catalogue "
              f"d'aujourd'hui, leur absence n'est donc pas comparable {retenus}",
              file=sys.stderr)

    if project.site_ahead(existing, fields) and not args.force:
        totals["site_ahead"] += 1
        return {**ligne, "status": "site_ahead",
                "detail": (f"la fiche porte {existing['report_date']}, plus récent que "
                           f"{fields['report_date']} — le site a été mis à jour depuis "
                           f"notre dernier backfill")}

    texte = project.dumps(project.apply_to(existing, entry.app_name, entry.handle, fields))
    if existing is not None and texte == path.read_text("utf-8"):
        totals["unchanged"] += 1
        return {**ligne, "status": "unchanged"}

    totals["changed"] += 1
    mouvements = project.changes(existing, fields)
    print(json.dumps({"file": path.name, "handle": entry.handle,
                      "created": existing is None, **mouvements},
                     ensure_ascii=False, sort_keys=True))
    if args.write:
        path.write_text(texte, "utf-8")
        totals["written"] += 1
    return {**ligne, "changes": mouvements, "created": existing is None}


# ---------------------------------------------------------------------- export

_SOURCES_MANQUANTES = (
    "document(s) de corpus absent(s) : {noms}. Le dataset est une jointure, pas une "
    "mesure : sans eux les tranches par application seraient vides sans que rien ne le "
    "dise. Lancer d'abord `apklab endpoints --write`, `apklab keys --write`, "
    "`apklab candidates --write`."
)


def _dans_le_depot(path: Path) -> bool:
    """Le chemin de sortie est-il sous le dépôt privé ?

    `relative_to` lève sur un chemin extérieur, ce qui est exactement la question posée.
    """
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def cmd_export(args) -> int:
    """Assemble le dataset dérivé : un document par application, plus un manifeste.

    Ce que produit cette commande n'existe nulle part ailleurs dans le dépôt. Le store
    est clé par handle et ne connaît ni les hôtes ni les comptes SDK ; les trois
    documents de corpus traversent les applications et ne savent pas raconter une seule.
    L'export fait la jointure, et c'est tout ce qu'il fait — aucun chiffre n'y est
    recalculé, chacun se retrouve à l'identique dans le document dont il vient.

    Deux gardes, dans cet ordre.

    1. **La sortie reste dans le dépôt privé.** Décision 12. `../reboot` est un dépôt
       public : y écrire ne serait pas une projection mais une publication, et ce n'est
       pas à cette commande de la décider. `--allow-outside` existe pour le jour où ce
       choix sera fait, explicitement, sous-ensemble par sous-ensemble.
    2. **Un document de corpus absent arrête la commande.** Une tranche vide se lirait
       comme « cette application ne parle à personne », ce qui est faux et indétectable.

    Un document identique octet pour octet n'est pas réécrit : le `git diff` du dépôt ne
    montre que les applications où un fait a bougé, comme pour `project`.
    """
    entries = _select(corpus.load(CORPUS_PATH), args.app, args.all)
    out = args.out or DIST_ROOT

    if not _dans_le_depot(out) and not args.allow_outside:
        return _err(f"sortie hors du dépôt privé : {out}. Le dataset n'est pas publié "
                    "(décision 12) — relancer avec --allow-outside pour l'écrire "
                    "quand même, en sachant où il part.")

    manquants = [p.name for p in (ENDPOINTS_PATH, KEYS_PATH, CANDIDATES_PATH)
                 if not p.exists()]
    if manquants:
        return _err(_SOURCES_MANQUANTES.format(noms=", ".join(manquants)))

    hosts = json.loads(ENDPOINTS_PATH.read_text("utf-8")).get("hosts") or []
    accounts = json.loads(KEYS_PATH.read_text("utf-8")).get("accounts") or []
    cands = json.loads(CANDIDATES_PATH.read_text("utf-8")).get("candidates") or []

    report = runreport.new("export", {"app": args.app, "all": args.all,
                                      "write": args.write, "out": str(out),
                                      "allow_outside": args.allow_outside})
    totals = {"apps": 0, "files": 0, "written": 0, "unchanged": 0, "no_snapshot": 0,
              "errors": 0}
    index: list[dict] = []
    skipped: list[dict] = []

    for entry in entries:
        totals["apps"] += 1
        série = sorted(store.read(SNAPSHOT_ROOT, entry.handle), key=diff._sort_key)
        if not série:
            totals["no_snapshot"] += 1
            skipped.append({"slug": entry.slug, "handle": entry.handle,
                            "reason": "no_snapshot"})
            runreport.add_app(report, entry.slug, entry.handle, "no_snapshot",
                              detail=_MOTIFS["no_snapshot"])
            continue

        doc = export.app_document(
            entry, série, diff.deltas_for(SNAPSHOT_ROOT, entry.handle),
            hosts=export.slice_for(entry.handle, hosts),
            accounts=export.slice_for(entry.handle, accounts),
            candidates=export.slice_for(entry.handle, cands))

        relpath = f"{export.APPS_RELDIR}/{entry.slug}.json"
        index.append(export.index_entry(doc, relpath))
        totals["files"] += 1
        statut = _export_one(out / relpath, doc, args, totals)
        runreport.add_app(report, entry.slug, entry.handle, statut,
                          version=(doc["latest"] or {}).get("version_name"),
                          counts=doc["counts"])

    doc = export.manifest(generated_at=_now(),
                          sources=[export.digest(p) for p in
                                   (ENDPOINTS_PATH, KEYS_PATH, CANDIDATES_PATH)],
                          apps=index, skipped=skipped)
    print(export.dumps(doc), end="")

    if args.write:
        (out / export.MANIFEST_NAME).parent.mkdir(parents=True, exist_ok=True)
        (out / export.MANIFEST_NAME).write_text(export.dumps(doc), "utf-8")

    print(f"{totals['apps']} app(s) | {totals['files']} document(s) | "
          f"{totals['written']} écrit(s) | {totals['unchanged']} inchangé(s) | "
          f"{totals['no_snapshot']} sans snapshot | {totals['errors']} erreur(s)",
          file=sys.stderr)
    print("  " + ", ".join(f"{n} {k}" for k, n in doc["totals"].items()),
          file=sys.stderr)
    if args.write:
        print(f"écrit : {_court(out)} — dataset privé, décision 12", file=sys.stderr)
    else:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    path = runreport.finish(report, totals, RUNS_ROOT, args.write)
    if path:
        print(f"rapport : {path}", file=sys.stderr)
    return 0


def _export_one(path: Path, doc: dict, args, totals: dict) -> str:
    """Un document : comparaison octet à octet, écriture éventuelle."""
    texte = export.dumps(doc)
    if path.exists() and path.read_text("utf-8") == texte:
        totals["unchanged"] += 1
        return "unchanged"
    if args.write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(texte, "utf-8")
        totals["written"] += 1
    return "changed"


# --------------------------------------------------------------------- publish

def _depot_git_tiers(path: Path) -> Path | None:
    """Le dépôt git qui contiendrait cette sortie, s'il en existe un autre que celui-ci.

    `publish` a vocation à écrire hors du dépôt privé — c'est la différence avec
    `export`, et la garde ne peut donc pas être la même. Ce qu'il faut empêcher a changé
    de nature : non plus la sortie du dataset, mais son entrée dans un dépôt versionné.
    Écrire dans `../reboot` mettrait la projection sous git, où elle serait committée
    puis diffusée à chaque passe, alors que le site la reconstruit à chaque build depuis
    le dépôt de fichiers du serveur. Un répertoire nu, hors git, est la seule cible.
    """
    for parent in [path.resolve(), *path.resolve().parents]:
        if (parent / ".git").exists() and parent != ROOT.resolve():
            return parent
    return None


def cmd_publish(args) -> int:
    """Écrit la projection publique du dataset : la réduction, et le manifeste qui l'indexe.

    Ce que cette commande retire est documenté dans `publish.py`, champ par champ. Ce
    qu'elle ajoute est l'empreinte de chaque document dans le manifeste : la projection
    traverse un réseau et se fait recopier par un script qui ne l'a pas produite, là où
    le dataset privé ne quitte jamais le disque qui l'a écrit.

    Elle dérive de `export`, pas du store : la jointure se fait une fois. Un chiffre qui
    divergerait entre la fiche du site et cette projection serait alors un bug visible,
    et non deux vérités pour un seul fait.

    Rien d'irréversible ici — la sortie est un répertoire de travail. Ce qui engage est
    le geste suivant, la dépose sur le serveur, et il reste manuel.
    """
    entries = _select(corpus.load(CORPUS_PATH), args.app, args.all)
    out = args.out or PUBLIC_ROOT

    tiers = _depot_git_tiers(out)
    if tiers:
        return _err(f"sortie sous un dépôt git tiers : {tiers}. La projection publique "
                    "n'est pas versionnée — le site la reconstruit à chaque build depuis "
                    "le dépôt de fichiers du serveur. Viser un répertoire hors git.")

    manquants = [p.name for p in (ENDPOINTS_PATH, KEYS_PATH, CANDIDATES_PATH)
                 if not p.exists()]
    if manquants:
        return _err(_SOURCES_MANQUANTES.format(noms=", ".join(manquants)))

    hosts = json.loads(ENDPOINTS_PATH.read_text("utf-8")).get("hosts") or []
    accounts = json.loads(KEYS_PATH.read_text("utf-8")).get("accounts") or []
    cands = json.loads(CANDIDATES_PATH.read_text("utf-8")).get("candidates") or []

    totals = {"apps": 0, "files": 0, "written": 0, "unchanged": 0, "no_snapshot": 0,
              "empty": 0}
    index: list[dict] = []
    catalog_version = None

    for entry in entries:
        totals["apps"] += 1
        série = sorted(store.read(SNAPSHOT_ROOT, entry.handle), key=diff._sort_key)
        if not série:
            totals["no_snapshot"] += 1
            continue

        privé = export.app_document(
            entry, série, diff.deltas_for(SNAPSHOT_ROOT, entry.handle),
            hosts=export.slice_for(entry.handle, hosts),
            accounts=export.slice_for(entry.handle, accounts),
            candidates=export.slice_for(entry.handle, cands))
        doc = publish.public_document(privé)
        catalog_version = série[-1].get("catalog_version") or catalog_version

        # Une application dont il ne reste rien après réduction n'est pas publiée. Le
        # cas est fréquent et normal : 113 des 130 applications du corpus n'ont qu'un
        # rapport Exodus derrière elles, dont le site porte déjà tout le contenu dans sa
        # fiche. Publier un document vide ferait croire à une analyse qui n'existe pas.
        if not doc["static"] and not doc["deltas"] and not doc["endpoints"]["hosts"]:
            totals["empty"] += 1
            continue

        texte = publish.dumps(doc)
        relpath = f"{publish.APPS_RELDIR}/{entry.slug}.json"
        index.append(publish.index_entry(doc, relpath, texte))
        totals["files"] += 1
        cible = out / relpath
        if cible.exists() and cible.read_text("utf-8") == texte:
            totals["unchanged"] += 1
        elif args.write:
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_text(texte, "utf-8")
            totals["written"] += 1

    doc = publish.manifest(generated_at=_now(), apps=index,
                           catalog_version=catalog_version)
    print(publish.dumps(doc), end="")

    if args.write:
        (out / publish.MANIFEST_NAME).parent.mkdir(parents=True, exist_ok=True)
        (out / publish.MANIFEST_NAME).write_text(publish.dumps(doc), "utf-8")

    print(f"{totals['apps']} app(s) | {totals['files']} publiable(s) | "
          f"{totals['written']} écrit(s) | {totals['unchanged']} inchangé(s) | "
          f"{totals['no_snapshot']} sans snapshot | {totals['empty']} sans rien à dire",
          file=sys.stderr)
    if args.write:
        print(f"écrit : {_court(out)} — projection publique, à déposer à la main",
              file=sys.stderr)
    else:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    return 0


# ------------------------------------------------------------------ signatures

def cmd_signatures_fetch(args) -> int:
    """Récupère le catalogue de signatures et le fige dans le dépôt.

    Il est versionné parce qu'un nombre de pisteurs ne veut rien dire sans lui : une
    analyse d'il y a six mois doit rester rejouable, et un delta de pisteurs entre deux
    catalogues différents ne dit rien sur l'application.
    """
    from datetime import date

    client = exodusweb.Client(CACHE_ROOT / "exodus", delay=args.delay)
    try:
        raw = client.get(signatures.CATALOG_URL).encode("utf-8")
    except exodusweb.HttpError as exc:
        return _err(str(exc))

    trackers = signatures.parse_catalog(json.loads(raw))
    version = signatures.catalog_version(raw, date.today().isoformat())
    print(f"{len(trackers)} pisteur(s) avec signature de code — {version}")

    if not args.write:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
        return 0
    SIGNATURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(raw)
    payload["catalog_version"] = version
    SIGNATURES_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                               "utf-8")
    print(f"écrit : {SIGNATURES_PATH}")
    return 0


# ------------------------------------------------------------------------ show

def cmd_candidates(args) -> int:
    """Les packages qu'aucun catalogue ne connaît, triés par prévalence.

    Le catalogue de signatures est obligatoire et sans échappatoire, contrairement à
    `analyze` qui offre `--without-signatures` : sans lui, les 400 pisteurs connus
    ressortiraient en candidats et la sortie serait à jeter en entier. Ce n'est pas un
    résultat dégradé, c'est du bruit pur.

    La sortie standard porte le document complet, l'erreur standard le haut du
    classement — c'est par là qu'un tri manuel commence, et il n'a pas besoin des
    échantillons de classes pour choisir quoi regarder.

    Le classement ne montre que le `pending` par défaut : un lot signé n'a plus à être
    relu, et sans ce filtre la plomberie Google occupe les douze premières lignes à
    chaque passe. Le document, lui, reste complet — un `rejected` sorti du JSON serait
    réévalué en `pending` à la passe suivante.

    **Il se replie sur l'éditeur par défaut depuis le 2026-09-14.** Stripe occupait
    202 lignes, Datadog 71, le 3DS2 d'Adyen 52 — pour une question chacun. Un trieur qui
    doit lire 202 lignes avant la question suivante ne finit pas la file. `--par package`
    rend la vue plate, qui reste la bonne pour poser un statut : le document ne connaît
    que des packages, et le repli n'est qu'un affichage.
    """
    docs = evidence.load_all(EVIDENCE_ROOT)
    if not docs:
        return _err(f"aucune preuve dans {EVIDENCE_ROOT} : lancer `apklab analyze` ou "
                    "`apklab run` avec --write d'abord")

    catalogue = _signatures(False)
    if catalogue is None:
        return _err(_SANS_CATALOGUE.format(path=SIGNATURES_PATH))
    trackers, catalog_version = catalogue

    allowlist = candidates.load_allowlist(ALLOWLIST_PATH)
    host_classes = endpoints.load_classes(HOST_CLASSES_PATH)
    found = candidates.find(docs, trackers, allowlist, min_apps=args.min_apps,
                            min_classes=args.min_classes, host_classes=host_classes)
    if args.only_endpoints:
        found = [c for c in found if c["endpoints"]]
    doc, counts = candidates.merge(candidates.load(CANDIDATES_PATH), found, _now(),
                                   catalog_version)

    print(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1))
    print(f"{len(docs)} application(s) analysée(s), {len(allowlist)} préfixe(s) bénins, "
          f"{len(trackers)} pisteur(s) au catalogue", file=sys.stderr)
    print(f"{len(doc['candidates'])} candidat(s) : "
          + ", ".join(f"{n} {k}" for k, n in counts.items() if n), file=sys.stderr)
    classement = [c for c in doc["candidates"]
                  if args.status == "all" or c["status"] == args.status]
    if args.status != "all":
        print(f"classement : {len(classement)} en {args.status}, "
              f"{len(doc['candidates']) - len(classement)} masqué(s) — `--status all` "
              "pour tout voir", file=sys.stderr)
    if args.par == "editeur":
        editeurs = candidates.par_editeur(classement)
        print(f"{len(editeurs)} éditeur(s) — `--par package` pour la vue plate",
              file=sys.stderr)
        for groupe in editeurs[:args.top]:
            hosts = ", ".join(groupe["endpoints"][:3]) or "—"
            voisins = ", ".join(groupe["catalogued_siblings"])
            paquets = len(groupe["packages"])
            print(f"  {groupe['app_count']:>3} app(s)  {groupe['racine']:<36} "
                  f"{paquets:>4} pkg  {groupe['class_count']:>6} classes  {hosts}"
                  + (f"  [voisin catalogué : {voisins}]" if voisins else ""),
                  file=sys.stderr)
    else:
        for candidate in classement[:args.top]:
            hosts = ", ".join(candidate["endpoints"][:3]) or "—"
            voisins = ", ".join(candidate.get("catalogued_siblings") or [])
            print(f"  {candidate['app_count']:>3} app(s)  {candidate['package']:<44} "
                  f"{candidate['class_count']:>5} classes  {hosts}"
                  + (f"  [voisin catalogué : {voisins}]" if voisins else ""),
                  file=sys.stderr)

    if args.write:
        CANDIDATES_PATH.write_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n", "utf-8")
        print(f"écrit : {_court(CANDIDATES_PATH)}", file=sys.stderr)
    else:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    return 0


def cmd_endpoints(args) -> int:
    """Les hôtes du corpus, classés, les plus répandus d'abord.

    Ne touche ni réseau ni binaire : tout est déjà dans les preuves écrites par
    `analyze` et `run`. Une passe coûte une seconde, ce qui est la raison pour laquelle
    la classification vit ici et non dans l'analyse — la table de classement se cure
    tous les jours, et rejouer onze APK à chaque correction serait absurde.

    Seuls les hôtes classés `endpoint` et non premier parti sont un fait publiable. Les
    autres classes restent dans le document : un tri qui jette ne peut plus être
    contesté, et c'est le contraire de ce que ce dataset doit permettre.
    """
    docs = evidence.load_all(EVIDENCE_ROOT)
    if not docs:
        return _err(f"aucune preuve dans {EVIDENCE_ROOT} : lancer `apklab analyze` ou "
                    "`apklab run` avec --write d'abord")

    table = endpoints.load_classes(HOST_CLASSES_PATH)
    hosts = endpoints.collect(docs, table)
    tiers = endpoints.third_party(hosts)
    tally = endpoints.counts(hosts)
    kept = [h for h in hosts if h["class"] == args.only] if args.only else hosts

    doc = {
        "generated_at": _now(),
        "app_count": len(docs),
        "counts": tally,
        "hosts": kept,
    }
    print(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1))

    print(f"{len(docs)} application(s), {len(hosts)} hôte(s) distinct(s), "
          f"{len(table)} classement(s) manuel(s)", file=sys.stderr)
    print("  " + ", ".join(f"{n} {k}" for k, n in tally.items() if n), file=sys.stderr)
    print(f"{len(tiers)} endpoint(s) tiers publiables", file=sys.stderr)
    for host in (kept if args.only else tiers)[:args.top]:
        owner = ", ".join(host["owner_packages"][:2]) or "—"
        print(f"  {host['app_count']:>3} app(s)  {host['host']:<46} {owner}",
              file=sys.stderr)

    if args.write:
        ENDPOINTS_PATH.write_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n", "utf-8")
        print(f"écrit : {_court(ENDPOINTS_PATH)}", file=sys.stderr)
    else:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    return 0


def cmd_keys(args) -> int:
    """Les identifiants de compte SDK du corpus, les plus répandus d'abord.

    Ne touche ni réseau ni binaire : les identifiants sont déjà tronqués dans les
    preuves écrites par `analyze` et `run`, et cette commande ne voit jamais une clé
    entière.

    Le compte d'applications est ce qui donne son sens à la sortie : un identifiant
    présent dans deux applications sans rapport n'est pas le compte d'un éditeur mais
    la valeur par défaut d'une bibliothèque.
    """
    docs = evidence.load_all(EVIDENCE_ROOT)
    if not docs:
        return _err(f"aucune preuve dans {EVIDENCE_ROOT} : lancer `apklab analyze` ou "
                    "`apklab run` avec --write d'abord")

    found = keys.collect(docs)
    if args.only_shared:
        found = [f for f in found if f["shared"]]
    doc = {"generated_at": _now(), "app_count": len(docs), "accounts": found}
    print(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1))

    partages = [f for f in found if f["shared"]]
    print(f"{len(docs)} application(s), {len(found)} identifiant(s) de compte, "
          f"{len(partages)} partagé(s) entre applications", file=sys.stderr)
    for entry in found[:args.top]:
        shown = entry.get("value") or f"{entry['value_prefix']}… ({entry['value_sha256']})"
        marque = "  ⚠ partagé — valeur par défaut d'une bibliothèque ?" if entry["shared"] else ""
        print(f"  {entry['app_count']:>3} app(s)  {entry['label']:<38} {shown}{marque}",
              file=sys.stderr)

    if args.write:
        KEYS_PATH.write_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n", "utf-8")
        print(f"écrit : {_court(KEYS_PATH)}", file=sys.stderr)
    else:
        print("dry-run — relancer avec --write pour écrire", file=sys.stderr)
    return 0


def cmd_show(args) -> int:
    """Lecture humaine de ce qui est déjà sur le disque. N'écrit rien, jamais.

    Toutes les autres commandes sortent du JSONL, seul format sur lequel on peut
    rejouer un calcul. Aucune ne permet de *voir* une série : c'est ce qui manquait pour
    valider une itération à l'œil au lieu de faire confiance aux tests.
    """
    entries = _select(corpus.load(CORPUS_PATH), args.app, args.all)

    if args.app:
        entry = entries[0]
        series = sorted(store.read(SNAPSHOT_ROOT, entry.handle), key=diff._sort_key)
        if not series:
            print(f"{entry.slug} — aucun snapshot")
            return 0
        print(render.header(series))
        print()
        for line in render.timeline(series, diff.deltas_for(SNAPSHOT_ROOT, entry.handle)):
            print(line)
        return 0

    rows = [(entry.slug,
             sorted(store.read(SNAPSHOT_ROOT, entry.handle), key=diff._sort_key),
             diff.deltas_for(SNAPSHOT_ROOT, entry.handle))
            for entry in entries]
    for line in render.overview(rows):
        print(line)
    return 0


# -------------------------------------------------------------------- validate

def cmd_validate(args) -> int:
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text("utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    lines = errors = 0
    for path in sorted(SNAPSHOT_ROOT.glob("*.jsonl")):
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            lines += 1
            for err in validator.iter_errors(json.loads(line)):
                errors += 1
                loc = "/".join(str(p) for p in err.absolute_path) or "(racine)"
                print(f"{path.name}:{n} {loc} : {err.message}", file=sys.stderr)
    print(f"{lines} snapshot(s) validé(s), {errors} erreur(s)")
    return 1 if errors else 0


# ------------------------------------------------------------------------ main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apklab", description=__doc__)
    parser.add_argument("--site-repo", type=Path, default=DEFAULT_SITE_REPO,
                        help="checkout du dépôt du site (défaut : ../reboot)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_corpus = sub.add_parser("corpus", help="corpus des applications suivies")
    corpus_sub = p_corpus.add_subparsers(dest="corpus_command", required=True)
    p_seed = corpus_sub.add_parser("seed", help="reconstruire corpus.json depuis le site")
    p_seed.add_argument("--write", action="store_true")
    p_seed.set_defaults(func=cmd_corpus_seed)
    corpus_sub.add_parser("list", help="lister le corpus").set_defaults(func=cmd_corpus_list)

    p_bg = sub.add_parser("backfill-git", help="snapshots depuis l'historique git du site")
    _scope(p_bg)
    p_bg.set_defaults(func=cmd_backfill_git)

    p_be = sub.add_parser("backfill-exodus", help="snapshots depuis les rapports Exodus")
    _scope(p_be)
    p_be.add_argument("--delay", type=float, default=exodusweb.DEFAULT_DELAY,
                      help="délai entre deux requêtes, en secondes "
                           f"(défaut : {exodusweb.DEFAULT_DELAY})")
    p_be.add_argument("--max-pages", type=int, default=0,
                      help="limiter le nombre de pages de liste lues par application")
    p_be.add_argument("--max-versions", type=int, default=0,
                      help="limiter le nombre de versions distinctes récupérées, "
                           "les plus récentes d'abord")
    p_be.set_defaults(func=cmd_backfill_exodus)

    p_store = sub.add_parser("store", help="store append-only")
    store_sub = p_store.add_subparsers(dest="store_command", required=True)
    p_ing = store_sub.add_parser("ingest", help="lire des snapshots JSONL et les ajouter")
    p_ing.add_argument("input", nargs="?", default="-")
    p_ing.add_argument("--write", action="store_true")
    p_ing.set_defaults(func=cmd_store_ingest)

    p_diff = sub.add_parser("diff", help="deltas entre snapshots consécutifs")
    _scope(p_diff)
    p_diff.set_defaults(func=cmd_diff)

    # Downloading stays in the private repository; the public one has no such module.
    if importlib.util.find_spec(".download", __package__):
        from . import download
        download.register(sub)

    p_an = sub.add_parser("analyze", help="analyser un APK ou un XAPK déposé à la main")
    p_an.add_argument("apk", help="chemin du fichier .apk ou .xapk")
    p_an.add_argument("--slug", required=True, help="slug du corpus auquel il appartient")
    p_an.add_argument("--compare", action="store_true",
                      help="confronter au snapshot Exodus de la même version")
    p_an.add_argument("--size-scope", default="single-apk",
                      choices=["single-apk", "merged-xapk", "base-only"],
                      help="périmètre auquel se rapporte le poids")
    p_an.add_argument("--acquisition", default="manual",
                      choices=["manual", "apkpure", "google-play"],
                      help="origine du binaire analysé")
    p_an.add_argument("--without-signatures", action="store_true",
                      help="analyser sans catalogue, sans chercher de pisteurs")
    p_an.add_argument("--write", action="store_true", help="écrire sur le disque")
    p_an.set_defaults(func=cmd_analyze)

    p_pj = sub.add_parser("project", help="dériver les fiches compare/*.json du site")
    _scope(p_pj)
    p_pj.add_argument("--out", type=Path,
                      help="répertoire des fiches (défaut : "
                           f"<site-repo>/{corpus.COMPARE_RELDIR})")
    p_pj.add_argument("--force", action="store_true",
                      help="écrire même si la fiche du site est plus récente")
    p_pj.set_defaults(func=cmd_project)

    p_ex = sub.add_parser("export",
                          help="dataset dérivé, un document par application")
    _scope(p_ex)
    p_ex.add_argument("--out", type=Path,
                      help=f"répertoire de sortie (défaut : {_court(DIST_ROOT)})")
    p_ex.add_argument("--allow-outside", action="store_true",
                      help="autoriser une sortie hors du dépôt privé — le dataset "
                           "n'est pas publié, décision 12")
    p_ex.set_defaults(func=cmd_export)

    p_pub = sub.add_parser("publish",
                           help="projection publique du dataset, à déposer sur le site")
    _scope(p_pub)
    p_pub.add_argument("--out", type=Path,
                       help=f"répertoire de sortie (défaut : {_court(PUBLIC_ROOT)})")
    p_pub.set_defaults(func=cmd_publish)

    p_sig = sub.add_parser("signatures", help="catalogue de signatures de pisteurs")
    sig_sub = p_sig.add_subparsers(dest="signatures_command", required=True)
    p_fetch = sig_sub.add_parser("fetch", help="récupérer le catalogue chez Exodus")
    p_fetch.add_argument("--delay", type=float, default=exodusweb.DEFAULT_DELAY)
    p_fetch.add_argument("--write", action="store_true")
    p_fetch.set_defaults(func=cmd_signatures_fetch)

    p_cd = sub.add_parser("candidates",
                          help="packages inconnus du catalogue, par prévalence")
    p_cd.add_argument("--min-apps", type=int, default=2,
                      help="ne garder que les packages présents dans N applications "
                           "(défaut : 2 — un singleton n'est pas une prévalence ; "
                           "passer 1 pour la queue complète)")
    p_cd.add_argument("--min-classes", type=int, default=candidates.MIN_CLASSES,
                      help="plancher de bruit, en classes cumulées "
                           f"(défaut : {candidates.MIN_CLASSES})")
    p_cd.add_argument("--only-endpoints", action="store_true",
                      help="ne garder que les candidats corrélés à un endpoint du "
                           "binaire — le signal le plus fort qu'un package sort sur "
                           "le réseau")
    p_cd.add_argument("--top", type=int, default=20,
                      help="nombre de candidats détaillés sur la sortie d'erreur")
    p_cd.add_argument("--par", choices=("editeur", "package"), default="editeur",
                      help="unité du classement sur la sortie d'erreur (défaut : "
                           "editeur — Stripe pose 202 sous-packages pour une seule "
                           "question ; `package` rend la vue plate, qui est celle où "
                           "un statut se pose)")
    p_cd.add_argument("--status", choices=[*candidates.STATUSES, "all"],
                      default="pending",
                      help="statut affiché dans le classement de la sortie d'erreur "
                           "(défaut : pending — un lot signé `rejected` n'a plus à "
                           "être relu, et la plomberie Google masquait le reste)")
    p_cd.add_argument("--write", action="store_true",
                      help="écrire candidates.json")
    p_cd.set_defaults(func=cmd_candidates)

    p_ep = sub.add_parser("endpoints",
                          help="hôtes du corpus classés, endpoints tiers par prévalence")
    p_ep.add_argument("--only", choices=list(endpoints.CLASSES),
                      help="ne garder qu'une classe dans le document de sortie")
    p_ep.add_argument("--top", type=int, default=25,
                      help="nombre d'hôtes détaillés sur la sortie d'erreur")
    p_ep.add_argument("--write", action="store_true", help="écrire endpoints.json")
    p_ep.set_defaults(func=cmd_endpoints)

    p_keys = sub.add_parser("keys",
                            help="identifiants de compte SDK relevés dans les binaires")
    p_keys.add_argument("--only-shared", action="store_true",
                        help="ne garder que les identifiants présents dans plusieurs "
                             "applications — donc probablement une valeur par défaut "
                             "de bibliothèque et non un compte d'éditeur")
    p_keys.add_argument("--top", type=int, default=30,
                        help="nombre d'identifiants détaillés sur la sortie d'erreur")
    p_keys.add_argument("--write", action="store_true", help="écrire keys.json")
    p_keys.set_defaults(func=cmd_keys)

    p_show = sub.add_parser("show", help="lire une série et ses deltas à l'œil")
    p_show.add_argument("--app", help="limiter à un slug")
    p_show.add_argument("--all", action="store_true", help="tout le corpus, une ligne par app")
    p_show.set_defaults(func=cmd_show)

    sub.add_parser("validate", help="valider les snapshots contre le schéma") \
        .set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
