"""Dataset dérivé : un document par application, assemblé depuis ce qui est versionné.

Trois choses que ce module ne fait pas, et c'est ce qui le définit.

**Il ne recalcule rien.** Les hôtes, les comptes SDK et les candidats viennent des
documents déjà écrits — `endpoints.json`, `keys.json`, `candidates.json` — et non des
preuves. Deux raisons : `evidence/` est gitignoré et n'existe que sur le poste qui a
téléchargé les binaires, alors que les trois documents sont versionnés ; et un dataset
recalculé au vol pourrait porter d'autres chiffres que ceux que le dépôt publie, ce qui
ferait deux vérités pour un seul fait. Le dataset est donc une **jointure**, pas une
mesure.

**Il ne réduit rien.** Une ligne d'hôte ou de compte entre telle quelle, avec sa
prévalence sur tout le corpus et ses drapeaux. Un consommateur qui ne verrait que la
part d'une application lirait « un hôte » là où le fait est « dix applications parlent à
cet hôte », et c'est la prévalence qui est opposable.

**Il ne décide pas de la diffusion.** Décision 12 : la sortie va dans le dépôt privé.
Ce qui traverse la frontière vers le site est un choix explicite, fait ailleurs, et
`--allow-outside` est la seule façon d'écrire hors du dépôt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

# Version du format. Un consommateur qui lit ce dataset doit pouvoir refuser un
# document dont la forme a changé, plutôt que d'en tirer un champ absent en silence.
DATASET_VERSION = 1

MANIFEST_NAME = "manifest.json"
APPS_RELDIR = "apps"


def handles_of(row: dict) -> set[str]:
    """Les handles cités par une ligne d'un document de corpus.

    Les trois documents ne portent pas `apps` de la même façon : `endpoints.json` et
    `keys.json` listent des handles nus, `candidates.json` des objets qui portent aussi
    le compte de classes vues dans cette application. Normaliser ici évite d'imposer
    aux trois producteurs un format commun qu'aucun d'eux n'a besoin d'avoir.
    """
    return {app["handle"] if isinstance(app, dict) else app
            for app in row.get("apps") or []}


def slice_for(handle: str, rows: Iterable[dict]) -> list[dict]:
    """Les lignes d'un document de corpus qui concernent une application."""
    return [row for row in rows if handle in handles_of(row)]


def app_document(entry, series: list[dict], deltas: list[dict], *,
                 hosts: list[dict], accounts: list[dict],
                 candidates: list[dict]) -> dict:
    """Le document d'une application : son histoire, et sa part des trois documents.

    `series` est attendue déjà triée dans le temps — c'est le store qui sait trier, pas
    ce module.

    `latest` est le dernier point tel quel, drapeaux de qualité compris, et non la
    projection publiable : un dataset privé n'a aucune raison de cacher un
    `trackers_not_analyzed` à son lecteur, il a au contraire besoin de le lui montrer.
    C'est la différence de fond avec `project`, qui refuse de publier un tel point
    parce qu'une fiche du site lirait sa liste vide comme « aucun pisteur ».
    """
    statiques = [s for s in series if s["source"] == "apk-static"]
    return {
        "dataset_version": DATASET_VERSION,
        "slug": entry.slug,
        "handle": entry.handle,
        "app_name": entry.app_name,
        "aliases": list(entry.aliases),
        "counts": {
            "snapshots": len(series),
            "static_analyses": len(statiques),
            "deltas": len(deltas),
            "endpoints": len(hosts),
            "accounts": len(accounts),
            "candidates": len(candidates),
        },
        "latest": series[-1] if series else None,
        "snapshots": series,
        "deltas": deltas,
        "endpoints": hosts,
        "accounts": accounts,
        "candidates": candidates,
    }


def digest(path: Path) -> dict[str, Any]:
    """Empreinte et taille d'un document de corpus, ou son absence déclarée.

    Le manifeste porte l'empreinte des trois documents qui ont alimenté la jointure :
    c'est la seule façon, pour qui relit le dataset six mois plus tard, de savoir de
    quelle passe viennent les tranches — un `candidates.json` recuré à la main ne change
    ni sa date ni son nombre d'entrées.
    """
    if not path.exists():
        return {"file": path.name, "present": False}
    raw = path.read_bytes()
    return {"file": path.name, "present": True, "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def manifest(*, generated_at: str, sources: list[dict], apps: list[dict],
             skipped: list[dict]) -> dict:
    """L'index du dataset. Un fichier de `apps/` absent d'ici est un reste périmé.

    Rien n'est supprimé de `apps/` : une application qui quitte le corpus y laisse son
    document, comme le store laisse ses lignes. Le manifeste est donc ce qui fait
    autorité sur le contenu du dataset, et c'est lui qu'un consommateur énumère.

    `skipped` est écrit et non tu : une application sans snapshot est un fait sur la
    collecte, et un dataset qui la ferait disparaître laisserait son lecteur croire que
    le corpus fait 130 entrées alors qu'il en fait 131.
    """
    return {
        "dataset_version": DATASET_VERSION,
        "generated_at": generated_at,
        # Décision 12, écrite dans la donnée et pas seulement dans la spec : ce dataset
        # n'est pas publié. Un consommateur qui le recopierait ailleurs le lit ici.
        "visibility": "private",
        "app_count": len(apps),
        "skipped_count": len(skipped),
        "totals": {
            key: sum(app["counts"][key] for app in apps)
            for key in ("snapshots", "static_analyses", "deltas",
                        "endpoints", "accounts", "candidates")
        },
        "sources": sources,
        "apps": apps,
        "skipped": skipped,
    }


def index_entry(doc: dict, relpath: str) -> dict:
    """La ligne du manifeste qui décrit un document d'application."""
    return {"slug": doc["slug"], "handle": doc["handle"], "file": relpath,
            "version": (doc["latest"] or {}).get("version_name"),
            "counts": doc["counts"]}


def dumps(doc: dict) -> str:
    """Sérialisation stable : clés triées, une ligne finale.

    Deux exports d'un même état doivent produire les mêmes octets, sinon la comparaison
    octet à octet qui évite de réécrire un fichier inchangé ne tiendrait pas — et le
    `git diff` du dépôt privé montrerait 131 documents changés à chaque passe.
    """
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
