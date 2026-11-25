"""Projection des faits vers les fiches du site. Pur : entre des dicts, sort des dicts.

Décision 6 du design : le dépôt privé détient la vérité, il **dérive** une projection
`compare/*.json` pour le site. Ce module ne touche pas au disque — il décide ce qu'une
fiche devrait porter, la CLI lit, écrit, et laisse le `git diff` du site à la revue
humaine. Même ligne de fracture que `gitlog` / `backfill_git`.

Le sens de la dépendance compte : le site est aujourd'hui la *source* du backfill git.
Projeter, c'est donc écrire chez celui dont on a lu l'histoire, et la moindre différence
de format ou d'ordre y ressemble à un changement de fait. Tout ce module tourne autour de
ça — ne réécrire que ce qui a réellement bougé.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

# Les seuls champs que le dépôt possède. Tout le reste appartient au site et n'est jamais
# touché : `slug_playstore` sur 95 fiches, et les champs hérités du vieux scraper Exodus
# — `uaid`, `apk_hash`, `created`, `downloads`… — sur trois. Les écraser ferait perdre au
# site des données que ce dépôt ne sait pas reproduire.
OWNED = ("version", "report_date", "permissions", "trackers")

# Les listes dont un réordonnancement n'est pas un changement. Mesuré sur les 131 fiches :
# 80 portent leurs pisteurs dans l'ordre d'Exodus et 28 leurs permissions dans un ordre
# quelconque. Réécrire pour trier produirait un diff de revue de 80 fichiers où aucun fait
# n'a bougé, et le vrai signal s'y perdrait.
SETS = ("permissions", "trackers")


def site_date(value: str) -> str:
    """Un instant ISO -> le format des fiches : UTC, millisecondes, suffixe `Z`.

    Les fiches portent `2026-04-18T16:17:00.000Z`. Nos snapshots portent le fuseau du
    committeur pour `exodus-git` — `+01:00` et `+02:00` cohabitent — et de l'UTC ailleurs.
    Écrire ces chaînes telles quelles mélangerait deux formats dans un même répertoire et
    ferait diverger une fiche de sa voisine pour une raison qui n'est pas un fait.
    """
    at = datetime.fromisoformat(value).astimezone(timezone.utc)
    return f"{at:%Y-%m-%dT%H:%M:%S}.{at.microsecond // 1000:03d}Z"


def fields_for(snap: dict) -> dict:
    """Les quatre champs possédés, tirés d'un snapshot.

    `report_date` retombe sur `observed_at` quand le snapshot n'en porte pas : c'est le
    cas de toute analyse statique, qui n'est le rapport de personne. La date publiée reste
    donc celle de la mesure, jamais une invention.

    Les pisteurs sont publiés en ids nus, comme le site les attend. Leur nom vient de son
    propre `trackers.json` ; le republier ici en figerait une copie qui divergerait.
    """
    return {
        "version": snap["version_name"],
        "report_date": site_date(snap.get("report_date") or snap["observed_at"]),
        "permissions": sorted(set(snap["permissions"])),
        "trackers": sorted({t["id"] for t in snap["trackers"]}),
    }


def _version_tuple(value: str | None) -> tuple[int, ...]:
    """La version en nombres, pour comparer `23.6.0` à `22.4.1` sans se tromper."""
    return tuple(int(n) for n in re.findall(r"\d+", value or ""))


def fait_foi(series: list[dict]) -> dict:
    """Le point qui fait foi : la **version la plus récente**, pas la dernière observée.

    La série est ordonnée par date d'observation, et ces deux ordres ont cessé de
    coïncider le jour où `run --depth` a remonté le temps : une 1.107.0 téléchargée
    aujourd'hui arrive *après* la 1.124.0 vue en juin, et la fiche du site publiait alors
    les permissions d'une version vieille d'un an sous la date du jour.

    C'est la troisième fois que le même piège se tend au même endroit — `evidence.load_all`
    et `publish._static_snapshot` l'ont corrigé chacun de leur côté, tous deux le
    2026-09-11. Partout où une série est ordonnée par date de collecte, « le dernier »
    n'est pas « le plus récent » dès qu'on collecte de l'ancien.

    La date d'observation, puis la position dans la série, départagent : deux analyses
    d'une même version portent le même numéro, et c'est la dernière des deux qui fait foi
    — la position garde le comportement d'avant sur une série qui n'a jamais reculé, où
    les deux ordres coïncident.
    """
    return max(enumerate(series),
               key=lambda pair: (_version_tuple(pair[1].get("version_name")),
                                 pair[1].get("observed_at") or "", pair[0]))[1]


def projection(series: list[dict]) -> tuple[dict | None, str | None]:
    """Les champs à projeter pour une série, ou un motif de refus.

    La série est attendue triée du plus ancien au plus récent — le tri temporel appartient
    à l'appelant, qui a déjà `diff._sort_key` sous la main. Le point publié est choisi par
    `fait_foi`, sur la version et non sur la position.

    **C'est le point qui fait foi qui est publié, et un point incomplet fait refuser
    l'application entière.** Un snapshot marqué `trackers_not_analyzed` porte une liste de
    pisteurs vide qui veut dire « pas cherché » ; la publier écrirait « aucun pisteur » sur
    la fiche. C'est exactement le défaut que le design reproche aux données existantes —
    22 fiches à `trackers: []`, dont `gmail.json`. Reculer d'un point pour retrouver des
    pisteurs ne vaut pas mieux : la fiche publierait alors des permissions et des pisteurs
    de deux versions différentes sous une seule date.

    Une liste vide *sans* ce drapeau est en revanche un fait — Exodus n'a rien trouvé — et
    se projette normalement. C'est toute la raison d'être du drapeau.
    """
    if not series:
        return None, "no_snapshot"
    snap = fait_foi(series)
    if "trackers_not_analyzed" in (snap.get("quality_flags") or []):
        return None, "trackers_not_analyzed"
    return fields_for(snap), None


def hold_removals(existing: dict | None, fields: dict,
                  series: list[dict]) -> tuple[dict, list[int]]:
    """Ne retirer un pisteur d'une fiche que si son absence est comparable à sa présence.

    Le cas mesuré le 2026-09-10 : Vinted passe de 27 pisteurs (rapport Exodus, catalogue
    inconnu) à 18 (notre analyse). `diff` marque ce delta `catalog_unknown` et
    `exploitable: false` — il refuse d'affirmer le changement. Publier l'état revient
    pourtant à l'affirmer sur le site : la fiche perdrait neuf noms, et un lecteur y
    lirait « Vinted a retiré neuf pisteurs ».

    **Ajouter et retirer ne sont pas symétriques.** Un pisteur trouvé dans le binaire est
    un fait : ses classes sont là, on les nomme. Un pisteur absent de *notre* analyse ne
    prouve pas son absence de l'application — il peut vivre dans un module livré à la
    demande que le miroir ne sert pas, ou porter un nom qu'aucune signature ne couvre,
    ce que le catalogue périmé de Vungle a démontré le même jour.

    La règle est donc celle du moteur de diff, appliquée pisteur par pisteur : **on ne
    retire que ce dont la dernière présence connue est comparable à l'absence
    d'aujourd'hui**, c'est-à-dire mesurée avec le même catalogue. Un pisteur vu pour la
    dernière fois dans un rapport Exodus — catalogue inconnu — reste sur la fiche.

    Première formulation, écartée le jour même : « deux analyses statiques consécutives,
    même catalogue ». Elle tenait sur Vinted et se cassait sur Grindr, où nos deux
    analyses statiques trouvent 19 pisteurs quand la fiche en porte 31 — dont une sur la
    version 26.2.0 qu'Exodus avait scannée lui-même. Deux mesures concordantes chez nous
    ne disent rien de la troisième : l'écart n'est pas un retrait dans le temps, c'est un
    désaccord sur la même version, et il se règle en confrontant les analyseurs, pas en
    republiant.
    """
    publiés = set(fields["trackers"])
    connus = {int(t) for t in (existing or {}).get("trackers") or []}
    retirés = connus - publiés
    if not retirés:
        return fields, []

    # Le même point que celui publié : comparer les pisteurs d'une fiche au catalogue
    # d'une version qu'on ne publie pas mélangerait deux mesures.
    référence = fait_foi(series).get("catalog_version") if series else None
    retenus = []
    for tracker in sorted(retirés):
        porteur = _dernier_porteur(series, tracker)
        comparable = (porteur is not None and référence is not None
                      and porteur.get("catalog_version") == référence)
        if not comparable:
            retenus.append(tracker)
    if not retenus:
        return fields, []
    return {**fields, "trackers": sorted(publiés | set(retenus))}, retenus


def _dernier_porteur(series: list[dict], tracker: int) -> dict | None:
    """Le point le plus récent qui portait ce pisteur, s'il y en a un dans la série.

    Aucun : le pisteur ne vient pas de nous — Exodus l'a détecté par signature réseau,
    ou la fiche est plus vieille que notre plus vieux point. Il reste sur la fiche, et
    c'est le bon comportement : sa place y vient d'une mesure qu'on ne sait pas refaire.
    """
    for snap in reversed(series):
        if any(t["id"] == tracker for t in snap["trackers"]):
            return snap
    return None


def site_ahead(existing: dict | None, fields: dict) -> bool:
    """La fiche du site est-elle plus récente que ce qu'on s'apprête à écrire ?

    Le site est la source du backfill git : sa date est normalement antérieure ou égale à
    la nôtre. Elle peut nous dépasser si `update-exodus.py` a tourné là-bas depuis notre
    dernier backfill. Écraser une fiche plus récente ferait *reculer* le site, et le ferait
    sans bruit : le `git diff` de revue montrerait un changement, pas une régression, et
    personne ne remarquerait qu'on vient de republier des faits périmés.
    """
    ancienne = (existing or {}).get("report_date")
    if not ancienne or not fields.get("report_date"):
        return False
    return _instant(ancienne) > _instant(fields["report_date"])


def apply_to(existing: dict | None, app_name: str, handle: str, fields: dict) -> dict:
    """La fiche telle qu'elle devrait être : les champs possédés, le reste intact.

    L'ordre des clés de la fiche existante est conservé — quatre ordres différents
    cohabitent sur le site — et un champ absent s'ajoute à la fin. Réordonner réécrirait le
    fichier entier, et un diff de revue « fichier entier changé » n'est pas lu.

    `app_name` n'est écrit qu'à la création. C'est le libellé d'affichage du site, et nos
    snapshots ne le portent que parce qu'ils l'y ont pris ; le republier annulerait un
    renommage fait là-bas.
    """
    doc = dict(existing) if existing else {"app_name": app_name, "handle": handle}
    for key in OWNED:
        if key in SETS and set(doc.get(key) or []) == set(fields[key]):
            continue
        doc[key] = fields[key]
    return doc


def changes(existing: dict | None, fields: dict) -> dict:
    """Ce que la projection déplacerait, pour le rapport de run et la lecture à l'œil."""
    avant = existing or {}
    perms, trackers = set(avant.get("permissions") or []), set(avant.get("trackers") or [])
    return {
        "version": _moved(avant.get("version"), fields["version"]),
        "report_date": _moved(avant.get("report_date"), fields["report_date"]),
        "permissions": {"added": sorted(set(fields["permissions"]) - perms),
                        "removed": sorted(perms - set(fields["permissions"]))},
        "trackers": {"added": sorted(set(fields["trackers"]) - trackers),
                     "removed": sorted(trackers - set(fields["trackers"]))},
    }


def dumps(doc: dict) -> str:
    """La sérialisation du site, mesurée sur ses 131 fiches : indentation 2, unicode non
    échappé, **aucun saut de ligne final**.

    Le format n'est pas un détail de confort. C'est lui qui fait qu'un `git diff` de revue
    montre les permissions ajoutées une par une, au lieu d'une seule ligne de 2 Ko
    remplacée par une autre — auquel cas la validation humaine promise par le design
    n'aurait plus rien à valider.
    """
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _moved(avant, après):
    return None if avant == après else [avant, après]


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)
