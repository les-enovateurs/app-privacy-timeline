"""Sous-projet C : les packages qu'aucun catalogue ne connaît, et leurs preuves.

Pur — entre des documents de preuves, sort des candidats. La méthode tient en trois
soustractions et une corrélation : les préfixes de packages du dex, moins le catalogue
Exodus, moins une allowlist de bibliothèques bénignes, moins le paquet de l'application
elle-même ; ce qui reste est croisé avec les domaines du binaire.

**Aucun candidat n'atteint le site sans signature humaine.** Ce module ne produit que du
`pending` ; `validated` et `rejected` ne s'écrivent qu'à la main, et une nouvelle passe
ne les efface jamais — c'est la seule propriété de cet étage qui compte vraiment, tout
le reste n'est qu'un tri à donner à lire.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from . import endpoints

STATUSES = ("pending", "validated", "rejected")

# Les premiers labels qui font d'un package un domaine à l'envers. `com.adjust.sdk`
# donne `adjust.com` ; `io.branch.referral` donne `branch.io`. Un package qui ne
# commence pas par l'un d'eux — `okhttp3`, `kotlinx` — n'a pas de domaine déductible et
# la corrélation retombe sur son label de marque.
_TLD_HEADS = frozenset(("com", "net", "org", "io", "co", "me", "app", "dev", "ai",
                        "fr", "de", "uk", "eu", "cn", "ru", "jp", "kr", "in", "tv"))


def load_allowlist(path: Path) -> list[str]:
    """Préfixes bénins, un par ligne, `#` en commentaire.

    Un fichier de données et non une constante du code : cette liste décide à elle
    seule du taux de faux positifs, elle démarre incomplète et se cure à la main
    pendant des mois. La mettre dans le code ferait passer chaque curation par un
    changement de code, donc par une revue qui n'a rien à revoir.
    """
    if not path.exists():
        return []
    lines = (line.split("#", 1)[0].strip() for line in path.read_text("utf-8").splitlines())
    return sorted({line for line in lines if line})


def known_by_exodus(package: str, trackers: Iterable) -> str | None:
    """Le nom du pisteur d'Exodus dont la signature couvre ce package, s'il existe.

    On applique la signature au *préfixe*, pas aux noms de classes : la question posée
    ici n'est pas « ce pisteur est-il présent ? » — `signatures.detect` y a déjà
    répondu au moment de l'analyse — mais « ce préfixe est-il déjà catalogué ? ».

    Deux sens de couverture, et le second n'est pas une subtilité : **beaucoup de
    signatures d'Exodus nomment une classe, pas un package.** AdMob se cherche par
    `com.google.android.gms.ads.AdView`, qui ne se trouve dans aucun préfixe. Sans ce
    second test, `com.google.android.gms.ads.internal` — 358 classes chez Grindr —
    ressortirait en candidat alors que le pisteur est catalogué depuis des années. On
    compare donc aussi le préfixe au *package* de la classe nommée par la signature.

    Une signature illisible est ignorée en silence, elle l'a déjà été bruyamment à
    l'analyse.
    """
    for tracker in trackers:
        try:
            if re.search(tracker.code_signature, package):
                return tracker.name
        except re.error:
            continue
        if any(package == owner or package.startswith(owner + ".")
               for owner in signature_packages(tracker.code_signature)):
            return tracker.name
    return None


# Une alternative de signature réduite à du texte : `com\.google\.android\.gms\.ads\.AdView`
# -> `com.google.android.gms.ads.AdView`. Les alternatives qui portent autre chose que
# des points échappés — quantificateurs, classes de caractères — sont laissées de côté :
# leur préfixe littéral n'est pas déductible sans interpréter la regex, et se tromper
# ici masquerait un candidat au lieu d'en montrer un de trop.
_TEXTE = re.compile(r"^[A-Za-z0-9_.$]+$")


def _literals(code_signature: str) -> list[str]:
    plain = (alt.replace(r"\.", ".").strip(".") for alt in code_signature.split("|"))
    return [alt for alt in plain if _TEXTE.match(alt)]


def signature_packages(code_signature: str) -> list[str]:
    """Les packages nommés par une signature, son dernier label retiré s'il est une classe.

    La convention Java est le seul indice disponible et elle suffit : une classe
    commence par une majuscule, un package non. `…ads.AdView` donne donc `…ads`, et
    `com.google.ads.` reste `com.google.ads`. Se tromper dans l'autre sens serait grave
    — retirer `ads` rendrait `com.google` couvert, et tout candidat Google
    disparaîtrait en silence.
    """
    out = []
    for literal in _literals(code_signature):
        labels = literal.split(".")
        if labels[-1][:1].isupper():
            labels = labels[:-1]
        if len(labels) > 1:
            out.append(".".join(labels))
    return out


def allowlisted(package: str, allowlist: Iterable[str]) -> bool:
    """Vrai si un préfixe bénin couvre ce package. Comparaison par labels.

    `startswith` sur la chaîne ferait passer `com.androidxyz.tracker` pour de
    l'`androidx` — la sous-chaîne suffit, le point de séparation manque.
    """
    labels = package.split(".")
    return any(labels[:len(prefix.split("."))] == prefix.split(".") for prefix in allowlist)


_CRC64 = re.compile(r"^crc64[0-9a-f]{16}$")


def obfusque(package: str) -> bool:
    """Vrai si le nom ne peut plus rien apprendre à personne.

    Mesuré sur les onze premières applications : `b`, `a`, `g4`, `i4`, `q`, `w5`
    remontaient dans le haut du classement — R8 renomme les packages d'un cran, et les
    classes de six applications différentes s'agrègent alors sous la même lettre. Ce
    n'est pas un SDK, c'est un artefact de nommage, et le plancher de classes ne l'arrête
    pas puisqu'il additionne le corpus entier.

    Deux formes d'abord, et le test reste volontairement étroit : un package d'un seul
    label de trois caractères ou moins, ou un package dont *tous* les labels tiennent en
    deux caractères. `bo.app` — le SDK Braze sous son nom interne — passe les deux, et
    c'est bien ce qu'on veut : un vrai candidat trouvé sur un nom court ne doit pas être
    emporté avec le bruit.

    **Troisième forme, ajoutée le 2026-09-14 : un label unique de quatre caractères qui
    porte un chiffre.** `di30`, `q2a0`, `x200`. Elle n'a été visible qu'au corpus entier :
    9 594 candidats de cette forme, et Duolingo, ChatGPT, Reddit, Strava et Twitch en
    portent **exactement les mêmes 9 594 noms**. Ce n'est pas un SDK partagé, c'est le
    dictionnaire de R8 en full mode, qui est déterministe — deux applications sans rien
    de commun se retrouvent sous le même nom parce que le renommeur compte dans le même
    ordre. Le chiffre est ce qui garde la règle étroite : `amex`, `type`, `mono` sont des
    labels de quatre caractères et restent dans la file.

    **La conséquence est le vrai motif.** Un nom de cette forme agrège les classes de
    vingt applications, entre donc dans le classement avec le score de prévalence d'un
    SDK répandu, et repousse en fin de file tout ce qui mérite d'être lu. C'est le grief
    de l'itération 13 sur la plomberie Google, en vingt fois pire.

    **Quatrième forme : `crc64` suivi de seize chiffres hexadécimaux.** Le pont JNI que
    Xamarin génère, un package par assembly, nommé sur le CRC de l'assembly. Le nom change
    à chaque build : le mettre en allowlist serait écrire une ligne morte, et le mettre en
    `rejected` dirait « examiné et écarté » d'un nom que personne ne reverra. Une forme,
    donc, comme les trois autres.
    """
    labels = package.split(".")
    if _CRC64.match(package):
        return True
    if len(labels) == 1:
        return (len(labels[0]) <= 3
                or (len(labels[0]) == 4 and any(c.isdigit() for c in labels[0])))
    return all(len(label) <= 2 for label in labels)


def racine(package: str) -> str:
    """L'éditeur du package. L'unité de décision du tri manuel, qui n'est pas le package.

    Stripe pose 202 sous-packages dans la file, Datadog 71, le 3DS2 d'Adyen 52. Ce sont
    202 lignes à lire pour *une* question — « Stripe collecte-t-il pour son propre
    compte ? » — et un trieur qui lit 202 lignes ne la pose pas une fois, il la repose à
    chaque ligne, ou il abandonne. Regrouper ne décide rien : le statut reste posé
    package par package, et le document ne connaît toujours que des packages.

    Deux labels sous un TLD en tête, un seul sinon — la même règle que la corrélation
    d'endpoints, pour que les deux vues d'un candidat s'accordent. `com.stripe.android`
    donne `com.stripe`, `expo.modules.location` donne `expo`, `atd.aq` donne `atd`.
    """
    labels = package.split(".")
    if len(labels) > 1 and labels[0] in _TLD_HEADS:
        return ".".join(labels[:2])
    return labels[0]


def par_editeur(candidats: Iterable[dict]) -> list[dict]:
    """Le classement replié sur les racines, le plus répandu d'abord.

    `app_count` d'une racine est le maximum de ses packages, jamais leur somme : les
    202 packages de Stripe vivent dans les mêmes applications, et les additionner
    donnerait 2 000 applications à un corpus qui en compte 130. Les endpoints et les
    voisins catalogués sont réunis — ce sont les preuves de la question, et elles sont
    portées par n'importe lequel des sous-packages.
    """
    groupes: dict[str, dict] = {}
    for candidate in candidats:
        found = groupes.setdefault(racine(candidate["package"]), {
            "racine": racine(candidate["package"]),
            "packages": [],
            "app_count": 0,
            "class_count": 0,
            "endpoints": set(),
            "catalogued_siblings": set(),
        })
        found["packages"].append(candidate["package"])
        found["app_count"] = max(found["app_count"], candidate.get("app_count", 0))
        found["class_count"] += candidate.get("class_count", 0)
        found["endpoints"].update(candidate.get("endpoints") or [])
        found["catalogued_siblings"].update(candidate.get("catalogued_siblings") or [])
    out = []
    for found in groupes.values():
        found["packages"].sort()
        found["endpoints"] = sorted(found["endpoints"])
        found["catalogued_siblings"] = sorted(found["catalogued_siblings"])
        out.append(found)
    out.sort(key=lambda g: (-g["app_count"], -len(g["packages"]), -g["class_count"],
                            g["racine"]))
    return out


def endpoints_for(package: str, domains: Iterable[str]) -> list[str]:
    """Les domaines du binaire qui portent le nom du package. Corrélation, pas preuve.

    Un SDK nomme presque toujours ses classes d'après son domaine, à l'envers ; c'est
    ce qui permet de rattacher un endpoint à un candidat sans instrumenter l'exécution.
    L'inverse n'est pas vrai — un endpoint absent ne dit pas que le SDK ne sort pas,
    il peut construire son hôte à l'exécution. D'où « candidat », et le tri manuel.
    """
    labels = package.split(".")
    if len(labels) >= 2 and labels[0] in _TLD_HEADS:
        target = f"{labels[1]}.{labels[0]}"
        return sorted({d for d in domains if d == target or d.endswith("." + target)})
    # Un label de marque de moins de trois caractères ne corrèle rien : le package `b`
    # se voyait attribuer `b.applovin.com` et `b.stats.paypal.com`, deux endpoints avec
    # lesquels il n'a en commun qu'une lettre. Une preuve fausse est pire qu'absente —
    # c'est sur elle qu'un tri manuel déciderait.
    if len(labels[0]) < 3:
        return []
    # Pas de TLD en tête : le premier label *est* la marque — `okhttp3.internal.tls`
    # vient d'okhttp3. On cherche alors ce label parmi ceux des domaines, ce qui est
    # plus lâche que l'inversion et se relit à la main comme tout le reste.
    return sorted({d for d in domains if labels[0] in d.split(".")})


def catalogued_siblings(package: str, trackers: Iterable) -> list[str]:
    """Les pisteurs catalogués qui vivent sous le même parent que ce candidat.

    Le signal qui manquait au tri manuel. `com.google.android.gms.auth` n'est couvert
    par aucune signature, et rien ne disait au trieur que son voisin immédiat
    `com.google.android.gms.ads` *est* AdMob, catalogué depuis des années — donc que ce
    candidat est très probablement de la plomberie du même éditeur, et non un SDK
    inconnu. L'information est rendue, pas appliquée : ce module ne décide rien, et un
    pisteur peut parfaitement se cacher à côté d'un pisteur connu.

    Le parent est le package moins son dernier label. Remonter plus haut rattacherait
    tout candidat Google à AdMob par `com.google`, ce qui ne dirait plus rien.

    **Et un parent réduit à un TLD ne dit rien non plus — corrigé le 2026-09-14.** Le
    parent de `org.webrtc` est `org`, celui de `com.reactnativekeyboardcontroller` est
    `com` : tout le catalogue vit sous ces deux labels. Mesuré sur la passe du
    2026-09-14, `com.reactnativekeyboardcontroller` se voyait attribuer **plus de deux
    cents voisins catalogués** — la quasi-totalité d'Exodus — ce qui n'occupait pas
    seulement la ligne du classement, ça inversait le sens du champ : le signal qui
    devait dire « regarde à côté, c'est déjà connu » disait « ce candidat touche à tout ».
    Un candidat à deux labels sous un TLD en tête n'a donc pas de voisinage calculable,
    et c'est le même garde-fou qu'`endpoints_for`, qui refuse de corréler sur un label de
    marque trop court. Une preuve fausse est pire qu'absente.
    """
    labels = package.split(".")
    if len(labels) < 2:
        return []
    parent = ".".join(labels[:-1])
    if parent in _TLD_HEADS:
        return []
    found = set()
    for tracker in trackers:
        for owner in signature_packages(tracker.code_signature):
            if owner == parent or owner.startswith(parent + "."):
                found.add(tracker.name)
    return sorted(found)


def _sample_entierement_catalogue(entry: dict, trackers: list) -> bool:
    """Vrai si l'échantillon *est* le package et que chaque classe est cataloguée.

    Le rattrapage des préfixes trop hauts. `com.google.android` ne peut être couvert
    par aucune signature — elles nomment toutes un produit plus bas — mais s'il ne
    porte que trois classes et qu'elles sont toutes sous `com.google.android.gms.ads`,
    il n'y a rien à trier. La condition `classes <= len(sample)` est ce qui rend le
    raisonnement exact : au-delà, l'échantillon ne dit rien du reste du package, et un
    pisteur inconnu logé sous le même parapluie disparaîtrait sans bruit.
    """
    sample = entry.get("sample") or []
    return (bool(sample) and entry["classes"] <= len(sample)
            and all(known_by_exodus(name, trackers) for name in sample))


# Plancher de bruit. Mesuré sur Grindr 26.15.1 : 13 710 candidats bruts, dont 13 498
# d'une seule classe — un dex obfusqué produit des milliers de packages `q0`, `a.b`,
# qu'aucun humain ne peut trier et qui pèsent 3,7 Mo à eux seuls. Le compte est celui
# de *toutes* les applications réunies : un package présent une fois dans vingt
# applications passe le seuil, et c'est exactement le signal qu'on cherche.
MIN_CLASSES = 5


def find(evidences: Iterable[dict], trackers: Iterable, allowlist: Iterable[str],
         min_apps: int = 1, min_classes: int = MIN_CLASSES,
         host_classes: dict[str, str] | None = None) -> list[dict]:
    """Les preuves du corpus -> les candidats, les plus répandus d'abord.

    Le paquet de l'application elle-même est écarté : `com.carrefour.fid` chez Carrefour
    n'est pas un SDK tiers, c'est l'application. Le test porte sur deux labels et non
    sur le handle entier — `com.carrefour.fid.android` porte du code sous
    `com.carrefour.market`, qui est du premier parti tout autant. Il ne rattrape pas les
    variantes de marque : Grindr publie `com.grindrapp.android` et porte du code sous
    `com.grindr.analytics`, qui ressort en candidat et se rejette à la main. Deviner la
    parenté par ressemblance de chaînes écarterait un jour un vrai pisteur homonyme.

    **La prévalence est passée de tri à filtre par défaut le 2026-09-10, sur mesure.**
    Le design disait qu'un SDK vu dans une seule application est un signal faible mais
    réel, donc jamais filtré par défaut. À l'échelle de onze applications, ces
    singletons sont **2 036 des 2 448 candidats** : ce n'est plus un signal faible,
    c'est le bruit dominant, et il enterre les 412 packages que deux applications
    partagent. Le seuil par défaut est donc à deux, et `--min-apps 1` rend la queue
    complète à qui veut la lire. Un package vu une fois sera écrit le jour où une
    seconde application le porte — ce qui est exactement la définition d'un argument de
    prévalence.

    Le plancher de classes est l'autre filtre par défaut — un package d'une seule
    classe n'est pas un SDK, et ils étaient 13 498 sur la première application analysée.

    **La corrélation ne porte que sur les hôtes classés `endpoint` depuis le
    2026-09-10.** Mesuré sur la première passe : les quinze premiers candidats étaient
    tous des internes de Play Services, et les trois mêmes « preuves » étaient collées
    à chacun — `accounts.google.com`, `console.firebase.google.com`,
    `developers.google.com`. Les deux dernières ne sont pas des endpoints du tout : une
    console d'éditeur et un portail de documentation, présents dans le binaire parce
    qu'une bibliothèque Google les cite. Un tri manuel décidait donc sur une preuve
    fausse, ce que ce module s'interdit ailleurs — d'où le passage par
    `endpoints.classify`.
    """
    trackers = list(trackers)
    allowlist = list(allowlist)
    seen: dict[str, dict] = {}

    for doc in evidences:
        handle = doc["handle"]
        first_party = handle.split(".")[:2]
        # Seuls les hôtes qu'une application peut réellement contacter valent comme
        # corrélation. La table de classement est optionnelle : sans elle, la structure
        # écarte déjà les non-hôtes, et les endpoints sous TLD faible se perdent — un
        # faux négatif que le fichier de données répare, jamais un faux positif.
        domains = [host for host in doc.get("domains") or []
                   if endpoints.classify(host, host_classes) == "endpoint"]
        for entry in doc.get("packages") or []:
            package = entry["package"]
            if allowlisted(package, [".".join(first_party)] + allowlist):
                continue
            if obfusque(package):
                continue
            if known_by_exodus(package, trackers):
                continue
            if _sample_entierement_catalogue(entry, trackers):
                continue
            found = seen.setdefault(package, {
                "package": package, "status": "pending", "apps": [],
                "app_count": 0, "class_count": 0, "sample_classes": [], "endpoints": [],
                "catalogued_siblings": [],
            })
            found["apps"].append({"handle": handle,
                                  "version_name": doc.get("version_name"),
                                  "classes": entry["classes"]})
            found["class_count"] += entry["classes"]
            found["sample_classes"] = sorted(
                set(found["sample_classes"]) | set(entry.get("sample") or []))[:5]
            found["endpoints"] = sorted(
                set(found["endpoints"]) | set(endpoints_for(package, domains)))
            found["catalogued_siblings"] = catalogued_siblings(package, trackers)

    out = []
    for found in seen.values():
        found["apps"].sort(key=lambda a: a["handle"])
        found["app_count"] = len(found["apps"])
        if found["app_count"] >= min_apps and found["class_count"] >= min_classes:
            out.append(found)
    return sorted(out, key=lambda c: (-c["app_count"], -c["class_count"], c["package"]))


def merge(existing: dict | None, fresh: list[dict], generated_at: str,
          catalog_version: str | None) -> tuple[dict, dict]:
    """Les candidats d'une passe + le fichier existant -> le fichier à écrire, et le bilan.

    Deux règles, et la première est la raison d'être de cette fonction.

    1. **Un statut posé à la main survit à toutes les passes.** Un `rejected` réévalué
       en `pending` ferait relire chaque mois les mêmes cinquante faux positifs, et le
       tri manuel s'arrêterait au deuxième passage. La note humaine suit le statut.
    2. **Un candidat jugé qui disparaît des preuves est gardé**, marqué `absent`. Il
       disparaît parce que l'application a été mise à jour, pas parce que le jugement
       est faux : le réintroduire à la prochaine régression le ferait rejuger. Un
       `pending` disparu, lui, est oublié — personne ne l'a encore regardé.
    """
    previous = {c["package"]: c for c in (existing or {}).get("candidates", [])}
    kept: list[dict] = []
    counts = {"new": 0, "pending": 0, "validated": 0, "rejected": 0, "absent": 0}

    for candidate in fresh:
        before = previous.pop(candidate["package"], None)
        if before:
            candidate["status"] = before.get("status", "pending")
            if before.get("note"):
                candidate["note"] = before["note"]
        else:
            counts["new"] += 1
        counts[candidate["status"]] = counts.get(candidate["status"], 0) + 1
        kept.append(candidate)

    for orphan in previous.values():
        if orphan.get("status", "pending") == "pending":
            continue
        orphan["absent"] = True
        counts["absent"] += 1
        kept.append(orphan)

    doc = {
        "generated_at": generated_at,
        "catalog_version": catalog_version,
        "candidates": sorted(kept, key=lambda c: (-c.get("app_count", 0),
                                                  -c.get("class_count", 0),
                                                  c["package"])),
    }
    return doc, counts


def load(path: Path) -> dict | None:
    return json.loads(path.read_text("utf-8")) if path.exists() else None
