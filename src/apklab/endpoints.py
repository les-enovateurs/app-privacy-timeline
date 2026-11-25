"""Les hôtes du binaire triés, et les endpoints tiers du corpus.

`evidence.hosts` reconnaît tout ce qui *ressemble* à un hôte ; ce module dit ce qu'un
hôte *est*. La distinction n'est pas cosmétique : sur les 730 hôtes distincts des onze
premières applications, une centaine ne sont ni des endpoints ni même des domaines —
`schemas.android.com` est un espace de noms XML, `filled.info` est l'icône
`Icons.Filled.Info` mise en minuscules, `user.id` est une clé JSON, `10.0.2.2` est le
loopback de l'émulateur. Publier « cette application contacte 239 domaines » sur cette
base serait faux, et faux dans le sens qui décrédibilise le plus un dataset opposable.

Rien n'est supprimé : chaque hôte reste dans les preuves et ressort ici avec sa classe.
Un tri qui jette perd la trace de ce qu'il a jeté, et personne ne peut plus contester
son verdict — c'est l'inverse du but.

Cinq classes :

- `endpoint`   — un hôte que l'application peut contacter. Le seul fait publiable.
- `doc`        — domaine réel, jamais contacté : espace de noms XML, doc, tracker
                 d'issues, portail de console. `schemas.android.com`, `www.w3.org`.
- `placeholder`— gabarit d'exemple ou de test laissé dans le binaire. `example.com`.
- `code`       — du code pris pour un hôte : clé JSON, propriété système, nom de
                 classe. Reconnaissable au TLD faible (`user.id`) ou listé à la main.
- `invalid`    — ne peut pas être un hôte : un seul label, TLD inconnu, IP, suffixe
                 public nu (`co.uk`), condensat.

Le module est pur : il reçoit les hôtes déjà extraits par `evidence` et une table de
classement chargée d'un fichier de données. Aucun APK, aucun réseau.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .evidence import TLDS

CLASSES = ("endpoint", "doc", "placeholder", "code", "invalid", "infra")

# TLD sous lesquels un identifiant de code est indistinguable d'un domaine. `user.id`,
# `session.id`, `view.id`, `dispatchers.io` — mesurés dans les preuves — ont la même
# forme que `screeb.app` ou `batch.io`, qui sont de vrais endpoints. La structure ne
# tranche pas ; seule une liste tenue à la main le fait. Le défaut est donc `code`, et
# un vrai endpoint sous TLD faible se déclare dans `host-classes.txt`. Un faux négatif
# visible dans la classe `code` vaut mieux qu'un faux endpoint publié.
WEAK_TLDS = frozenset(("id", "info", "app", "dev", "io", "cloud", "site", "me", "co",
                       "tv", "to", "st", "sh", "gg", "ly", "am", "fm", "cc", "ai",
                       "xyz", "online", "biz", "asia"))

# Suffixes publics à deux labels rencontrés dans le corpus. `www.blablacar.co.uk` est
# un hôte, `co.uk` nu n'en est pas un : c'est le suffixe seul, sans marque devant.
PUBLIC_SUFFIXES = frozenset(("co.uk", "co.th", "co.nz", "co.za", "co.jp", "co.kr",
                             "co.in", "co.il", "com.br", "com.tr", "com.ua", "com.vn",
                             "com.au", "com.mx", "com.cn", "com.hk", "com.tw",
                             "net.in", "org.uk", "gouv.fr", "com.ar"))

# Un label d'exemple. Le test porte sur le label entier et non sur la sous-chaîne :
# `facetec` contient `test`, et `api.facetec.com` est un endpoint réel.
_PLACEHOLDER_LABELS = frozenset(("example", "dummy", "fake", "whatever", "toto",
                                 "placeholder", "mock", "test", "testing", "localhost",
                                 "faux", "sample", "yourdomain", "mydomain", "domain"))

# Un condensat ou un identifiant OAuth pris pour un hôte : `73b4a5a4…531`,
# `123456789012-abcdefgh….apps.googleusercontent.com`.
_OPAQUE = re.compile(r"^[0-9a-f]{16,}$|^[0-9a-f-]{32,}$|^\d{6,}-[a-z0-9]{16,}$")


def load_classes(path: Path) -> dict[str, str]:
    """La table de classement, `<classe> <hôte>` par ligne, `#` en commentaire.

    Un hôte préfixé d'un point couvre ses sous-domaines : `doc .w3.org` classe
    `www.w3.org` et `purl.w3.org` d'un coup.

    Fichier de données et non constante du code, pour la raison qui vaut déjà pour
    `allowlist.txt` : cette table démarre incomplète, se cure à chaque passe, et la
    mettre dans le code ferait passer chaque curation par une revue de code qui n'a
    rien à revoir.
    """
    table: dict[str, str] = {}
    if not path.exists():
        return table
    for raw in path.read_text("utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or parts[0] not in CLASSES:
            continue
        table[parts[1].lower()] = parts[0]
    return table


def override_for(host: str, table: dict[str, str]) -> str | None:
    """La classe déclarée à la main pour cet hôte, la plus spécifique d'abord.

    L'hôte exact gagne sur le suffixe : `endpoint console.example.com` reste un
    endpoint même sous un `doc .example.com`. Sans cette priorité, une exception ne
    serait pas exprimable et la table entière deviendrait tout-ou-rien.
    """
    if host in table:
        return table[host]
    labels = host.split(".")
    for cut in range(1, len(labels)):
        found = table.get("." + ".".join(labels[cut:]))
        if found:
            return found
    return None


def classify(host: str, table: dict[str, str] | None = None) -> str:
    """La classe d'un hôte. Structure d'abord, table ensuite, TLD faible en dernier.

    L'ordre compte. Les règles structurelles passent avant la table parce qu'aucune
    curation ne doit pouvoir déclarer endpoint ce qui n'est pas un hôte — c'est la
    seule barrière que le fichier de données ne peut pas ouvrir par erreur. La table
    passe avant le TLD faible pour que `endpoint adj.st` soit déclarable.
    """
    host = host.strip().lower().strip(".")
    labels = host.split(".")
    if not host or len(labels) < 2:
        return "invalid"
    if all(label.isdigit() for label in labels):
        return "invalid"
    if labels[-1] not in TLDS:
        return "invalid"
    if host in PUBLIC_SUFFIXES:
        return "invalid"
    if any(_OPAQUE.match(label) for label in labels):
        return "invalid"

    declared = override_for(host, table or {})
    if declared:
        return declared

    if any(label in _PLACEHOLDER_LABELS for label in labels):
        return "placeholder"
    if labels[-1] in WEAK_TLDS:
        return "code"
    return "endpoint"


def owners(host: str, packages: Iterable[str]) -> list[str]:
    """Les packages du binaire qui portent la marque de cet hôte. Corrélation, pas preuve.

    L'inverse de `candidates.endpoints_for`, et la même convention : un SDK nomme ses
    classes d'après son domaine, à l'envers. `sdk.iad-01.braze.com` a pour marque
    `braze`, qu'on retrouve dans `com.braze.push`.

    La marque est le label qui précède le TLD, jamais un label plus à gauche : sur
    `checkoutshopper-live.adyen.com`, chercher `checkoutshopper-live` ne rendrait rien
    et chercher tous les labels rattacherait `sdk.iad-01.braze.com` à tout package
    contenant `sdk`. Un label de moins de trois caractères ne corrèle rien — la leçon
    est déjà payée dans `candidates`, où le package `b` héritait de `b.applovin.com`.

    **La marque doit ouvrir le package, pas y apparaître n'importe où.** Mesuré à la
    première passe : `play.google.com` se voyait attribuer
    `co.reachfive.identity.sdk.google`, un module d'intégration qui *nomme* Google au
    cinquième label sans lui appartenir. Un SDK commence par sa marque, éventuellement
    derrière un TLD inversé — `com.braze.push`, `com.adjust.sdk`, `adj` — d'où les deux
    premiers labels seulement.
    """
    labels = host.split(".")
    if len(labels) < 2:
        return []
    brand = labels[-2]
    if len(brand) < 3:
        return []
    return sorted({pkg for pkg in packages if brand in pkg.split(".")[:2]})


def collect(evidences: Iterable[dict], table: dict[str, str] | None = None) -> list[dict]:
    """Les preuves du corpus -> un hôte par entrée, les plus répandus d'abord.

    La prévalence est le fait qui intéresse une étude : « douze applications parlent à
    `adjust.com` » est opposable, « BlaBlaCar parle à `adjust.com` » l'est déjà moins.
    Elle se compte par application et non par preuve, `evidence.load_all` ne rendant
    qu'une version par application.

    `first_party` n'est posé que sur une correspondance de label avec le handle, jamais
    déduite d'une ressemblance. Comuto publie `com.comuto` et contacte
    `www.blablacar.fr` : la marque diffère du paquet, et deviner la parenté marquerait
    un jour premier parti l'endpoint d'un tiers homonyme. Un endpoint tiers non marqué
    reste donc à lire, ce qui est le comportement voulu.
    """
    table = table or {}
    seen: dict[str, dict] = {}
    for doc in evidences:
        handle = doc["handle"]
        handle_labels = set(handle.split("."))
        packages = [entry["package"] for entry in doc.get("packages") or []]
        for host in doc.get("domains") or []:
            host = host.strip().lower()
            if not host:
                continue
            found = seen.setdefault(host, {
                "host": host, "class": classify(host, table), "apps": [],
                "app_count": 0, "owner_packages": [], "owner_package_count": 0,
                "first_party": False, "_owners": set(),
            })
            if handle not in found["apps"]:
                found["apps"].append(handle)
            found["_owners"] |= set(owners(host, packages))
            labels = host.split(".")
            if len(labels) >= 2 and labels[-2] in handle_labels:
                found["first_party"] = True

    out = []
    for found in seen.values():
        found["apps"].sort()
        found["app_count"] = len(found["apps"])
        # Le compte porte sur tous les packages corrélés, la liste s'arrête à cinq.
        # Rendre `len(owner_packages)` ferait lire « cinq packages » là où il y en a
        # trente, et une marque partagée par trente packages n'est attribuable à aucun
        # — c'est justement ce que le compte doit dire.
        owner_packages = sorted(found.pop("_owners"))
        found["owner_package_count"] = len(owner_packages)
        found["owner_packages"] = owner_packages[:5]
        out.append(found)
    return sorted(out, key=lambda h: (-h["app_count"], h["host"]))


def third_party(hosts: Iterable[dict]) -> list[dict]:
    """Les seuls endpoints publiables : classés `endpoint` et non premier parti.

    La classe `infra` en est exclue par construction, comme `doc` ou `code`. Un serveur
    de temps ou un hôte virtuel de WebView est un vrai nom de domaine, que les règles
    structurelles classent donc `endpoint` — mais personne n'y reçoit de donnée.
    `pool.ntp.org` apparaissait 40 fois sur le corpus et aurait fait lire « cette
    application parle à huit serveurs tiers » là où le fait est « elle lit l'heure ».
    """
    return [h for h in hosts if h["class"] == "endpoint" and not h["first_party"]]


def counts(hosts: Iterable[dict]) -> dict[str, int]:
    """Le bilan par classe, pour que le tri se juge sur des nombres et pas à l'œil."""
    tally = {name: 0 for name in CLASSES}
    for host in hosts:
        tally[host["class"]] = tally.get(host["class"], 0) + 1
    return tally
