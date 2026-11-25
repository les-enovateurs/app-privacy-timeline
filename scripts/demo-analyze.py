#!/usr/bin/env python3
"""Démo de l'analyse statique sur un vrai APK, sans réseau.

`uv run python scripts/demo-analyze.py`

L'APK est celui de la fixture — F-Droid Privileged Extension 0.2.13, 46 Ko, Apache-2.0.
Montre ce que l'analyseur tire du binaire, puis ce que donne la confrontation à un
rapport Exodus de la même version : le point de contrôle de l'itération 4.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apklab import analyze, diff, render, signatures  # noqa: E402
from apklab.corpus import CorpusEntry  # noqa: E402
from apklab.models import Snapshot  # noqa: E402

APK = ROOT / "tests" / "fixtures" / "apk" / "org.fdroid.fdroid.privileged_2130.apk"
ENTRY = CorpusEntry(slug="fdroid-privileged", handle="org.fdroid.fdroid.privileged",
                    app_name="F-Droid Privileged")

# Catalogue de démonstration : deux signatures, dont une qui matche une classe de cet
# APK. Le vrai catalogue se récupère par `apklab signatures fetch --write`.
CATALOGUE = {"trackers": {
    "1": {"id": 1, "name": "Démo — installeur", "code_signature": "android.content.pm.IPackage"},
    "52": {"id": 52, "name": "Google AdMob", "code_signature": "com.google.android.gms.ads."},
}}


def titre(n: int, texte: str) -> None:
    print(f"\n\033[1m── ÉTAPE {n} — {texte}\033[0m")


def main() -> int:
    titre(1, f"le binaire : {APK.name}")
    print(f"  {APK.stat().st_size} octets sur le disque, licence Apache-2.0")

    titre(2, "ce qu'androguard en tire")
    apk = analyze.facts(APK)
    print(f"  paquet        {apk.handle}")
    print(f"  version       {apk.version_name}  (code {apk.version_code})")
    print(f"  SDK           min {apk.min_sdk}, cible {apk.target_sdk}")
    print(f"  permissions   {len(apk.permissions)} : "
          f"{', '.join(p.rsplit('.', 1)[-1] for p in apk.permissions)}")
    print(f"  poids         total {apk.total_bytes} o — dex {apk.dex_bytes}, "
          f"res {apk.res_bytes}, assets {apk.assets_bytes}, natif {apk.native_bytes}")
    print(f"  ABI           {apk.native_abis or '—'}")
    print(f"  dex           {len(apk.class_names)} classes, "
          f"{apk.dex_method_count} méthodes")
    print(f"  sha256        {apk.apk_sha256}")
    print("  aucune source existante ne publie ces poids : ni Exodus, ni le git du site.")

    titre(3, "les signatures de pisteurs cherchées dans les classes")
    trackers = signatures.parse_catalog(CATALOGUE)
    detection = signatures.detect(apk.class_names, trackers)
    for tracker in trackers:
        trouve = "✓ trouvé" if tracker.id in detection.tracker_ids else "· absent"
        print(f"  {trouve}   {tracker.name:<22} /{tracker.code_signature}/")
    print("  c'est le contrat repris d'exodus-core, qui refuse de s'installer sur macOS :")
    print("  une regex par pisteur, cherchée dans les noms de classes du dex.")

    titre(4, "le snapshot apk-static produit")
    snap, _ = analyze.snapshot_for(ENTRY, apk, detection.tracker_ids,
                                   {t.id: t.name for t in trackers},
                                   "2026-09-09/2/sha256:demo00000000",
                                   "2026-09-09T12:00:00+00:00")
    for champ in ("source", "acquisition", "catalog_version", "size_scope",
                  "total_bytes", "dex_method_count", "quality_flags"):
        print(f"  {champ:<18} {snap.to_dict()[champ]}")
    print("  catalog_version est renseigné, contrairement aux sources Exodus : c'est ce")
    print("  qui rendra les deltas de pisteurs exploitables entre deux analyses.")

    titre(5, "le point de contrôle : confrontation au rapport Exodus de la même version")
    for cas, permissions in (("analyseur d'accord avec Exodus", apk.permissions),
                             ("analyseur en désaccord", apk.permissions[:1])):
        reference = _exodus_snapshot(permissions)
        delta = diff.build_delta(reference, snap.to_dict())
        divergence = delta["permissions"]["added"] or delta["permissions"]["removed"]
        print(f"\n  cas « {cas} » :")
        for ligne in render.delta(delta):
            print(f"  {ligne}")
        print("    → permissions " + ("DIVERGENTES, l'analyseur est faux"
                                      if divergence else "identiques, analyseur validé"))

    print("\n  Aucune automatisation ne se construit sur un analyseur non validé.")
    print("  En vrai : uv run apklab analyze <fichier.apk> --slug <x> --compare")
    return 0


def _exodus_snapshot(permissions: list[str]) -> dict:
    return json.loads(json.dumps(Snapshot(
        handle=ENTRY.handle, slug=ENTRY.slug, app_name=ENTRY.app_name,
        version_name="0.2.13", source="exodus-web", analyzer="exodus",
        observed_at="2026-08-01T10:00:00+00:00", report_date="2026-08-01T10:00:00+00:00",
        permissions=permissions, trackers=[{"id": 1, "name": "Démo — installeur"}],
        provenance_ref="123456").to_dict()))


if __name__ == "__main__":
    raise SystemExit(main())
