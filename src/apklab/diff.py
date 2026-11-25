"""Moteur de diff. Quatre règles, cf. docs/design.md § Moteur de diff.

C'est ici que se joue la crédibilité des études : un delta mal attribué et le dataset
n'est plus opposable. D'où le parti pris — chaque delta porte de quoi être refusé par
son consommateur, plutôt que d'être silencieusement supprimé ici.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import store
from .analyze import reference_densities


def _version_tuple(value: str | None) -> tuple[int, ...] | None:
    parts = tuple(int(p) for p in re.findall(r"\d+", value or ""))
    return parts or None


def _sort_key(snap: dict) -> tuple:
    """Tri temporel, jamais alphabétique : les observed_at ne portent pas tous le même
    fuseau — heure du committeur pour exodus-git, UTC pour exodus-web — et
    2026-03-01T01:00+02:00 suit 2026-02-28T23:30+00:00 dans l'ordre des chaînes alors
    qu'il le précède dans le temps : il vaut 23:00 UTC."""
    return (datetime.fromisoformat(snap["observed_at"]), snap.get("report_date") or "")


def _densities(parts: list[dict]) -> set[str]:
    return {p["qualifier"] for p in parts if p["kind"] == "density"}


def _reference_delta(prev: dict, cur: dict, regressed: bool) -> dict:
    """Reference-phone size change. Blind to ABI and density changes: that's its purpose."""
    before, after = prev.get("reference_bytes"), cur.get("reference_bytes")
    flags = ["version_regressed"] if regressed else []
    if before is None or after is None:
        flags.append("reference_missing")
    elif (reference_densities(prev.get("size_parts") or [])
          != reference_densities(cur.get("size_parts") or [])):
        flags.append("reference_density_changed")
    return {"reference_bytes_delta": None if before is None or after is None else after - before,
            "flags": flags, "exploitable": not flags}


def build_delta(prev: dict, cur: dict) -> dict:
    prev_perms, cur_perms = set(prev["permissions"]), set(cur["permissions"])
    prev_trk = {t["id"]: t.get("name") for t in prev["trackers"]}
    cur_trk = {t["id"]: t.get("name") for t in cur["trackers"]}

    perms_added = sorted(cur_perms - prev_perms)
    perms_removed = sorted(prev_perms - cur_perms)
    trk_added = sorted(set(cur_trk) - set(prev_trk))
    trk_removed = sorted(set(prev_trk) - set(cur_trk))

    # Règle 2 : un delta de trackers ne dit quelque chose sur l'application que si les
    # deux côtés ont été mesurés avec le même catalogue. Null vaut inconnu, pas identique.
    tracker_flags: list[str] = []
    prev_cat, cur_cat = prev.get("catalog_version"), cur.get("catalog_version")
    if prev_cat is None or cur_cat is None:
        tracker_flags.append("catalog_unknown")
    elif prev_cat != cur_cat:
        tracker_flags.append("analyzer_shift")

    # Règle 4 : le poids n'est comparable qu'entre deux analyses statiques du même
    # périmètre. Un XAPK fusionné contre un APK unique donnerait un écart de source
    # présenté comme un écart d'application.
    prev_v, cur_v = _version_tuple(prev["version_name"]), _version_tuple(cur["version_name"])
    regressed = bool(prev_v and cur_v and cur_v < prev_v)

    size = None
    if (prev["source"] == cur["source"] == "apk-static"
            and prev.get("size_scope") is not None
            and prev.get("size_scope") == cur.get("size_scope")):
        # Un poids qui « baisse » parce que la source a servi une version antérieure
        # n'est pas un gain d'éco-conception, c'est un retour en arrière. Le bloc le
        # déclare lui-même, comme le bloc trackers déclare `catalog_unknown` : un
        # consommateur qui ne lit que la valeur ne doit pas pouvoir la prendre pour
        # bonne. `version_regressed` reste aussi dans `series_flags` — il concerne la
        # série entière — mais le lire là supposait de savoir qu'il faut aller l'y
        # chercher, et c'est exactement ce qui produit un graphe faux.
        size_flags = ["version_regressed"] if regressed else []
        # Same scope isn't same binary: APKPure picks the ABI splits per pass, and a
        # merged XAPK loses 87 MB going from arm64+x86+x86_64 to armeabi-v7a alone.
        prev_abis, cur_abis = prev.get("native_abis"), cur.get("native_abis")
        if prev_abis is None or cur_abis is None:
            size_flags.append("abi_unknown")
        elif set(prev_abis) != set(cur_abis):
            size_flags.append("abi_changed")
        # Density splits move with ABI splits; the global block can't compare across them.
        prev_parts, cur_parts = prev.get("size_parts"), cur.get("size_parts")
        if (prev_parts is None) != (cur_parts is None):
            size_flags.append("density_unknown")
        elif prev_parts is not None and _densities(prev_parts) != _densities(cur_parts):
            size_flags.append("density_changed")
        size ={"size_scope": cur["size_scope"], "flags": size_flags,
                "exploitable": not size_flags}
        for key in ("total_bytes", "dex_bytes", "res_bytes", "assets_bytes", "native_bytes"):
            size[f"{key}_delta"] = (cur.get(key) or 0) - (prev.get(key) or 0)
        if prev_parts is not None or cur_parts is not None:
            size["reference"] = _reference_delta(prev, cur, regressed)

    series_flags: list[str] = []
    if regressed:
        series_flags.append("version_regressed")
    if cur.get("previous_observed_at") not in (None, prev["observed_at"]):
        series_flags.append("chain_broken")
    if prev["source"] != cur["source"]:
        series_flags.append("source_changed")
    elif prev.get("acquisition") != cur.get("acquisition"):
        # Same source, different binary supplier — `source_changed` misses it, since
        # `source` is `apk-static` on both sides of an APKPure → Google Play switch. The
        # bases are byte-identical so permissions and trackers stay usable and this flag
        # gates nothing; it's here so a size or class-count comparison isn't read as a
        # fact about the app.
        #
        # `elif`, not `if`: `acquisition` moves with almost every source change, and
        # flagging both would just add noise to backfill deltas.
        series_flags.append("acquisition_changed")

    return {
        "handle": cur["handle"],
        "slug": cur["slug"],
        # Règle 1 : une fenêtre, jamais une date. Les runs sont manuels et irréguliers ;
        # affirmer un jour précis serait une invention.
        "window": {"from": prev["observed_at"], "to": cur["observed_at"]},
        "from_version": prev["version_name"],
        "to_version": cur["version_name"],
        "from_source": prev["source"],
        "to_source": cur["source"],
        "from_report_date": prev.get("report_date"),
        "to_report_date": cur.get("report_date"),
        # Règle 3 : lues du manifeste et non d'un catalogue, donc exploitables même à
        # travers un changement de source. C'est ce qui sauve les diffs du backfill.
        "permissions": {"added": perms_added, "removed": perms_removed, "exploitable": True},
        "trackers": {
            "added": [{"id": i, "name": cur_trk[i]} for i in trk_added],
            "removed": [{"id": i, "name": prev_trk[i]} for i in trk_removed],
            "flags": tracker_flags,
            "exploitable": not tracker_flags,
        },
        "size": size,
        "series_flags": series_flags,
        "has_change": bool(perms_added or perms_removed or trk_added or trk_removed or size),
    }


def deltas_for(root: Path, handle: str) -> list[dict]:
    """Deltas entre snapshots consécutifs d'une série, du plus ancien au plus récent."""
    series = sorted(store.read(root, handle), key=_sort_key)
    return [build_delta(a, b) for a, b in zip(series, series[1:])]
