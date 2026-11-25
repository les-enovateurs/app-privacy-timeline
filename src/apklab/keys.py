"""Les identifiants de compte SDK écrits en clair dans l'APK.

Un pisteur détecté dit *quel* SDK est embarqué. Une clé de SDK dit *quel compte* chez ce
SDK, donc quel contrat lie l'éditeur au sous-traitant — l'information qu'un rapport
RGPD réclame et qu'aucun catalogue de signatures ne porte. Elle est aussi la preuve la
plus dure que produit ce projet : ni heuristique, ni corrélation, une valeur écrite par
le développeur dans son manifeste ou ses ressources.

Deux voies de reconnaissance, et la seconde est la seule qui découvre :

- **par nom** — `com.google.android.geo.API_KEY`, `facebook_app_id`, `io.sentry.dsn`.
  Sûre, mais ne trouve que ce qui est déjà dans la liste.
- **par forme de valeur** — `AIza…` est une clé Google, `pk_live_…` une clé Stripe,
  `AKIA…` un identifiant AWS, quel que soit le nom sous lequel elle est rangée. C'est
  ce qui fait remonter un compte qu'on ne cherchait pas.

**Ce qui est publié est tronqué.** Une clé cliente embarquée dans un APK est publique
par construction, mais un dataset qui la republie en entier fournit un moyen de la
consommer aux frais de l'éditeur — et l'empreinte suffit à tout ce qui nous intéresse :
prouver la présence, et voir la rotation d'une version à l'autre. Les *identifiants* de
compte qui ne sont pas des secrets — identifiant d'application Facebook, projet
Firebase — sont gardés entiers : c'est précisément le nom du compte qui fait le fait.

Module pur : il reçoit des couples (origine, nom, valeur) déjà extraits du binaire par
`analyze`, et rend des dicts. Rien ici n'ouvre un APK.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable

# Longueur du préfixe rendu en clair pour un secret. Six caractères identifient le
# fournisseur (`AIzaSy`, `pk_liv`) sans rendre la valeur utilisable.
PREFIX = 6

# Longueur de l'empreinte. Douze hexadécimaux suffisent à comparer deux versions ; la
# somme entière donnerait à qui possède un dictionnaire de clés le moyen de la retrouver.
DIGEST = 12

# Une valeur de manifeste peut être une *référence* de ressource non résolue plutôt
# qu'une valeur : `@7F14005A`, `@string/maps_key`, `?attr/…`. Mesuré à la passe du
# 2026-09-10 : Deliveroo rangeait sa clé Maps derrière `@7F140…`, et l'outil annonçait
# « clé API Google Maps » sur une chaîne qui n'en est pas une. La résoudre demanderait
# de rejouer la table des ressources par configuration ; l'écarter coûte un faux négatif
# visible et évite d'affirmer un compte qu'on n'a jamais lu.
_REFERENCE = re.compile(r"^[@?]")


@dataclass(frozen=True)
class Pattern:
    """Une famille d'identifiants. `name` reconnaît le rangement, `value` la forme.

    `secret` décide de la publication : une clé qui autorise un appel facturé est
    tronquée, un identifiant qui nomme seulement le compte est gardé entier.
    """
    id: str
    label: str
    name: str | None
    value: str | None
    secret: bool = True


# Reconnaissances tenues à la main. L'ordre compte : la première famille qui reconnaît
# gagne, donc les noms précis passent avant les formes génériques — sans quoi
# `com.google.android.geo.API_KEY` serait rendu sous l'étiquette « clé Google » et on
# perdrait l'information qu'il s'agit de Maps.
PATTERNS: tuple[Pattern, ...] = (
    Pattern("google-maps", "clé API Google Maps",
            r"^com\.google\.android\.(geo|maps\.v2)\.API_KEY$", None),
    Pattern("firebase-api-key", "clé API Firebase (google-services.json)",
            r"^google_api_key$|^google_crash_reporting_api_key$", None),
    Pattern("firebase-project", "projet Firebase",
            r"^project_id$|^google_storage_bucket$|^firebase_database_url$", None,
            secret=False),
    Pattern("firebase-sender", "expéditeur GCM/FCM",
            r"^gcm_defaultSenderId$", None, secret=False),
    Pattern("firebase-app-id", "identifiant d'application Firebase",
            r"^google_app_id$", None, secret=False),
    Pattern("facebook-app", "identifiant d'application Facebook",
            r"^com\.facebook\.sdk\.ApplicationId$|^facebook_app_id$", None,
            secret=False),
    Pattern("facebook-token", "jeton client Facebook",
            r"^com\.facebook\.sdk\.ClientToken$|^facebook_client_token$", None),
    Pattern("appsflyer", "clé développeur AppsFlyer",
            r"appsflyer.*dev.?key|af_dev_key", None),
    Pattern("adjust", "jeton d'application Adjust",
            r"adjust.*app.?token", None, secret=False),
    Pattern("branch", "clé Branch",
            r"^io\.branch\.sdk\.BranchKey", r"^key_(live|test)_[A-Za-z0-9]{20,}$"),
    Pattern("onesignal", "identifiant d'application OneSignal",
            r"onesignal.*app.?id", None, secret=False),
    Pattern("braze", "clé API Braze",
            r"com.?braze.?api.?key|appboy.*api.?key", None),
    Pattern("bugsnag", "clé API Bugsnag",
            r"^com\.bugsnag\.android\.API_KEY$", None),
    Pattern("instabug", "jeton Instabug", r"instabug.*token", None),
    Pattern("amplitude", "clé API Amplitude", r"amplitude.*(api.?)?key", None),
    Pattern("mixpanel", "jeton Mixpanel", r"mixpanel.*token", None),
    Pattern("algolia", "identifiants Algolia", r"algolia.*(app.?id|api.?key)", None),
    Pattern("mapbox", "jeton Mapbox", r"mapbox.*token", r"^(pk|sk)\.eyJ[A-Za-z0-9._-]{20,}$"),
    Pattern("sentry-dsn", "DSN Sentry",
            r"^io\.sentry\.dsn$", r"^https://[0-9a-f]{16,}@[A-Za-z0-9.\-]+/\d+$"),
    Pattern("datadog", "jeton client Datadog", r"datadog.*token", r"^pub[0-9a-f]{32}$"),
    Pattern("stripe", "clé publiable Stripe", r"stripe.*key",
            r"^pk_(live|test)_[A-Za-z0-9]{20,}$"),
    Pattern("aws-access-key", "identifiant d'accès AWS", None, r"^AKIA[0-9A-Z]{16}$"),
    Pattern("google-oauth-client", "client OAuth Google", None,
            r"^\d{9,}-[a-z0-9]{16,}\.apps\.googleusercontent\.com$", secret=False),
    # Dernière de la liste, volontairement : toute clé Google non reconnue par son nom.
    # Elle rattrape les rangements exotiques — un `AIza…` dans une ressource au nom
    # maison — au prix d'une étiquette imprécise, ce qui reste mieux que rien.
    Pattern("google-api-key", "clé API Google", None, r"^AIza[0-9A-Za-z_\-]{35}$"),
)

_COMPILED = tuple(
    (p,
     re.compile(p.name, re.IGNORECASE) if p.name else None,
     re.compile(p.value) if p.value else None)
    for p in PATTERNS)


def match(name: str, value: str) -> tuple[int, Pattern] | None:
    """La famille qui reconnaît ce couple et son rang, ou rien.

    Le nom d'abord, la forme ensuite : `com.google.android.geo.API_KEY` doit sortir
    sous « clé Maps » et non sous « clé Google », que les deux reconnaissances
    s'appliquent ou non. Le rang est rendu parce qu'il *est* la précision : il sert à
    trancher quand la même valeur est reconnue deux fois, sous deux rangements.
    """
    for rank, (pattern, name_re, value_re) in enumerate(_COMPILED):
        if name_re and name and name_re.search(name):
            return rank, pattern
        if value_re and value and value_re.match(value):
            return rank, pattern
    return None


def redact(value: str, secret: bool) -> dict:
    """La valeur telle qu'elle sera publiée : entière, ou préfixe et empreinte.

    L'empreinte porte sur la valeur entière : c'est elle qui permet de dire « la clé a
    changé entre 6.29 et 6.30 » sans jamais avoir republié la clé.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:DIGEST]
    found = {"value_prefix": value[:PREFIX], "value_sha256": digest,
             "value_length": len(value)}
    if not secret:
        found["value"] = value
    return found


def scan(pairs: Iterable[tuple[str, str, str]]) -> list[dict]:
    """Des couples (origine, nom, valeur) -> les identifiants reconnus, dédoublonnés.

    Le dédoublonnage porte sur la *valeur* et non sur le nom : les outils de build
    déversent la même clé Google dans le manifeste et dans les ressources, sous deux
    noms différents, et deux faits pour une clé laisseraient croire à deux comptes.

    Quand une valeur est reconnue deux fois, c'est le rangement le plus précis qui est
    gardé — `com.google.android.geo.API_KEY` bat `cle_maison` sur la même clé, alors
    que l'ordre de lecture aurait pu donner l'inverse.
    """
    seen: dict[str, tuple[int, dict]] = {}
    for origin, name, value in pairs:
        value = (value or "").strip()
        name = (name or "").strip()
        if not value or len(value) > 512 or _REFERENCE.match(value):
            continue
        found_pattern = match(name, value)
        if not found_pattern:
            continue
        rank, pattern = found_pattern
        found = {"id": pattern.id, "label": pattern.label, "name": name,
                 "origin": origin, "secret": pattern.secret,
                 **redact(value, pattern.secret)}
        known = seen.get(found["value_sha256"])
        if known is None or rank < known[0]:
            seen[found["value_sha256"]] = (rank, found)
    return sorted((found for _, found in seen.values()),
                  key=lambda f: (f["id"], f["value_sha256"]))


def collect(evidences: Iterable[dict]) -> list[dict]:
    """Les preuves du corpus -> un identifiant de compte par entrée, les plus répandus d'abord.

    L'agrégation répond à la question qu'une passe application par application ne peut
    pas poser : **cette clé est-elle celle de l'éditeur ?** Mesuré à la passe du
    2026-09-10 — France Travail et BeReal portent la *même* clé Maps, empreinte
    identique. Deux éditeurs sans rapport ne partagent pas un compte : c'est la valeur
    par défaut d'une bibliothèque embarquée par les deux. Un compte partagé ne dit donc
    rien de l'éditeur, et tout de la bibliothèque.

    `shared` est rendu, pas appliqué. Deux applications d'un même éditeur partagent
    légitimement un projet Firebase, et c'est un fait à lire, pas un doublon à écarter.
    """
    seen: dict[tuple[str, str], dict] = {}
    for doc in evidences:
        handle = doc["handle"]
        for found in doc.get("credentials") or []:
            key = (found["id"], found["value_sha256"])
            entry = seen.setdefault(key, {
                "id": found["id"], "label": found["label"],
                "value_prefix": found["value_prefix"],
                "value_sha256": found["value_sha256"],
                "secret": found.get("secret", True),
                "apps": [], "app_count": 0, "names": [], "origins": [],
                "shared": False,
            })
            if "value" in found:
                entry["value"] = found["value"]
            if handle not in entry["apps"]:
                entry["apps"].append(handle)
            entry["names"] = sorted(set(entry["names"]) | {found["name"]})
            entry["origins"] = sorted(set(entry["origins"]) | {found["origin"]})

    out = []
    for entry in seen.values():
        entry["apps"].sort()
        entry["app_count"] = len(entry["apps"])
        entry["shared"] = entry["app_count"] > 1
        out.append(entry)
    return sorted(out, key=lambda e: (-e["app_count"], e["id"], e["value_sha256"]))
