"""Preuves d'un binaire : inventaire des packages du dex et domaines contactés.

Sorti du snapshot par décision d'architecture : l'inventaire d'une grosse application
pèse des centaines de kilo-octets, et une ligne JSONL de cette taille rendrait le store
illisible autant qu'impossible à relire ligne à ligne. Les preuves vivent donc à côté,
un fichier par (handle, version), et alimentent le seul étage qui en a besoin —
`candidates`.

Le module est pur : il reçoit des noms de classes et des chaînes déjà extraites du
binaire par `analyze`, et rend des dicts. Rien ici n'ouvre un APK, et toute la
reconnaissance de domaines se teste donc sans androguard.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

# Profondeur de départ de l'agrégat. `com.foo.bar` est le grain auquel un SDK se
# reconnaît : `com.foo` mélangerait tous les produits d'un éditeur, et un cran plus bas
# éclaterait un même SDK en dizaines de sous-packages qu'un tri manuel devrait recoller.
PREFIX_DEPTH = 3

# Au-delà de ce nombre de classes, un préfixe n'est plus un SDK mais un parapluie, et
# on descend d'un label. Mesuré sur Grindr 26.15.1 : à profondeur fixe,
# `com.google.android` emportait 15 408 des 83 284 classes — Play Services, AdMob,
# ExoPlayer et le transport de données dans un seul candidat, sous un préfixe qu'aucune
# signature d'Exodus ne peut couvrir puisqu'elles nomment toutes le produit
# (`com.google.android.gms.ads`) et non l'éditeur. Le catalogue ne soustrayait donc
# rien, et le premier candidat du corpus était un faux positif de 15 000 classes.
SPLIT_ABOVE = 250

# Plancher de la descente. Passé ce point, un dex obfusqué éclaterait en milliers de
# nœuds d'une classe — le fichier de preuves grossirait sans qu'un tri manuel y gagne.
MAX_DEPTH = 6

# Nombre de classes gardées par package. La preuve doit permettre de trancher à l'œil
# — « ces classes-là font-elles de la télémétrie ? » — pas de rejouer l'analyse.
SAMPLE = 5

# Une chaîne n'est un domaine que si son dernier label est un TLD connu. Sans ce
# garde-fou, tout nom de classe du dex — `com.foo.Bar` — passerait pour un hôte, et
# l'inventaire de domaines serait une copie bruitée de l'inventaire de packages.
# Liste volontairement courte : elle couvre ce que porte le corpus et se complète à la
# première preuve d'un manque, un TLD absent produisant un faux négatif visible et non
# une erreur silencieuse.
TLDS = frozenset("""
com net org io co app dev ai cloud me tv info biz online site xyz
fr de es it uk nl be ch pt pl se no dk fi cz ru ua tr gr ie at
us ca br mx ar cl jp cn kr in au nz za sg hk tw th vn id il ae
eu asia gg to cc ly sh gl st am fm cat mobi tech world goog google
""".split())

# Les racines de packages Java. Une chaîne nue qui commence par l'une d'elles est du
# code, pas un hôte : `androidx.core.app` finit sur `app`, qui est un TLD, et passerait
# sans ce garde-fou. Le prix est un faux négatif sur un sous-domaine qui commencerait
# par `com.` ou `net.` — on n'en a vu aucun, et un domaine perdu se rattrape par les
# URL, qui ne passent pas par ce filtre.
_PACKAGE_HEADS = frozenset(("com", "org", "net", "io", "android", "androidx", "kotlin",
                            "kotlinx", "java", "javax", "dalvik", "sun"))

# Un hôte : des labels séparés par des points, en minuscules. Les URL sont traitées
# à part, avant, parce qu'elles portent leur hôte sans ambiguïté.
_URL = re.compile(r"\b(?:https?|wss?)://([A-Za-z0-9.\-]{4,253})")
_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.){1,5}[a-z]{2,24}$")

# Les gabarits d'URL des SDK — `%s.example.com`, `{region}.example.com` — sont gardés
# en remplaçant le trou par une étoile plutôt qu'en les jetant : l'hôte réel se lit
# quand même, et c'est souvent la seule forme sous laquelle un endpoint apparaît.
_PLACEHOLDER = re.compile(r"%[sd@]|\{[a-zA-Z_]+\}|<[a-zA-Z_]+>")


def hosts(strings: Iterable[str]) -> list[str]:
    """Les hôtes reconnaissables dans les chaînes d'un dex, triés et dédoublonnés.

    Deux passes, dans cet ordre : l'hôte d'une URL passe le filtre de forme sans le
    filtre de package, une chaîne nue doit passer les deux. L'inversion donnerait
    `com.google.android.gms` pour un domaine — les noms de packages Java sont exactement
    des hôtes écrits à l'envers, et ils sont des dizaines de milliers dans un dex réel.

    **L'hôte d'une URL était retenu sans aucune condition jusqu'au 2026-09-10.** Mesuré
    sur les onze premières applications : `app`, `api`, `www`, `gdpr`, `ssrv`, `graph`,
    `login`, `payments`, `subscription` étaient enregistrés comme domaines, tous venus
    de bases d'URL construites par morceaux dans le code d'un SDK (`"https://" + host`,
    et Adjust concatène ses sous-domaines). Un label unique n'est pas un hôte : le
    filtre de forme s'applique donc aussi aux URL. Ce qu'il coûte est un hôte d'URL
    sous un TLD absent de `TLDS`, faux négatif visible ; ce qu'il évite est un dataset
    qui affirme qu'une application contacte « gdpr ».
    """
    found: set[str] = set()
    for raw in strings:
        if not raw or len(raw) > 253 or " " in raw:
            continue
        text = _PLACEHOLDER.sub("*", raw)
        url = _URL.search(text)
        if url:
            candidate = url.group(1).strip(".").lower()
            if _HOST.match(candidate) and candidate.split(".")[-1] in TLDS:
                found.add(candidate)
            continue
        candidate = text.strip(".").lower()
        if candidate.startswith("*."):
            candidate = candidate[2:]
        labels = candidate.split(".")
        if labels[0] in _PACKAGE_HEADS:
            continue
        if _HOST.match(candidate) and labels[-1] in TLDS:
            found.add(candidate)
    return sorted(found)


def packages(class_names: Iterable[str]) -> list[dict]:
    """Noms de classes -> préfixes de packages, comptés, avec un échantillon.

    Le grain n'est pas fixe, il s'adapte au poids : un préfixe qui porte plus de
    `SPLIT_ABOVE` classes descend d'un label, tant qu'il lui reste de la profondeur.
    Un grain uniforme ne peut pas convenir aux deux extrêmes du corpus — un SDK de 50
    classes sous `com.acme.sdk` et les 15 000 classes de `com.google.android`.

    Le dernier label d'un nom de classe est le nom de la classe et non un package :
    `com.foo.Bar` est compté sous `com.foo`. L'y laisser ferait de chaque classe d'un
    package peu profond son propre candidat.

    Une classe trop courte pour porter un package — `Foo` d'un dex obfusqué — est
    comptée sous ce qu'elle a. La jeter perdrait justement les dex obfusqués, où un SDK
    inconnu est le plus probable.
    """
    items = []
    for name in sorted(set(class_names)):
        labels = name.split(".")
        items.append((labels[:-1] or labels, name))
    out: list[dict] = []
    _emit(items, PREFIX_DEPTH, out)
    return sorted(out, key=lambda entry: entry["package"])


def _emit(items: list[tuple[list[str], str]], depth: int, out: list[dict]) -> None:
    groups: dict[str, list[tuple[list[str], str]]] = {}
    for labels, name in items:
        groups.setdefault(".".join(labels[:depth]), []).append((labels, name))
    for prefix, members in sorted(groups.items()):
        if (len(members) > SPLIT_ABOVE and depth < MAX_DEPTH
                and any(len(labels) > depth for labels, _ in members)):
            _emit(members, depth + 1, out)
            continue
        out.append({"package": prefix, "classes": len(members),
                    "sample": [name for _, name in members[:SAMPLE]]})


def build(*, handle: str, version_name: str | None, apk_sha256: str, observed_at: str,
          analyzer_version: str, class_names: Iterable[str],
          domains: Iterable[str], credentials: Iterable[dict] = ()) -> dict:
    """Le document de preuves d'une version, prêt à écrire.

    Les domaines arrivent déjà reconnus : `analyze` applique `hosts` dex par dex pour
    ne pas tenir en mémoire le pool de chaînes complet d'une grosse application, et
    y joint ceux du `network_security_config`. Les deux origines finissent dans une
    seule liste — les distinguer demanderait à `candidates` de gérer deux jeux là où la
    question posée est la même : « ce package parle-t-il à ce domaine ? ».
    """
    return {
        "handle": handle,
        "version_name": version_name,
        "apk_sha256": apk_sha256,
        "observed_at": observed_at,
        "analyzer_version": analyzer_version,
        "packages": packages(class_names),
        "domains": sorted({d.strip().lower() for d in domains if d.strip()}),
        # Les identifiants de compte SDK arrivent déjà reconnus et tronqués par `keys` :
        # ce module ne voit jamais une clé entière, et une preuve écrite ne peut donc
        # pas en republier une par accident.
        "credentials": list(credentials),
    }


def path_for(root: Path, handle: str, version_name: str | None) -> Path:
    """`evidence/<handle>/<version>.json`. La version est assainie, pas supposée sûre.

    Un `version_name` vient du manifeste d'un tiers : il porte des barres obliques dans
    la nature, et une preuve écrirait alors hors de son répertoire.
    """
    version = re.sub(r"[^A-Za-z0-9._+-]", "_", version_name or "unknown")
    return root / handle / f"{version}.json"


def write(root: Path, doc: dict) -> Path:
    path = path_for(root, doc["handle"], doc.get("version_name"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                    "utf-8")
    return path


def _rang(doc: dict) -> tuple:
    """De quoi ordonner deux preuves d'une même application.

    La version d'abord, en tri naturel : `5.12.0` est postérieure à `4.44.1`, ce qu'un
    tri de chaînes affirmerait à l'envers dès qu'un nombre passe à deux chiffres. La date
    d'observation ne sert que de départage, quand deux documents portent la même version
    ou aucune version lisible.
    """
    return (tuple(int(n) for n in re.findall(r"\d+", doc.get("version_name") or "")),
            doc.get("observed_at") or "")


def load_all(root: Path) -> list[dict]:
    """Toutes les preuves du disque, la plus récente d'abord pour chaque application.

    `candidates` raisonne par application et non par version : garder deux versions
    d'une même application compterait sa prévalence deux fois et gonflerait tout SDK
    présent chez celle qu'on a analysée le plus souvent.

    « La plus récente » se lit sur la **version**, pas sur `observed_at`. Ce dernier est
    le jour où *nous* avons téléchargé, ce qui est la même chose tant qu'on ne récupère
    que des versions courantes — et exactement l'inverse dès qu'on remonte une version
    de 2023. Elle arriverait avec l'`observed_at` du jour, deviendrait « la plus
    récente », et `endpoints`, `keys` et `candidates` décriraient un binaire de trois ans
    comme l'état actuel de l'application. Silencieusement.
    """
    latest: dict[str, dict] = {}
    for path in sorted(root.glob("*/*.json")) if root.exists() else []:
        doc = json.loads(path.read_text("utf-8"))
        known = latest.get(doc["handle"])
        if known is None or _rang(doc) >= _rang(known):
            latest[doc["handle"]] = doc
    return [latest[handle] for handle in sorted(latest)]
