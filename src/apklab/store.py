"""Store : une ligne JSONL par snapshot, un fichier par handle, trié dans le temps."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import Snapshot


def path_for(root: Path, handle: str) -> Path:
    return root / f"{handle}.jsonl"


def read(root: Path, handle: str) -> list[dict]:
    path = path_for(root, handle)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _instant(value: str) -> datetime:
    """Clé de tri temporelle. Comparer les chaînes ne marche pas : les snapshots git
    portent le fuseau du committeur (+02:00) et ceux d'Exodus de l'UTC, et
    2026-03-01T01:00+02:00 suit 2026-02-28T23:30+00:00 dans l'ordre alphabétique alors
    qu'il le précède dans le temps : il vaut 23:00 UTC."""
    return datetime.fromisoformat(value)


def ingest(root: Path, snapshots: Iterable[Snapshot], write: bool = False) -> dict:
    """Insère les snapshots qui apportent une information nouvelle, à leur place.

    Trois règles, dans cet ordre.

    1. Rejeu exact — (source, fingerprint, observed_at) déjà présent dans le fichier :
       ignoré. C'est ce qui rend `backfill-git --all --write` relançable sans dupliquer
       la série.
    2. Inchangé — même empreinte et même source que le snapshot qui le *précède
       chronologiquement* dans la série : ignoré, c'est la décision 3. La comparaison
       porte sur le précédent et pas sur tout le fichier, sinon une permission retirée
       puis remise produirait deux points au lieu de trois et le retour en arrière
       disparaîtrait de l'historique.
    3. Sinon : insertion à sa place dans l'ordre chronologique.

    L'insertion hors ordre est la règle et non l'exception depuis le backfill Exodus :
    un rapport de 2019 découvert aujourd'hui se range avant des snapshots déjà écrits.
    D'où le tri à l'écriture, et la réécriture du fichier plutôt qu'un simple append.

    Règle 0, posée après coup : **un snapshot marqué `trackers_not_analyzed` n'entre
    jamais dans une série.** Sa liste de pisteurs vide veut dire « pas cherché » ; rangée
    entre deux points qui, eux, ont cherché, elle produit une oscillation −8 puis +8 que
    rien ne distingue d'un vrai retrait de pisteurs, et le store étant append-only, elle
    y reste pour toujours. Le refus est ici et non dans la CLI parce que toutes les
    voies d'écriture passent par cette fonction, `store ingest` sur stdin compris. Le
    snapshot reste imprimé par la commande qui l'a produit : sans catalogue, l'analyse
    garde son intérêt pour les poids, elle n'a simplement pas sa place dans la série.

    Aucune ligne déjà écrite n'est supprimée, même si un point inséré avant elle la rend
    redondante : elle a une provenance propre — un commit, un id de rapport — et des
    deltas déjà publiés s'y adossent. Seul son previous_observed_at bouge.

    previous_observed_at est recalculé ici sur toute la série, pas au backfill : seul le
    store sait ce qui a fini dans le fichier. Un chaînage calculé en amont pointerait sur
    un snapshot que les règles 1 ou 2 viennent d'écarter, et un point inséré au milieu
    laisserait son successeur chaîné sur un prédécesseur qui n'est plus le sien — la
    fenêtre d'incertitude de ses deltas serait plus large que ce que la donnée permet.
    """
    stats = {"appended": 0, "replayed": 0, "unchanged": 0, "refused": 0, "handles": 0}

    grouped: dict[str, list[Snapshot]] = {}
    for snap in snapshots:
        if "trackers_not_analyzed" in (snap.quality_flags or []):
            stats["refused"] += 1
            continue
        grouped.setdefault(snap.handle, []).append(snap)

    for handle, incoming in grouped.items():
        merged = read(root, handle)
        keys = {(s["source"], s["fingerprint"], s["observed_at"]) for s in merged}
        added = 0

        for snap in sorted(incoming, key=lambda s: _instant(s.observed_at)):
            key = (snap.source, snap.fingerprint, snap.observed_at)
            if key in keys:
                stats["replayed"] += 1
                continue
            at = _position(merged, snap.observed_at)
            previous = merged[at - 1] if at else None
            if previous and previous["fingerprint"] == snap.fingerprint \
                    and previous["source"] == snap.source:
                stats["unchanged"] += 1
                continue
            keys.add(key)
            merged.insert(at, snap.to_dict())
            added += 1

        if not added:
            continue
        stats["appended"] += added
        stats["handles"] += 1

        for n, row in enumerate(merged):
            row["previous_observed_at"] = merged[n - 1]["observed_at"] if n else None

        if write:
            root.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in merged]
            path_for(root, handle).write_text("\n".join(lines) + "\n", "utf-8")

    return stats


def _position(rows: list[dict], observed_at: str) -> int:
    """Rang d'insertion : après les points de même date, avant les plus récents.

    À date égale, le nouveau passe derrière l'existant. Deux sources peuvent dater du
    même instant — un commit du site qui reprend un rapport scanné la même minute — et
    l'ordre d'arrivée est alors le seul départage disponible.
    """
    when = _instant(observed_at)
    for n, row in enumerate(rows):
        if _instant(row["observed_at"]) > when:
            return n
    return len(rows)
