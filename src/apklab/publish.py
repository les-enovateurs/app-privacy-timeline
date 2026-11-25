"""Projection publique du dataset : ce qui traverse vers le site, et rien d'autre.

`export` assemble la vérité privée ; ce module en retire une **réduction volontaire**.
Les deux se lisent ensemble, et trois partis pris les séparent.

**Liste blanche, jamais liste noire.** Un champ que ce module ne nomme pas n'est pas
publié. C'est la seule propriété de sûreté qui survive au temps : un nouveau champ dans
le store — une empreinte de certificat, un chemin de fichier, le nom d'une machine de
collecte — arrive par défaut du bon côté de la frontière. Une liste noire aurait publié
tout ce que personne n'a pensé à interdire.

**Chaque tranche est réduite au fait opposable.** Le piège n'est pas le volume publié,
c'est la phrase qu'un lecteur pourra en tirer. « Cette application contacte 173
domaines » serait faux deux fois : ces hôtes sont pour la plupart de la documentation,
des gabarits et des identifiants de code, et l'analyse est statique — elle relève un nom
dans un binaire, elle ne voit passer aucune requête. Ne traversent que les hôtes classés
`endpoint` et non premier parti, et chaque bloc porte la base de sa mesure.

**Un delta inexploitable ne traverse pas.** `diff` pose ses drapeaux pour que son
consommateur puisse refuser un bloc ; ici on les lit. Un retrait de pisteurs mesuré
contre deux catalogues différents se lirait « cette application a retiré trois
pisteurs » alors que le fait est « le catalogue a changé ». C'est la dette 9 du design,
et l'étage de publication est l'endroit où elle se paie.

La sortie est destinée à un dépôt de fichiers sur le serveur de déploiement, hors de
tout dépôt git : le privé reste la vérité, le site n'en dérive qu'une projection qu'il
reconstruit à chaque build.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from . import endpoints as endpoints_mod

# Version du format public, indépendante de celle du dataset privé : la réduction peut
# changer de forme sans que la jointure bouge, et l'inverse. Le site refuse un document
# dont le numéro ne lui est pas connu, plutôt que d'en lire un champ absent en silence.
PUBLIC_DATASET_VERSION = 1

MANIFEST_NAME = "manifest.json"
APPS_RELDIR = "apps"

# Les mesures d'une analyse statique. C'est la seule tranche du dataset qu'aucune source
# publique ne porte — ni Exodus ni le git du site ne connaissent le poids d'un APK — et
# c'est aussi celle qui n'engage personne : elle décrit un binaire, pas un comportement.
STATIC_FIELDS = (
    "version_name", "report_date", "observed_at", "apk_sha256",
    "total_bytes", "dex_bytes", "res_bytes", "assets_bytes", "native_bytes",
    "size_scope", "dex_method_count", "min_sdk", "native_abis",
    # The site shows these in place of an Exodus report older than our analysis. A
    # tracker count means nothing without the catalogue it was searched against.
    "permissions", "trackers", "catalog_version", "acquisition",
)

# L'histoire du poids, version par version : la seule série qui traverse la frontière,
# et elle ne porte que des mesures de taille. Ni permissions ni pisteurs — le site tient
# déjà les siens, de sa propre source, et deux historiques pour un même fait feraient
# deux vérités. Le poids, lui, n'existe nulle part ailleurs : une fiche qui ne reçoit
# que le dernier point peut dire « 136 Mo » mais jamais « le double d'il y a deux ans ».
SIZE_HISTORY_FIELDS = (
    "version_name", "observed_at", "total_bytes", "dex_bytes", "res_bytes",
    "assets_bytes", "native_bytes", "dex_method_count", "size_scope", "reference_bytes",
)

# Ce qu'un hôte publie de lui-même. `apps` est écarté : la liste des handles du corpus
# n'apprend rien de plus que `app_count` au lecteur d'une fiche, et la recopier dans 130
# documents ferait porter le corpus entier par chacun. `owner_packages` reste, parce que
# c'est lui qui rend le fait opposable — le package qui porte la marque du destinataire.
HOST_FIELDS = ("host", "app_count", "owner_packages", "owner_package_count")


def _version_tuple(value: str | None) -> tuple[int, ...]:
    """La version en nombres, pour comparer `23.6.0` à `22.4.1` sans se tromper."""
    return tuple(int(n) for n in re.findall(r"\d+", value or ""))


def _static_snapshot(snapshots: list[dict]) -> dict | None:
    """L'analyse statique de la **version la plus récente**, pas la dernière observée.

    `latest` du dataset privé peut être un rapport Exodus, qui ne porte ni poids ni
    nombre de méthodes. Publier ses champs absents donnerait un `dex_bytes: null` qu'une
    interface afficherait « 0 octet » — un fait faux là où il n'y a pas de mesure.

    Le choix se fait sur la version et non sur la position dans la série, qui est
    ordonnée par date d'observation. Remonter une version ancienne la place en dernier —
    elle vient d'être téléchargée — et la fiche publiait alors son poids comme la mesure
    courante de l'application. Constaté en production le 2026-09-11 : Carrefour affichait
    142 Mo pour la 22.4.1 d'il y a un an, quand la 23.6.0 en vigueur en pèse 163.

    C'est la même erreur que celle corrigée dans `evidence.load_all`, à un deuxième
    endroit : partout où une série est ordonnée par date de collecte, « le dernier » n'est
    pas « le plus récent » dès qu'on remonte le temps.
    """
    statiques = [s for s in snapshots if s.get("source") == "apk-static"]
    if not statiques:
        return None
    last = max(statiques, key=lambda s: (_version_tuple(s.get("version_name")),
                                         s.get("observed_at") or ""))
    out = {k: last.get(k) for k in STATIC_FIELDS if last.get(k) is not None}
    # An empty list here would read "no tracker" when the fact is "not searched".
    if "trackers_not_analyzed" in (last.get("quality_flags") or []):
        out.pop("trackers", None)
    return out


def _size_history(snapshots: list[dict]) -> list[dict]:
    """La suite des poids measured, une ligne par version, de la plus ancienne à la plus
    récente.

    Trois règles, et chacune évite une phrase fausse sur une fiche.

    **Une version, un point.** Une même version réanalysée deux fois donne deux mesures
    presque égales ; les tracer toutes les deux dessinerait une marche là où il n'y a
    qu'une reprise de collecte. Le point gardé est la dernière observation de la version.

    **Un point sans poids n'est pas un point.** Un rapport Exodus ne pèse rien : il
    entrerait dans la series comme un zéro, et une courbe le lirait comme un
    effondrement.

    **A variant change is declared.** APKPure serves a single-ABI APK one day and a
    four-ABI XAPK the next: the size doubles without a line of code changing. The ABI set
    and `size_scope` are compared point to point, and `variant_change` marks the point
    that doesn't compare to the previous one. `abi_count` alone missed arm64 → armeabi-v7a.
    """
    measured = [s for s in snapshots
               if s.get("source") == "apk-static" and s.get("total_bytes")]
    by_version: dict[str, dict] = {}
    for snap in sorted(measured, key=lambda s: s.get("observed_at") or ""):
        by_version[snap.get("version_name") or ""] = snap

    series: list[dict] = []
    previous: dict | None = None
    previous_abis: set[str] = set()
    for snap in sorted(by_version.values(),
                       key=lambda s: (_version_tuple(s.get("version_name")),
                                      s.get("observed_at") or "")):
        point = {k: snap.get(k) for k in SIZE_HISTORY_FIELDS if snap.get(k) is not None}
        abis = set(snap.get("native_abis") or [])
        point["abi_count"] = len(abis)
        if previous is not None and (abis != previous_abis
                                      or point.get("size_scope") != previous.get("size_scope")):
            point["variant_change"] = True
        series.append(point)
        previous, previous_abis = point, abis
    return series


def _public_delta(delta: dict) -> dict | None:
    """Un delta réduit à ses blocs exploitables, ou rien s'il n'en reste aucun.

    Le bloc permissions est toujours exploitable : il est lu du manifeste de l'APK et
    non d'un catalogue, donc il traverse un changement de source. Les deux autres se
    déclarent eux-mêmes, et un bloc qui se déclare inexploitable ne se publie pas.

    Deux refus s'ajoutent ici, que le dataset privé n'a pas à faire.

    **Une série qui recule ne se publie pas du tout.** `diff` gate le poids sur
    `version_regressed` mais laisse le bloc permissions exploitable, à raison : la
    comparaison de manifestes est valide dans les deux sens. Ce qui ne l'est pas, c'est
    la phrase qu'une fiche en tire. Un delta `26.15.1 → 26.2.0` afficherait « permission
    retirée » là où le fait est « la source a servi un APK plus ancien ». Le privé garde
    les deux sens, parce qu'il décrit la collecte ; le site n'affiche que ce qui avance.

    **Un poids qui n'a pas bougé n'est pas un fait.** Deux analyses d'une même version
    produisent un bloc `size` entier dont tous les écarts valent zéro. `has_change` le
    déclare vrai parce que le bloc existe, et une fiche afficherait un changement sans
    rien dedans.
    """
    if "version_regressed" in (delta.get("series_flags") or []):
        return None

    out: dict[str, Any] = {}
    perms = delta.get("permissions") or {}
    if perms.get("added") or perms.get("removed"):
        out["permissions"] = {"added": list(perms.get("added") or []),
                              "removed": list(perms.get("removed") or [])}

    trackers = delta.get("trackers") or {}
    if trackers.get("exploitable") and (trackers.get("added") or trackers.get("removed")):
        out["trackers"] = {
            "added": [{"id": t["id"], "name": t.get("name")} for t in trackers["added"]],
            "removed": [{"id": t["id"], "name": t.get("name")} for t in trackers["removed"]],
        }

    size = delta.get("size")
    if size and size.get("exploitable"):
        écarts = {k: size[k] for k in size if k.endswith("_delta")}
        if any(écarts.values()):
            out["size"] = {**écarts, "size_scope": size.get("size_scope")}

    reference = (size or {}).get("reference") or {}
    if reference.get("exploitable") and reference.get("reference_bytes_delta"):
        out["size_reference"] = {"reference_bytes_delta": reference["reference_bytes_delta"],
                                 "size_scope": size.get("size_scope")}

    if not out:
        return None
    # La fenêtre, jamais une date : les runs sont manuels et irréguliers, et affirmer un
    # jour précis serait une invention. Règle 1 du moteur de diff, conservée telle quelle
    # jusqu'au site — c'est la seule façon d'écrire une phrase vraie sous un graphe.
    out["window"] = {"from": delta["window"]["from"], "to": delta["window"]["to"]}
    out["from_version"] = delta.get("from_version")
    out["to_version"] = delta.get("to_version")
    return out


def _linear(deltas: list[dict]) -> list[dict]:
    """Un seul delta par point de départ, celui qui va le plus loin.

    La série n'est pas linéaire : deux sources peuvent partir du même point et arriver à
    deux versions différentes. Grindr en donne le cas — `26.2.0 → 26.13.0` par le rapport
    Exodus et `26.2.0 → 26.15.1` par l'analyse statique — et les deux blocs portent le
    même ajout de permissions. Publier les deux fait lire deux fois le même fait, et
    laisse croire à deux ajouts.

    La règle retenue est la plus étroite qui reste vraie : à départ égal, le delta qui
    atteint la version la plus récente est celui qui décrit l'état courant. Les autres ne
    sont pas faux, ils sont redondants — et le dataset privé les garde, parce que lui
    décrit la collecte et non l'application.

    Ce n'est pas une fusion : rien n'est recalculé, un delta est gardé tel quel ou écarté.
    """
    def rang(d: dict) -> tuple:
        return (d["window"]["to"], d.get("to_version") or "")

    par_départ: dict[str, dict] = {}
    for d in deltas:
        clé = d.get("from_version") or d["window"]["from"]
        gardé = par_départ.get(clé)
        if gardé is None or rang(d) > rang(gardé):
            par_départ[clé] = d
    return sorted(par_départ.values(), key=lambda d: d["window"]["to"])


def _public_endpoints(hosts: Iterable[dict]) -> dict:
    """Les hôtes tiers relevés dans le binaire, et le compte de ce qui a été écarté.

    **Relevés, pas contactés.** L'analyse est statique : elle prouve qu'un nom de domaine
    est présent dans le binaire distribué, jamais qu'une requête est partie. « Cette
    application contacte 130 domaines » resterait faux même après le tri, et c'est
    pourquoi `basis` voyage avec la donnée plutôt que dans une note de bas de page — un
    consommateur qui ne lit que `third_party_count` doit trouver la réserve dans le même
    objet.

    Le compte des écartés est publié avec eux : sans lui, un lecteur qui compare à un
    autre travail sur le même binaire croirait à une mesure incomplète, alors que c'est
    un tri. Publier le tri sans son volume serait moins honnête que ne rien publier.
    """
    hosts = list(hosts)
    tiers = endpoints_mod.third_party(hosts)
    return {
        "basis": "strings-in-binary",
        "third_party_count": len(tiers),
        "hosts": [{k: h[k] for k in HOST_FIELDS if k in h} for h in tiers],
        "excluded_count": len(hosts) - len(tiers),
    }


def _public_accounts(accounts: Iterable[dict]) -> dict:
    """Les familles de SDK dont un compte a été relevé, jamais le compte lui-même.

    Le dataset privé porte un préfixe de six caractères et une empreinte tronquée : rien
    de secret, tout vient du binaire distribué. Mais un compte nomme un sous-traitant par
    éditeur, et le publier est un geste que ce module ne prend pas. Reste la famille —
    « cette application porte une clé Branch » — qui dit la même chose au lecteur d'une
    fiche sans nommer le projet de personne.
    """
    tally: dict[str, int] = {}
    for account in accounts:
        tally[account["id"]] = tally.get(account["id"], 0) + 1
    return {"by_sdk": dict(sorted(tally.items()))}


def _public_candidates(candidates: Iterable[dict]) -> dict:
    """Des compteurs, aucun nom de package.

    Deux statuts existent aujourd'hui : `rejected`, signé, et `pending`, que personne n'a
    relu. Publier un `pending` nommerait un éditeur sur une preuve non relue, ce qui
    n'est pas défendable. Publier un `rejected` par son nom ne l'est pas davantage pour
    une autre raison : « ce package a été examiné et écarté » se lit comme une accusation
    retirée, alors que le fait est qu'il n'y avait rien à dire. Restent les compteurs,
    qui décrivent la méthode et non un tiers. Le jour où un statut `accepted` existera,
    signé, ses noms auront leur place ici — c'est le seul cas qui apprendrait au lecteur
    quelque chose que le catalogue Exodus ne porte pas déjà.
    """
    by_status: dict[str, int] = {}
    for cand in candidates:
        by_status[cand["status"]] = by_status.get(cand["status"], 0) + 1
    return {"examined": sum(by_status.values()), "by_status": dict(sorted(by_status.items()))}


def public_document(doc: dict) -> dict:
    """La projection publique d'un document du dataset privé.

    Prend la sortie de `export`, pas le store : la jointure se fait une fois, et les deux
    projections — la fiche du site par `project`, ce dataset-ci — dérivent du même
    document. Un chiffre qui divergerait entre les deux serait un bug visible, pas une
    seconde vérité.
    """
    latest = doc.get("latest") or {}
    return {
        "dataset_version": PUBLIC_DATASET_VERSION,
        "slug": doc["slug"],
        "handle": doc["handle"],
        "app_name": doc["app_name"],
        "latest": {"version_name": latest.get("version_name"),
                   "report_date": latest.get("report_date"),
                   "source": latest.get("source")},
        "static": _static_snapshot(doc.get("snapshots") or []),
        "size_history": _size_history(doc.get("snapshots") or []),
        "deltas": _linear([d for d in (_public_delta(x) for x in doc.get("deltas") or []) if d]),
        "endpoints": _public_endpoints(doc.get("endpoints") or []),
        "sdk_accounts": _public_accounts(doc.get("accounts") or []),
        "tracker_candidates": _public_candidates(doc.get("candidates") or []),
    }


def dumps(doc: dict) -> str:
    """Sérialisation stable : clés triées, une ligne finale, les mêmes octets que
    `export`. Deux passes sur un même état doivent produire des fichiers identiques,
    sinon la porte d'idempotence côté CI republierait à chaque tour."""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def index_entry(doc: dict, relpath: str, texte: str) -> dict:
    """La ligne du manifeste qui décrit un document publié.

    Elle porte l'empreinte du fichier, ce que le manifeste privé ne fait pas : là-bas le
    dataset et son index sont écrits ensemble sur le même disque, ici ils traversent un
    réseau et se font recopier par un script qui n'a pas produit la donnée. Le site
    revérifie chaque empreinte avant d'hydrater, et refuse le lot entier si une seule
    diverge — servir la moitié d'un dataset serait le servir comme s'il était complet.
    """
    return {"slug": doc["slug"], "handle": doc["handle"], "app_name": doc["app_name"],
            "version": doc["latest"]["version_name"], "file": relpath,
            "sha256": hashlib.sha256(texte.encode("utf-8")).hexdigest()}


def manifest(*, generated_at: str, apps: list[dict], catalog_version: str | None) -> dict:
    """L'index de la projection publique.

    `catalog_version` est publié parce qu'un nombre de pisteurs ne veut rien dire sans
    lui, et parce que c'est ce qui permet à un lecteur de rejouer la mesure six mois plus
    tard. Rien d'autre du privé ne traverse : ni empreinte de source, ni compte de
    snapshots, ni liste des applications écartées — un `skipped` publié dirait quelles
    applications sont dans le corpus sans y être analysées, ce qui est une information
    sur le plan de collecte et non sur les applications.
    """
    return {
        "dataset_version": PUBLIC_DATASET_VERSION,
        "visibility": "public",
        "generated_at": generated_at,
        "catalog_version": catalog_version,
        "app_count": len(apps),
        "apps": apps,
    }


def read_manifest(path: Path) -> dict | None:
    """Le manifeste déjà en place, ou rien. Sert la comparaison d'idempotence."""
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
