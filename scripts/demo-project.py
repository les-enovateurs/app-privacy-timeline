#!/usr/bin/env python3
"""Démo de la projection vers les fiches du site, sur un faux site jetable.

`uv run python scripts/demo-project.py`

Rejoue une vraie `apklab project` sur quatre applications posées dans un répertoire
temporaire, aux formats exacts du site mesurés sur ses 131 fiches. Montre ce que la
commande écrit, ce qu'elle refuse d'écrire, et surtout **à quoi ressemble le diff de
revue** — c'est lui la validation promise par le design, pas la commande.
"""

from __future__ import annotations

import difflib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apklab import cli, corpus, project, store  # noqa: E402
from apklab.corpus import CorpusEntry  # noqa: E402
from apklab.models import Snapshot  # noqa: E402

# Quatre cas, un par colonne du problème.
A_JOUR = "carrefour"          # le store et la fiche disent la même chose
BOUGE = "fnac"                # une nouvelle version scannée
EN_AVANCE = "spotify"         # le site a été mis à jour depuis notre backfill
SANS_PISTEURS = "lidl"        # dernier point : analyse statique sans catalogue

HANDLES = {A_JOUR: "com.carrefour.fid.android", BOUGE: "com.fnac.android",
           EN_AVANCE: "com.spotify.music", SANS_PISTEURS: "com.lidl.eci.lidlplus"}


def titre(n: int, texte: str) -> None:
    print(f"\n\033[1m── ÉTAPE {n} — {texte}\033[0m")


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    atelier = Path(tempfile.mkdtemp(prefix="demo-project-"))
    compare = _installe(atelier)

    titre(1, "une fiche du site, telle qu'elle est aujourd'hui")
    fiche = compare / f"{A_JOUR}.json"
    print(f"  {fiche.name} — {len(fiche.read_bytes())} octets, "
          f"{len(fiche.read_text('utf-8').splitlines())} lignes")
    for ligne in fiche.read_text("utf-8").splitlines()[:6]:
        print(f"  {ligne}")
    print("  …")
    print("\n  Format mesuré sur les 131 fiches : indentation 2, unicode non échappé,")
    print("  aucun saut de ligne final. La projection le reproduit — sinon le diff de")
    print("  revue serait « fichier entier réécrit » et personne ne le lirait.")
    print("  Et `slug_playstore`, présent sur 95 fiches, appartient au site : ce dépôt")
    print("  ne sait pas le reproduire, donc il n'y touche pas.")

    titre(2, "la projection, à l'œil")
    print("  $ apklab project --all\n")
    avant = {slug: (compare / f"{slug}.json").read_text("utf-8") for slug in HANDLES}
    _apklab(atelier, "project", "--all")

    titre(3, "ce qui est refusé, et pourquoi")
    print(f"  {EN_AVANCE:<12} le site porte une date plus récente que notre dernier")
    print("               point. L'écraser ferait *reculer* le site, et sans bruit : le")
    print("               diff montrerait un changement, pas une régression. Mesuré pour")
    print("               de vrai sur 13 applications, dont Facebook qui serait passé de")
    print("               548.1.0.51.64 à 529.0.0.44.73.")
    print(f"  {SANS_PISTEURS:<12} son dernier point est une analyse statique sans catalogue :")
    print("               sa liste de pisteurs vide veut dire « pas cherché ». La publier")
    print("               écrirait « aucun pisteur » sur la fiche — le défaut même que le")
    print("               design reproche aux données existantes, 22 fiches à trackers []")
    print("               dont gmail.json.")
    print(f"  {A_JOUR:<12} inchangée : le store vient du git du site, donc projeter doit")
    print("               rendre sa fiche identique octet pour octet. C'est la propriété")
    print("               qui valide l'étage.")

    titre(4, "le diff de revue")
    print("  $ apklab project --all --write\n")
    _apklab(atelier, "project", "--all", "--write")
    print()
    for slug in HANDLES:
        après = (compare / f"{slug}.json").read_text("utf-8")
        if après == avant[slug]:
            continue
        for ligne in difflib.unified_diff(
                avant[slug].splitlines(), après.splitlines(),
                fromfile=f"a/{slug}.json", tofile=f"b/{slug}.json", lineterm=""):
            print(f"  {_couleur(ligne)}")
    print("\n  Une permission par ligne, une date, une version. C'est ça que la décision")
    print("  « git diff côté site avant tout commit » suppose lisible — et c'est pour ça")
    print("  que la projection complète une fiche au lieu de la réécrire.")

    titre(5, "relancer ne change plus rien")
    print("  $ apklab project --all\n")
    _apklab(atelier, "project", "--all")
    print("\n  Zéro à changer. La projection est idempotente, donc relançable sans bruit :")
    print("  un second passage qui bougerait des fichiers voudrait dire que l'écriture et")
    print("  la lecture ne s'accordent pas sur un format.")

    print("\n  En vrai, dans cet ordre :")
    print("    uv run apklab project --all                       # à l'œil, n'écrit rien")
    print("    uv run apklab project --all --write")
    print("    git -C ../reboot diff -- public/data/compare      # la vraie validation")
    return 0


def _couleur(ligne: str) -> str:
    if ligne.startswith("+") and not ligne.startswith("+++"):
        return f"\033[32m{ligne}\033[0m"
    if ligne.startswith("-") and not ligne.startswith("---"):
        return f"\033[31m{ligne}\033[0m"
    return ligne


def _apklab(atelier: Path, *args: str) -> None:
    cli.main(["--site-repo", str(atelier), *args])


def fiche(slug: str, version: str, report_date: str, permissions: list[str],
          trackers: list[int]) -> dict:
    """Une fiche au format du site, `slug_playstore` compris."""
    return {"app_name": slug.title(), "handle": HANDLES[slug], "version": version,
            "report_date": report_date, "permissions": permissions,
            "trackers": trackers, "slug_playstore": HANDLES[slug]}


def _installe(atelier: Path) -> Path:
    """Un faux site et un store, l'un cohérent avec l'autre sauf là où c'est voulu."""
    cli.CORPUS_PATH = atelier / "corpus.json"
    cli.SNAPSHOT_ROOT = atelier / "snapshots"
    cli.RUNS_ROOT = atelier / "runs"

    corpus.write([CorpusEntry(slug=s, handle=h, app_name=s.title())
                  for s, h in HANDLES.items()], cli.CORPUS_PATH)

    compare = atelier / corpus.COMPARE_RELDIR
    compare.mkdir(parents=True)

    perms = ["android.permission.ACCESS_FINE_LOCATION", "android.permission.INTERNET"]

    # À jour : la fiche et le store disent exactement la même chose. Les pisteurs sont
    # dans l'ordre d'Exodus, pas triés — 80 des 131 fiches réelles sont dans ce cas.
    _pose(compare, A_JOUR, fiche(A_JOUR, "23.0.0", "2026-04-18T16:17:00.000Z",
                                 perms, [49, 12]))
    _snap(A_JOUR, "23.0.0", "2026-04-18T16:17:00.000Z", perms, [12, 49])

    # Bouge : une version plus récente, une permission de plus, un pisteur de moins.
    _pose(compare, BOUGE, fiche(BOUGE, "7.2.5", "2026-01-06T09:00:00.000Z",
                                perms, [12, 49]))
    _snap(BOUGE, "7.4.0", "2026-05-20T11:30:00.000Z",
          [*perms, "android.permission.CAMERA"], [12])

    # En avance : le site a été mis à jour depuis notre dernier backfill.
    _pose(compare, EN_AVANCE, fiche(EN_AVANCE, "9.1.36.1948",
                                    "2026-04-07T08:00:00.000Z", perms, [49]))
    _snap(EN_AVANCE, "9.1.6.1145", "2025-12-22T08:00:00.000Z", perms, [49])

    # Sans pisteurs : un point Exodus, puis une analyse statique sans catalogue.
    _pose(compare, SANS_PISTEURS, fiche(SANS_PISTEURS, "5.30.0",
                                        "2026-02-01T10:00:00.000Z", perms, [12, 49]))
    _snap(SANS_PISTEURS, "5.30.0", "2026-02-01T10:00:00.000Z", perms, [12, 49])
    store.ingest(cli.SNAPSHOT_ROOT, [Snapshot(
        handle=HANDLES[SANS_PISTEURS], slug=SANS_PISTEURS,
        app_name=SANS_PISTEURS.title(), version_name="5.31.0", source="apk-static",
        acquisition="apkpure", observed_at="2026-09-09T14:38:52+00:00",
        permissions=perms, trackers=[],
        quality_flags=["empty_trackers", "merged_xapk", "trackers_not_analyzed"])],
        write=True)
    return compare


def _pose(compare: Path, slug: str, doc: dict) -> None:
    (compare / f"{slug}.json").write_text(project.dumps(doc), "utf-8")


def _snap(slug: str, version: str, report_date: str, permissions: list[str],
          trackers: list[int]) -> None:
    store.ingest(cli.SNAPSHOT_ROOT, [Snapshot(
        handle=HANDLES[slug], slug=slug, app_name=slug.title(), version_name=version,
        source="exodus-git", observed_at=report_date, report_date=report_date,
        permissions=permissions,
        trackers=[{"id": i, "name": f"T{i}"} for i in trackers])], write=True)


if __name__ == "__main__":
    raise SystemExit(main())
