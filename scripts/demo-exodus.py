#!/usr/bin/env python3
"""Démo hors ligne du backfill Exodus, étape par étape.

`uv run python scripts/demo-exodus.py`

Aucune requête réseau : le HTML servi est celui des pages réelles archivées dans
tests/fixtures/exodus/. Le but est de *voir* ce que fait chaque étage — liste, dédup,
détail, snapshot, insertion dans la série — au lieu de le déduire d'une suite de tests
verts. Rien n'est écrit hors d'un répertoire temporaire.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apklab import backfill_exodus as bx  # noqa: E402
from apklab import diff, render, store  # noqa: E402
from apklab.corpus import CorpusEntry  # noqa: E402
from apklab.models import Snapshot  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "exodus"
FACEBOOK = CorpusEntry(slug="facebook", handle="com.facebook.katana", app_name="Facebook")
NAMES = {65: "Facebook Ads", 66: "Facebook Analytics", 68: "Facebook Login",
         47: "Google CrashLytics", 48: "Google Firebase Analytics"}


def titre(n: int, texte: str) -> None:
    print(f"\n\033[1m── ÉTAPE {n} — {texte}\033[0m")


def main() -> int:
    liste = (FIXTURES / "search_facebook_p1.html").read_text("utf-8")
    detail = (FIXTURES / "report_775051.html").read_text("utf-8")

    # ---------------------------------------------------------------- 1. liste
    titre(1, "la page de liste, 25 rapports par page")
    refs, page_suivante = bx.parse_list(liste)
    print(f"  {len(refs)} rapports lus, page suivante : {page_suivante}")
    for ref in refs[:5]:
        print(f"    rapport {ref.report_id}  version {ref.version_name}  ({ref.store})")
    print("    …")

    # ---------------------------------------------------------------- 2. dédup
    titre(2, "un rapport par version distincte, le plus ancien")
    gardes, ecartes = bx.dedupe_by_version(refs)
    print(f"  {len(refs)} rapports → {len(gardes)} versions distinctes, "
          f"{len(ecartes)} re-scans écartés")
    print("  autrement dit autant de requêtes de détail économisées. Exemple :")
    for issue in ecartes[:3]:
        print(f"    rapport {issue.ref} : {issue.detail}")

    # --------------------------------------------------------------- 3. détail
    titre(3, "le détail d'un rapport")
    rapport = bx.parse_report(detail, 775051)
    print(f"  handle        {rapport.handle}")
    print(f"  version       {rapport.version_name}  (code {rapport.version_code})")
    print(f"  créé le       {rapport.created_at}   re-scanné : {rapport.updated_at}")
    print(f"  source        {rapport.store}")
    print(f"  sha256 APK    {rapport.apk_sha256}")
    print(f"  permissions   {len(rapport.permissions)}  "
          f"({', '.join(p.rsplit('.', 1)[-1] for p in rapport.permissions[:3])} …)")
    print(f"  pisteurs      {len(rapport.tracker_ids)}  {rapport.tracker_ids}")
    print("  la page annonce 92 permissions et 9 pisteurs à ses badges : c'est le compte.")

    # ------------------------------------------------------------- 4. snapshot
    titre(4, "le snapshot produit")
    snap, _ = bx.snapshot_for(FACEBOOK, rapport, NAMES)
    ligne = json.dumps(snap.to_dict(), ensure_ascii=False, sort_keys=True)
    print(f"  une ligne JSONL de {len(ligne)} octets, dont :")
    for champ in ("source", "observed_at", "version_code", "apk_sha256",
                  "provenance_ref", "catalog_version", "fingerprint"):
        print(f"    {champ:<16} {snap.to_dict()[champ]}")
    print("  catalog_version reste nul : la page publie ses pisteurs, pas le catalogue")
    print("  qui les a détectés. Les deltas de pisteurs sortiront donc inexploitables.")

    # --------------------------------------------- 5. insertion dans la série
    titre(5, "l'insertion hors ordre, le vrai changement de cette itération")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "snapshots"

        # Deux points venus du git du site, comme ceux déjà committés dans le dépôt.
        # Leurs permissions sont des sous-ensembles de celles du rapport réel, pour que
        # les deltas affichés ressemblent à ceux qu'on lit sur le dépôt.
        store.ingest(root, [
            _git_snap("2026-03-10T09:00:00+01:00", "570.0",
                      rapport.permissions[:85], [65, 66]),
            _git_snap("2026-09-01T09:00:00+02:00", "577.0.0.50.72",
                      rapport.permissions[:90], [65, 66, 68]),
        ], write=True)
        print("  série de départ, reconstruite depuis le git du site :")
        _afficher(root)

        # Un rapport Exodus antérieur au dernier point : il ne peut pas finir en fin de
        # fichier, sinon la série cesse d'être une série.
        ancien, _ = bx.snapshot_for(FACEBOOK, bx.parse_report(
            detail.replace("31 août 2026 07:31", "5 juin 2026 12:00"), 700000), NAMES)
        stats = store.ingest(root, [ancien], write=True)
        print(f"\n  ingestion d'un rapport Exodus daté du 5 juin 2026 → {stats}")
        print("  il se range entre les deux, et le point de septembre est re-chaîné :")
        _afficher(root)

    print("\n  Sur le dépôt réel, la même lecture est disponible sur les données "
          "committées :\n    uv run apklab show --app carrefour\n    uv run apklab show --all")
    return 0


def _git_snap(quand: str, version: str, perms: list[str], trackers: list[int]) -> Snapshot:
    return Snapshot(handle=FACEBOOK.handle, slug=FACEBOOK.slug, app_name=FACEBOOK.app_name,
                    version_name=version, source="exodus-git", analyzer="exodus",
                    observed_at=quand, report_date=quand, permissions=perms,
                    trackers=[{"id": i, "name": NAMES.get(i)} for i in trackers])


def _afficher(root: Path) -> None:
    serie = sorted(store.read(root, FACEBOOK.handle), key=diff._sort_key)
    for ligne in render.timeline(serie, diff.deltas_for(root, FACEBOOK.handle)):
        print(f"  {ligne}")
    print("    previous_observed_at : "
          + ", ".join(f"{s['observed_at'][:10]} ← {(s['previous_observed_at'] or '—')[:10]}"
                      for s in serie))


if __name__ == "__main__":
    raise SystemExit(main())
