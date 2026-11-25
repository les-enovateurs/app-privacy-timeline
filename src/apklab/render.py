"""Rendu lisible d'une série et de ses deltas. Pur : entre des dicts, sort des lignes.

Le JSONL est le format de vérité, pas un format de lecture. Une série de 8 points fait
8 lignes de 2 Ko : personne ne peut voir à l'œil qu'un snapshot s'est rangé au bon
endroit ni qu'un delta est inexploitable. C'est ce que ce module rend visible, et c'est
la seule façon de valider une itération autrement qu'en faisant confiance aux tests.
"""

from __future__ import annotations

# Au-delà, la liste devient illisible et on ne lit de toute façon plus les noms un à un.
MAX_NAMES = 4

FLAG_LABELS = {
    "catalog_unknown": "catalogue inconnu des deux côtés",
    "analyzer_shift": "catalogues différents",
    "version_regressed": "version en recul",
    "abi_changed": "ABI différentes",
    "abi_unknown": "ABI inconnues",
    "chain_broken": "chaînage rompu",
    "source_changed": "changement de source",
    "density_changed": "densités différentes",
    "density_unknown": "densités inconnues",
    "reference_missing": "référence absente d'un côté",
    "reference_abi_missing": "pas d'arm64-v8a",
    "reference_density_substituted": "densité servie à la place de xxhdpi",
    "reference_density_changed": "densités de référence différentes",
    "reference_part_unknown": "part non reconnue",
}


def _day(value: str | None) -> str:
    return (value or "?")[:10]


def _names(items: list[str]) -> str:
    shown = ", ".join(items[:MAX_NAMES])
    rest = len(items) - MAX_NAMES
    return f"{shown} … +{rest}" if rest > 0 else shown


def _short(permission: str) -> str:
    """android.permission.RECORD_AUDIO -> RECORD_AUDIO, le préfixe est le même partout."""
    return permission.rsplit(".", 1)[-1]


def point(snap: dict) -> str:
    flags = f"  [{', '.join(snap['quality_flags'])}]" if snap.get("quality_flags") else ""
    return (f"  ● {_day(snap['observed_at'])}  {snap['version_name'] or '—':<16} "
            f"{snap['source']:<11} {len(snap['permissions']):>3} perms  "
            f"{len(snap['trackers']):>2} pisteurs{flags}")


def delta(item: dict) -> list[str]:
    """Un delta en trois lignes au plus : fenêtre, permissions, pisteurs.

    La fenêtre passe avant le reste parce que c'est elle qui dit ce qu'on a le droit
    d'affirmer. Un delta de pisteurs inexploitable est affiché quand même, barré de son
    motif : le masquer laisserait croire qu'il n'y a rien eu.
    """
    perms, trackers = item["permissions"], item["trackers"]
    lines = [f"  │ fenêtre {_day(item['window']['from'])} → {_day(item['window']['to'])}"
             + (f"   ⚠ {', '.join(FLAG_LABELS.get(f, f) for f in item['series_flags'])}"
                if item["series_flags"] else "")]

    if perms["added"] or perms["removed"]:
        parts = []
        if perms["added"]:
            parts.append(f"+{len(perms['added'])} {_names([_short(p) for p in perms['added']])}")
        if perms["removed"]:
            parts.append(f"−{len(perms['removed'])} {_names([_short(p) for p in perms['removed']])}")
        lines.append(f"  │ permissions  {'  '.join(parts)}")

    if trackers["added"] or trackers["removed"]:
        parts = []
        if trackers["added"]:
            names = [t["name"] or f"#{t['id']}" for t in trackers["added"]]
            parts.append(f"+{len(names)} {_names(names)}")
        if trackers["removed"]:
            names = [t["name"] or f"#{t['id']}" for t in trackers["removed"]]
            parts.append(f"−{len(names)} {_names(names)}")
        motif = ("  ⛔ inexploitable : "
                 + ", ".join(FLAG_LABELS.get(f, f) for f in trackers["flags"])
                 if trackers["flags"] else "")
        lines.append(f"  │ pisteurs     {'  '.join(parts)}{motif}")

    if item["size"]:
        size = item["size"]
        motif = ("  ⛔ inexploitable : "
                 + ", ".join(FLAG_LABELS.get(f, f) for f in size.get("flags") or [])
                 if size.get("flags") else "")
        lines.append(f"  │ poids        {size['total_bytes_delta']:+} o "
                     f"({size['size_scope']}){motif}")
        reference = size.get("reference")
        if reference:
            shown = (f"{reference['reference_bytes_delta']:+} o"
                     if reference["reference_bytes_delta"] is not None else "—")
            reason = ("  ⛔ inexploitable : "
                      + ", ".join(FLAG_LABELS.get(f, f) for f in reference["flags"])
                      if reference["flags"] else "")
            lines.append(f"  │ poids réf. arm64/xxhdpi  {shown}{reason}")

    if len(lines) == 1:
        lines.append("  │ version seule, aucun changement de permission ni de pisteur")
    return lines


def timeline(series: list[dict], deltas: list[dict]) -> list[str]:
    """La série du plus ancien au plus récent, chaque delta intercalé entre ses bornes."""
    if not series:
        return ["  (aucun snapshot)"]

    lines = [point(series[0])]
    for item, snap in zip(deltas, series[1:]):
        lines.extend(delta(item))
        lines.append(point(snap))
    return lines


def header(series: list[dict]) -> str:
    first, last = series[0], series[-1]
    sources = sorted({snap["source"] for snap in series})
    return (f"{first['app_name']} — {first['handle']}\n"
            f"{len(series)} snapshot(s) du {_day(first['observed_at'])} au "
            f"{_day(last['observed_at'])} — source(s) : {', '.join(sources)}")


def overview(rows: list[tuple[str, list[dict], list[dict]]]) -> list[str]:
    """Une ligne par application : de quoi repérer d'un coup d'œil quoi aller regarder.

    Les applications sans snapshot sont montrées, pas filtrées : un trou est justement
    ce qu'on cherche à voir — disneyplus n'a aucun point parce que toutes ses révisions
    sont des faux positifs de renommage.
    """
    lines = [f"{'application':<28} {'snaps':>5} {'deltas':>6} {'perms':>6}  "
             f"{'pisteurs':>8}  dernière observation"]
    for slug, series, deltas in rows:
        if not series:
            lines.append(f"{slug:<28} {'—':>5} {'—':>6} {'—':>6}  {'—':>8}  —")
            continue
        last = series[-1]
        exploitable = sum(1 for d in deltas if d["trackers"]["exploitable"])
        lines.append(f"{slug:<28} {len(series):>5} {len(deltas):>6} "
                     f"{len(last['permissions']):>6}  {len(last['trackers']):>8}  "
                     f"{_day(last['observed_at'])} {last['version_name'] or '—'}"
                     + (f"  ({exploitable}/{len(deltas)} pisteurs exploitables)"
                        if deltas else ""))
    return lines
