#!/usr/bin/env python3
"""Démo du sous-projet C : les candidats pisteurs, sur un corpus jetable.

`uv run python scripts/demo-candidates.py`

Lance les **vraies** commandes. `analyze` tourne sur le vrai APK de fixture et dépose
ses preuves ; trois preuves synthétiques complètent le corpus pour que la prévalence
ait un sens ; puis `candidates` fait ses trois soustractions et sa corrélation.

Ce que la démo doit rendre visible, c'est ce qui est *retiré* et pourquoi — un candidat
n'a de valeur que si on sait ce que le tri a déjà écarté — et le fait qu'un jugement
humain survive à la passe suivante.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apklab import candidates, cli, corpus, evidence  # noqa: E402
from apklab.corpus import CorpusEntry  # noqa: E402

APK = ROOT / "tests" / "fixtures" / "apk" / "org.fdroid.fdroid.privileged_2130.apk"
FIXTURE_HANDLE = "org.fdroid.fdroid.privileged"

# Le catalogue de la démo : trois entrées réelles, copiées du catalogue Exodus. La
# signature d'AdMob nomme une *classe* et non un package — c'est le cas qui a produit
# un faux candidat de 358 classes sur Grindr avant d'être traité.
CATALOGUE = {
    "catalog_version": "2026-09-09/3/sha256:demo00000000",
    "trackers": {
        "312": {"id": 312, "name": "Google AdMob",
                "code_signature": r"com\.google\.ads\.|com\.google\.android\.gms\.ads\.AdView"},
        "2": {"id": 2, "name": "Adjust", "code_signature": r"com\.adjust\.sdk"},
        "49": {"id": 49, "name": "Google Firebase Analytics",
               "code_signature": r"com\.google\.firebase\.analytics\.FirebaseAnalytics"},
    },
}

# L'allowlist réelle du dépôt, pas une liste de démo : c'est elle qui décide du taux de
# faux positifs, et la montrer à l'œuvre fait partie de ce qu'on valide ici.
ALLOWLIST = (ROOT / "signatures" / "allowlist.txt").read_text("utf-8")


def titre(n: int, texte: str) -> None:
    print(f"\n\033[1m── ÉTAPE {n} — {texte}\033[0m")


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    atelier = Path(tempfile.mkdtemp(prefix="demo-candidates-"))
    _installe(atelier)

    titre(1, "les preuves, écrites par la vraie analyse d'un vrai APK")
    print(f"  $ apklab analyze {APK.name} --slug fdroid-privileged --write\n")
    cli.main(["--site-repo", str(atelier), "analyze", str(APK),
              "--slug", "fdroid-privileged", "--write"])
    preuve = json.loads(
        (atelier / "evidence" / FIXTURE_HANDLE / "0.2.13.json").read_text("utf-8"))
    print(f"\n  evidence/{FIXTURE_HANDLE}/0.2.13.json")
    for entry in preuve["packages"]:
        print(f"    {entry['package']:<40} {entry['classes']:>3} classes  "
              f"ex. {entry['sample'][0]}")
    print(f"    domaines : {preuve['domains'] or '— (aucun, application de 46 Ko)'}")
    print("\n  Les preuves vivent à côté du store et jamais dedans : l'inventaire d'une")
    print("  grosse application pèse plus d'un Mo — 14 785 packages sur Grindr — et une")
    print("  ligne JSONL de cette taille rendrait la série illisible.")

    titre(2, "trois preuves de plus, pour que la prévalence veuille dire quelque chose")
    _pose_corpus(atelier)
    for doc in evidence.load_all(atelier / "evidence"):
        print(f"  {doc['handle']:<32} {len(doc['packages']):>3} package(s), "
              f"{len(doc['domains'])} domaine(s)")
    print(f"\n  allowlist : {len(candidates.load_allowlist(atelier / 'allowlist.txt'))} "
          "préfixes bénins, celle du dépôt")

    titre(3, "ce que la commande retire, et pourquoi")
    print("  $ apklab candidates\n")
    cli.main(["--site-repo", str(atelier), "candidates"])
    print("\n  Quatre soustractions, dans cet ordre :")
    print("    com.adjust.sdk               catalogué chez Exodus — la signature")
    print("                                 s'applique au préfixe")
    print("    com.google.android.gms.ads.internal   catalogué aussi, mais la signature")
    print("                                 d'AdMob nomme une classe (`…ads.AdView`).")
    print("                                 Sans le second test, 358 classes de Grindr")
    print("                                 ressortaient en candidat.")
    print("    androidx.core, okhttp3.…     allowlist de libs bénignes — la liste qui")
    print("                                 décide seule du taux de faux positifs")
    print("    com.shopdemo.…               le paquet de l'application elle-même")

    titre(4, "les candidats, écrits en pending")
    print("  $ apklab candidates --write\n")
    cli.main(["--site-repo", str(atelier), "candidates", "--write"])
    doc = json.loads((atelier / "candidates.json").read_text("utf-8"))
    print()
    for candidate in doc["candidates"]:
        apps = ", ".join(a["handle"] for a in candidate["apps"])
        print(f"  \033[1m{candidate['package']}\033[0m — {candidate['status']}, "
              f"{candidate['app_count']} app(s), {candidate['class_count']} classes")
        print(f"    endpoints : {', '.join(candidate['endpoints']) or '—'}")
        print(f"    porté par : {apps}")
        print(f"    classes   : {', '.join(candidate['sample_classes'][:2])}")
    print("\n  `com.pistodemo.sdk` est dans trois applications sur quatre et parle à")
    print("  `api.pistodemo.com` : c'est le profil d'un SDK tiers non catalogué. La")
    print("  corrélation vient du nom du package lu à l'envers — un SDK nomme presque")
    print("  toujours ses classes d'après son domaine. L'inverse n'est pas vrai : pas")
    print("  d'endpoint ne veut pas dire pas de sortie réseau, d'où « candidat ».")

    titre(5, "un jugement humain survit à la passe suivante")
    _juge(atelier, "com.rendudemo.ui", "rejected", "bibliothèque de rendu, vérifiée à la main")
    print("  candidates.json édité à la main : com.rendudemo.ui -> rejected")
    print("\n  $ apklab candidates --write\n")
    cli.main(["--site-repo", str(atelier), "candidates", "--write"])
    après = json.loads((atelier / "candidates.json").read_text("utf-8"))
    print()
    for candidate in après["candidates"]:
        note = f" — « {candidate['note']} »" if candidate.get("note") else ""
        print(f"  {candidate['package']:<28} {candidate['status']}{note}")
    print("\n  Aucun candidat n'atteint le site sans signature humaine, et une passe ne")
    print("  réévalue jamais un statut posé à la main : un `rejected` qui redeviendrait")
    print("  `pending` ferait relire chaque mois les mêmes faux positifs, et le tri")
    print("  s'arrêterait au deuxième passage.")

    titre(6, "mesuré pour de vrai, le 2026-09-10")
    print("  Grindr 26.15.1, XAPK de 166 Mo déjà en cache, analysé par la vraie commande :")
    print("    83 284 classes -> 14 785 packages, 173 domaines, 1,4 Mo de preuves")
    print("    19 pisteurs catalogués détectés, retirés des candidats")
    print("    en tête des candidats : com.facetec.sdk (1 082 classes, api.facetec.com),")
    print("    com.google.android.recaptcha.internal (989), gatewayprotocol.v1 (704),")
    print("    bo.app (444, bo.app — le SDK Braze), io.bidmachine (285, api.bidmachine.io)")
    print("\n  Une seule application analysée : la prévalence ne veut encore rien dire.")
    print("  Elle prendra son sens après la passe réelle de l'itération 6.")
    return 0


def _installe(atelier: Path) -> None:
    cli.CORPUS_PATH = atelier / "corpus.json"
    cli.SNAPSHOT_ROOT = atelier / "snapshots"
    cli.RUNS_ROOT = atelier / "runs"
    cli.SIGNATURES_PATH = atelier / "trackers.json"
    cli.ALLOWLIST_PATH = atelier / "allowlist.txt"
    cli.EVIDENCE_ROOT = atelier / "evidence"
    cli.CANDIDATES_PATH = atelier / "candidates.json"
    cli.ROOT = atelier
    cli.CACHE_ROOT = atelier / "cache"

    corpus.write([CorpusEntry(slug="fdroid-privileged", handle=FIXTURE_HANDLE,
                              app_name="F-Droid Privileged")], cli.CORPUS_PATH)
    (atelier / "trackers.json").write_text(json.dumps(CATALOGUE), "utf-8")
    (atelier / "allowlist.txt").write_text(ALLOWLIST, "utf-8")

    compare = atelier / corpus.COMPARE_RELDIR
    compare.mkdir(parents=True)
    (compare / "trackers.json").write_text(
        json.dumps([{"id": int(k), "name": v["name"]}
                    for k, v in CATALOGUE["trackers"].items()]), "utf-8")


def _pose_corpus(atelier: Path) -> None:
    """Trois applications, chacune posée pour un cas du tri."""
    commun = ["com.pistodemo.sdk.Session", "androidx.core.app.Fragment",
              "okhttp3.internal.Http", "com.adjust.sdk.Adjust"]
    _preuve(atelier, "com.shopdemo.android",
            [*commun, "com.shopdemo.android.Main", "com.shopdemo.panier.Panier",
             "com.rendudemo.ui.Vue", "com.google.android.gms.ads.internal.Overlay"],
            ["api.pistodemo.com", "www.shopdemo.fr", "app-measurement.com"])
    _preuve(atelier, "com.voyagedemo.app",
            [*commun, "com.voyagedemo.app.Main", "com.rendudemo.ui.Vue"],
            ["cdn.pistodemo.com", "api.voyagedemo.com"])
    _preuve(atelier, "com.banquedemo.mobile",
            ["androidx.core.app.Fragment", "com.banquedemo.mobile.Main",
             "com.rendudemo.ui.Vue", "com.pistodemo.sdk.Session"],
            ["api.banquedemo.fr", "api.pistodemo.com"])


def _preuve(atelier: Path, handle: str, classes: list[str], domains: list[str]) -> None:
    evidence.write(atelier / "evidence", evidence.build(
        handle=handle, version_name="1.0", apk_sha256="0" * 64,
        observed_at="2026-09-10T08:00:00+00:00", analyzer_version="androguard 4.1.4",
        class_names=classes, domains=domains))


def _juge(atelier: Path, package: str, status: str, note: str) -> None:
    path = atelier / "candidates.json"
    doc = candidates.load(path)
    for candidate in doc["candidates"]:
        if candidate["package"] == package:
            candidate["status"] = status
            candidate["note"] = note
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
