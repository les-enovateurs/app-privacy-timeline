"""Catalogue de signatures de pisteurs et détection dans une liste de classes.

Cet étage remplace `exodus-core`, qui refuse de s'installer ailleurs que sur Linux
(« we do not support your platform darwin ») alors que la collecte tourne en local sur
un poste macOS. Ce qu'il apportait tient en une ligne : chaque pisteur porte une
`code_signature`, une expression régulière cherchée dans les noms de classes du dex.
On garde ce contrat mot pour mot — c'est lui qui rend nos résultats comparables à ceux
d'Exodus, condition pour mélanger les deux sources dans une même série.

La fidélité de cette réécriture n'est pas décrétée, elle se mesure : l'itération 4
compare notre analyse d'une version aux pisteurs du rapport Exodus de la même version.
Une divergence signifie que ce module est faux, et on l'apprend avant d'automatiser.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# L'API publique des pisteurs, à récupérer quand l'hôte est joignable. Le catalogue est
# ensuite versionné dans le dépôt : sans lui figé, un nombre de pisteurs ne veut rien
# dire, et une analyse d'il y a six mois n'est plus rejouable.
CATALOG_URL = "https://reports.exodus-privacy.eu.org/api/trackers"


@dataclass(frozen=True)
class Tracker:
    id: int
    name: str
    code_signature: str


class CatalogError(Exception):
    pass


def parse_catalog(payload: dict) -> list[Tracker]:
    """La charge de /api/trackers -> des pisteurs utilisables.

    Le service rend `{"trackers": {"1": {...}, ...}}`, l'id étant la clé et non un
    champ. Les pisteurs sans signature de code existent — Exodus en catalogue certains
    sur leur seule signature réseau — et sont écartés ici : on ne les détecterait jamais
    dans un dex, les garder gonflerait le catalogue sans rien détecter.
    """
    trackers = payload.get("trackers")
    if not isinstance(trackers, dict):
        raise CatalogError("charge inattendue : pas de dictionnaire « trackers »")
    out = []
    for key, entry in trackers.items():
        signature = (entry.get("code_signature") or "").strip()
        if not signature:
            continue
        out.append(Tracker(int(entry.get("id", key)), entry.get("name") or "", signature))
    return sorted(out, key=lambda t: t.id)


def catalog_version(raw: bytes, date: str) -> str:
    """AAAA-MM-JJ/<nb>/sha256:<court>, même format que le catalogue de noms du site.

    La date est celle de la récupération, pas celle de la publication : Exodus ne
    versionne pas sa liste. C'est une approximation assumée, et c'est déjà tout ce qu'il
    faut pour dire « ces deux analyses n'ont pas vu le même catalogue ».
    """
    payload = json.loads(raw.decode("utf-8"))
    return (f"{date}/{len(payload.get('trackers') or {})}/"
            f"sha256:{hashlib.sha256(raw).hexdigest()[:12]}")


def load(path: Path, corrections: Path | None = None) -> tuple[list[Tracker], str]:
    """Un catalogue local -> ses pisteurs et son identifiant de version.

    Le fichier porte les deux : la liste et la version sous laquelle elle a été figée.
    Les séparer laisserait fatalement une analyse revendiquer une version de catalogue
    qui n'est pas celle avec laquelle elle a tourné.

    Les corrections locales, s'il y en a, sont appliquées ici et **changent
    l'identifiant de version** : une analyse faite avec un catalogue corrigé n'est pas
    comparable à une analyse faite sans, et le snapshot doit pouvoir le dire.
    """
    doc = json.loads(path.read_text("utf-8"))
    trackers = parse_catalog(doc)
    version = doc.get("catalog_version") or "inconnu"
    if corrections and corrections.exists():
        raw = corrections.read_bytes()
        trackers, appliquées, orphelines = apply_corrections(
            trackers, json.loads(raw.decode("utf-8")))
        if appliquées:
            version += f"+corr:{appliquées}/sha256:{hashlib.sha256(raw).hexdigest()[:12]}"
        if orphelines:
            # Le module est pur : il rend l'information, la CLI la dit. Une correction
            # orpheline est devenue inutile ou porte un mauvais identifiant, et les deux
            # se règlent en la retirant du fichier.
            LAST_ORPHANS[:] = orphelines
    return trackers, version


# Consignation des corrections orphelines de la dernière lecture. Une liste de module
# plutôt qu'un retour de plus : `load` est appelée par six commandes qui n'ont pas
# toutes de quoi remonter un troisième élément, et l'information est un avertissement,
# pas un résultat.
LAST_ORPHANS: list[int] = []


def apply_corrections(trackers: list[Tracker],
                      doc: dict) -> tuple[list[Tracker], int, list[int]]:
    """Étend les signatures d'Exodus là où le SDK a été renommé sous elles.

    Mesuré le 2026-09-10 : Vungle se cherche par `com.vungle.publisher.` et
    `com.vungle.warren.`, Braze par `com.appboy`. Les deux SDK ont été renommés —
    `com.vungle.ads`, `com.braze` — et nos propres candidats les remontaient donc comme
    des pisteurs inconnus alors qu'ils sont catalogués depuis des années. Ne rien faire
    reviendrait à publier « pas de Braze » sur une application qui l'embarque.

    Trois garde-fous, parce qu'éditer la donnée d'un tiers est exactement le genre de
    chose qui se retourne contre soi six mois plus tard.

    1. **Une correction ne peut qu'ajouter.** La signature d'origine doit rester
       textuellement dans la corrigée : une correction qui la remplacerait pourrait
       *retirer* une détection sans que personne ne le voie, et c'est la seule faute
       irrattrapable ici — un faux négatif silencieux.
    2. **Un identifiant inconnu du catalogue est consigné et sauté**, jamais appliqué —
       même traitement qu'une signature illisible, et pour la même raison : le catalogue
       est de la donnée tierce qui bouge sous nos pieds. Une correction devenue orpheline
       après une mise à jour d'Exodus ne doit pas empêcher toutes les commandes de
       tourner ; elle doit se voir et se retirer. Ce fichier corrige Exodus, il n'invente
       pas de pisteur.
    3. **Chaque correction porte son motif et l'endroit où le nouveau nom a été vu.**
       Sans quoi personne ne saura, à la prochaine mise à jour du catalogue, laquelle
       est devenue inutile — ni quoi signaler en amont.
    """
    par_id = {t.id: t for t in trackers}
    corrigés = dict(par_id)
    orphelines: list[int] = []
    for entry in doc.get("corrections") or []:
        tracker = par_id.get(int(entry["id"]))
        if tracker is None:
            orphelines.append(int(entry["id"]))
            continue
        if not (entry.get("why") and entry.get("observed_in")):
            raise CatalogError(f"correction sans motif ni observation : {entry['id']}")
        étendue = entry["code_signature"]
        if tracker.code_signature not in étendue:
            raise CatalogError(
                f"la correction du pisteur {tracker.id} ({tracker.name}) ne contient pas "
                f"la signature d'origine : une correction ajoute, elle ne remplace pas")
        corrigés[tracker.id] = Tracker(tracker.id, tracker.name, étendue)
    appliquées = len(doc.get("corrections") or []) - len(orphelines)
    return sorted(corrigés.values(), key=lambda t: t.id), appliquées, orphelines


@dataclass
class Detection:
    tracker_ids: list[int]
    broken: list[tuple[int, str]]


def detect(class_names: Iterable[str], trackers: Iterable[Tracker]) -> Detection:
    """Les pisteurs dont la signature apparaît dans les noms de classes.

    La recherche porte sur les noms joints par des sauts de ligne, comme chez Exodus :
    une signature du type `com.google.android.gms.ads` doit pouvoir matcher au milieu
    d'un nom complet, pas seulement en préfixe exact.

    Une signature qui n'est pas une regex valide est consignée et sautée. Le catalogue
    est de la donnée tierce : une entrée cassée ne doit pas faire échouer l'analyse
    entière, mais elle ne doit pas non plus disparaître en silence.
    """
    haystack = "\n".join(sorted(set(class_names)))
    found: list[int] = []
    broken: list[tuple[int, str]] = []
    for tracker in trackers:
        try:
            if re.search(tracker.code_signature, haystack):
                found.append(tracker.id)
        except re.error as exc:
            broken.append((tracker.id, str(exc)))
    return Detection(sorted(found), broken)
