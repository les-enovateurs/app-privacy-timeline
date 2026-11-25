"""Corpus des applications suivies, seedé depuis les fiches de comparaison du site."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

COMPARE_RELDIR = "public/data/compare"
MANUAL_RELDIR = "public/data/manual"


@dataclass
class CorpusEntry:
    slug: str
    handle: str
    app_name: str
    source: str = "umd-compare"
    aliases: list[str] = field(default_factory=list)

    @property
    def compare_relpaths(self) -> list[str]:
        """Tous les fichiers du site qui décrivent ce handle, alias compris.

        Deux slugs peuvent pointer sur le même paquet — alibaba.json et aliexpress.json
        portent tous deux com.alibaba.aliexpresshd. Comme le store est clé par handle,
        leurs deux historiques appartiennent à la même série ; n'en lire qu'un perdrait
        purement et simplement l'autre.
        """
        return [f"{COMPARE_RELDIR}/{slug}.json" for slug in [self.slug, *self.aliases]]


def seed(site_repo: Path) -> tuple[list[CorpusEntry], list[dict]]:
    """Parcourt public/data/compare/*.json et en tire {slug, handle, app_name}.

    Le corpus ne peut pas venir de services.json : son champ exodus est un booléen à
    true pour 124 services sur 135, sans aucune valeur d'identification.

    Deux filtres. Un fichier sans champ handle n'est pas une application mais un
    catalogue — breaches, permissions, trackers, dpo-directory... — et sort du corpus
    sans erreur. Un handle déjà pris devient un alias : le corpus est clé par handle
    parce que le store l'est aussi, et deux slugs pointant sur le même handle
    fusionneraient deux histoires distinctes dans une même série. Cas réel :
    alibaba.json et aliexpress.json portent tous deux com.alibaba.aliexpresshd.
    """
    entries: dict[str, CorpusEntry] = {}
    issues: list[dict] = []
    for path in sorted((site_repo / COMPARE_RELDIR).glob("*.json")):
        slug = path.stem
        try:
            doc = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            issues.append({"slug": slug, "reason": "invalid_json", "detail": str(exc)})
            continue
        if not isinstance(doc, dict) or not doc.get("handle"):
            continue
        handle = doc["handle"]
        if handle in entries:
            entries[handle].aliases.append(slug)
            issues.append({"slug": slug, "reason": "duplicate_handle",
                           "detail": f"{handle} déjà porté par {entries[handle].slug}"})
            continue
        entries[handle] = CorpusEntry(slug=slug, handle=handle,
                                      app_name=doc.get("app_name") or slug)
    # Draft and newly published fiches name their app before any compare file exists:
    # without them the watch never fetches the first version.
    for path in sorted((site_repo / MANUAL_RELDIR).glob("*.json")):
        try:
            doc = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            issues.append({"slug": path.stem, "reason": "invalid_json", "detail": str(exc)})
            continue
        handle = ((doc.get("playstore") or {}).get("id") if isinstance(doc, dict) else None)
        if handle and handle not in entries:
            entries[handle] = CorpusEntry(slug=path.stem, handle=handle,
                                          app_name=doc.get("name") or path.stem,
                                          source="umd-fiche")
    return list(entries.values()), issues


def merge(seeded: list[CorpusEntry], existing: list[CorpusEntry]) -> list[CorpusEntry]:
    """Seeded entries plus the hand-added ones the site has no compare file for.

    An existing entry keeps its source: the AFP toys were added by hand before their
    compare file existed, and re-seeding must not rewrite where they came from.
    """
    by_handle = {e.handle: e for e in existing}
    merged = [CorpusEntry(**{**asdict(e), "source": by_handle[e.handle].source})
              if e.handle in by_handle else e for e in seeded]
    seeded_handles = {e.handle for e in seeded}
    return merged + [e for e in existing if e.handle not in seeded_handles]


def write(entries: list[CorpusEntry], path: Path) -> None:
    payload = [asdict(e) for e in sorted(entries, key=lambda e: e.slug)]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")


def load(path: Path) -> list[CorpusEntry]:
    return [CorpusEntry(**e) for e in json.loads(path.read_text("utf-8"))]


def by_slug(entries: list[CorpusEntry]) -> dict[str, CorpusEntry]:
    return {e.slug: e for e in entries}


def app_list(path: Path, entries: list[CorpusEntry]) -> tuple[list[CorpusEntry], list[str]]:
    """Les entrées désignées par un fichier de slugs. Rend aussi les slugs inconnus.

    Un slug par ligne, `#` commente une ligne ou une fin de ligne, les lignes vides sont
    ignorées. Un fichier plutôt qu'une suite de --app parce qu'il se commit, se relit et
    se rejoue à l'identique : une passe à échelle réduite n'a d'intérêt que si on peut
    dire exactement sur quoi elle a porté.

    L'ordre du fichier est celui du run et n'est pas trié. Un run qui télécharge dure des
    dizaines de minutes ; s'il est interrompu, il doit avoir couvert ce que l'humain a mis
    en tête de liste. Un slug répété n'est rendu qu'une fois — le retélécharger coûterait
    155 Mo pour rien.

    Les inconnus sont rendus plutôt que levés : c'est à l'appelant de refuser toute la
    liste avant la première requête, pas de s'arrêter à la première ligne fautée.
    """
    index = by_slug(entries)
    chosen: list[CorpusEntry] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for line in path.read_text("utf-8").splitlines():
        slug = line.split("#", 1)[0].strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        if slug in index:
            chosen.append(index[slug])
        else:
            unknown.append(slug)
    return chosen, unknown
