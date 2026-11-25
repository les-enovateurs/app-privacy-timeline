"""Analyse statique d'un APK. Avec `fetch`, le seul module à toucher un binaire.

Rien ici ne sort sur le réseau : l'APK est déjà sur le disque. Les faits produits sont
ceux qu'aucune source existante ne donne — les poids par nature, les ABI embarquées, le
nombre de méthodes — plus ceux qu'on doit pouvoir confronter à Exodus : permissions du
manifeste et pisteurs détectés.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import evidence, keys
from .corpus import CorpusEntry
from .models import Snapshot

# Le poids retenu par nature est le poids *compressé*, celui qui transite et qui occupe
# l'appareil. Le poids décompressé ferait paraître lourde une application dont les
# ressources se compressent bien, ce qui est l'inverse du signal éco-conception.
NATIVE_PREFIX = "lib/"
ASSETS_PREFIX = "assets/"
RES_PREFIXES = ("res/", "resources.arsc")

# The fixed phone every new size is measured for, so a delta survives APKPure switching
# the splits it serves.
REFERENCE_ABI = "arm64-v8a"
REFERENCE_DENSITY = "xxhdpi"


@dataclass
class ApkFacts:
    handle: str
    version_name: str | None
    version_code: int | None
    min_sdk: int | None
    target_sdk: int | None
    permissions: list[str]
    apk_sha256: str
    size_scope: str
    total_bytes: int
    dex_bytes: int
    res_bytes: int
    assets_bytes: int
    native_bytes: int
    native_abis: list[str]
    dex_method_count: int
    class_names: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    credentials: list[dict] = field(default_factory=list)
    analyzer_version: str = ""
    native_bytes_by_abi: dict[str, int] = field(default_factory=dict)
    size_parts: list[dict] = field(default_factory=list)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sizes(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Compressed bytes per kind, and native bytes per ABI, read from the archive alone.

    `total_bytes` is the file size, not the sum of entries: headers included, so the
    buckets never add up exactly to the total, on purpose.
    """
    buckets = {"dex_bytes": 0, "res_bytes": 0, "assets_bytes": 0, "native_bytes": 0}
    native_by_abi: dict[str, int] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.endswith(".dex"):
                buckets["dex_bytes"] += info.compress_size
            elif name.startswith(NATIVE_PREFIX):
                buckets["native_bytes"] += info.compress_size
                parts = name.split("/")
                if len(parts) > 2:
                    native_by_abi[parts[1]] = native_by_abi.get(parts[1], 0) + info.compress_size
            elif name.startswith(ASSETS_PREFIX):
                buckets["assets_bytes"] += info.compress_size
            elif name.startswith(RES_PREFIXES):
                buckets["res_bytes"] += info.compress_size
    return buckets, dict(sorted(native_by_abi.items()))


def size_part(name: str, kind: str, qualifier: str | None, part: "ApkFacts") -> dict:
    """One entry of `size_parts`: a bundle member and its buckets."""
    return {"name": name, "kind": kind, "qualifier": qualifier, "bytes": part.total_bytes,
            "dex_bytes": part.dex_bytes, "res_bytes": part.res_bytes,
            "assets_bytes": part.assets_bytes, "native_bytes": part.native_bytes}


def _sum_by_abi(parts: list["ApkFacts"]) -> dict[str, int]:
    total: dict[str, int] = {}
    for part in parts:
        for abi, count in part.native_bytes_by_abi.items():
            total[abi] = total.get(abi, 0) + count
    return dict(sorted(total.items()))


def reference_densities(size_parts: list[dict]) -> set[str]:
    """Densities the reference counts: xxhdpi, or whatever was served in its place."""
    served = {p["qualifier"] for p in size_parts if p["kind"] == "density"}
    return {REFERENCE_DENSITY} if REFERENCE_DENSITY in served else served


def reference_bytes(size_scope: str, total_bytes: int, size_parts: list[dict],
                    native_bytes_by_abi: dict[str, int]) -> tuple[int | None, list[str]]:
    """Bytes the reference phone would install, or `None` and the flag that says why.

    Language splits are left out: which ones APKPure serves is unknown.
    """
    flags: list[str] = []
    if native_bytes_by_abi and REFERENCE_ABI not in native_bytes_by_abi:
        flags.append("reference_abi_missing")

    if size_scope != "merged-xapk":
        # A lone APK carries every density, identical on both sides of a comparison.
        if flags:
            return None, flags
        other_abis = sum(n for abi, n in native_bytes_by_abi.items() if abi != REFERENCE_ABI)
        return total_bytes - other_abis, flags

    densities = reference_densities(size_parts)
    if densities and REFERENCE_DENSITY not in densities:
        # APKPure serves one density, rarely xxhdpi; the split is ~1 % of the size.
        flags.append("reference_density_substituted")
    if any(p["kind"] == "unknown" for p in size_parts):
        flags.append("reference_part_unknown")
    if "reference_abi_missing" in flags:
        return None, flags

    kept = [p for p in size_parts
            if p["kind"] in ("base", "feature")
            or (p["kind"] == "abi" and p["qualifier"] == REFERENCE_ABI)
            or (p["kind"] == "density" and p["qualifier"] in densities)]
    return sum(p["bytes"] for p in kept), flags


def facts(path: Path, size_scope: str = "single-apk") -> ApkFacts:
    """Les faits d'un APK déposé sur le disque.

    androguard est importé ici et pas en tête de module : les huit autres unités du
    projet sont des fonctions pures sur JSON et doivent rester importables — et
    testables — sans que l'analyseur soit installé.

    Ses journaux sont coupés. En mode debug il émet plusieurs milliers de lignes par
    APK, ce qui noierait le seul flux qui compte, le JSONL sur la sortie standard.
    """
    from loguru import logger

    logger.remove()
    import androguard
    from androguard.core.apk import APK
    from androguard.core.dex import DEX

    apk = APK(str(path))
    buckets, native_by_abi = sizes(path)

    classes: list[str] = []
    domains: set[str] = set()
    methods = 0
    for raw in apk.get_all_dex():
        dex = DEX(raw)
        classes.extend(_dotted(name) for name in dex.get_classes_names())
        methods += dex.header.method_ids_size
        # Les hôtes sont reconnus dex par dex plutôt qu'à la fin : une grosse
        # application porte des centaines de milliers de chaînes, dont il ne reste
        # après filtrage que quelques dizaines. Les accumuler toutes pour n'en garder
        # que celles-là ferait tenir en mémoire le pool de chaînes complet.
        domains.update(evidence.hosts(dex.get_strings()))
    domains.update(_config_domains(apk))

    found = ApkFacts(
        credentials=keys.scan(credential_pairs(apk)),
        handle=apk.get_package(),
        version_name=apk.get_androidversion_name(),
        version_code=int(apk.get_androidversion_code() or 0) or None,
        min_sdk=_as_int(apk.get_min_sdk_version()),
        target_sdk=_as_int(apk.get_target_sdk_version()),
        permissions=sorted(set(apk.get_permissions())),
        apk_sha256=sha256(path),
        size_scope=size_scope,
        total_bytes=path.stat().st_size,
        native_abis=sorted(native_by_abi),
        native_bytes_by_abi=native_by_abi,
        dex_method_count=methods,
        class_names=sorted(set(classes)),
        domains=sorted(domains),
        analyzer_version=f"androguard {androguard.__version__}",
        **buckets,
    )
    found.size_parts = [size_part(path.name, "base", None, found)]
    return found


# `<meta-data android:name="…" android:value="…"/>`, l'attribut porte l'espace de noms
# Android complet dans l'arbre rendu par androguard.
_ANDROID_NS = "{http://schemas.android.com/apk/res/android}"

# `<string name="google_api_key">AIza…</string>` dans les ressources décompilées.
_STRING_TAG = re.compile(r'<string name="([^"]+)">([^<]*)</string>')


def credential_pairs(apk) -> list[tuple[str, str, str]]:
    """Les couples (origine, nom, valeur) où un identifiant de compte peut se trouver.

    Deux origines, et il faut les deux. Le manifeste porte ce qu'un SDK lit au
    démarrage — clé Maps, application Facebook. Les ressources portent ce que
    `google-services.json` y a déversé au build : clé API Firebase, projet, expéditeur
    FCM, qu'aucun manifeste ne montre.

    Seules les chaînes de la configuration par défaut sont lues, pas les traductions :
    une clé n'est pas localisée, et parcourir les quarante langues d'une grosse
    application multiplierait le coût pour rendre les mêmes valeurs.

    Les ressources d'un tiers sont de la donnée compilée par un outil qu'on ne
    contrôle pas : leur échec est avalé, comme celui d'un XML de `network_security_config`.
    """
    pairs: list[tuple[str, str, str]] = []
    try:
        manifest = apk.get_android_manifest_xml()
    except Exception:                                           # noqa: BLE001
        manifest = None
    for meta in (manifest.findall(".//meta-data") if manifest is not None else []):
        name = meta.get(_ANDROID_NS + "name") or ""
        value = meta.get(_ANDROID_NS + "value") or ""
        if name and value:
            pairs.append(("manifest-meta-data", name, value))

    try:
        resources = apk.get_android_resources()
        packages = resources.get_packages_names() if resources else []
    except Exception:                                           # noqa: BLE001
        packages = []
    for package in packages:
        try:
            raw = resources.get_string_resources(package)
        except Exception:                                       # noqa: BLE001
            continue
        text = (raw or b"").decode("utf-8", "replace")
        pairs.extend(("resource-string", name, value)
                     for name, value in _STRING_TAG.findall(text))
    return pairs


def _merge_credentials(parts: list[ApkFacts]) -> list[dict]:
    """Les identifiants de toutes les parts, dédoublonnés sur (famille, empreinte).

    L'empreinte porte sur la valeur entière, elle identifie donc la clé sans qu'on ait
    à la relire. C'est ce qui permet de réunir sans jamais re-mesurer.
    """
    seen: dict[tuple[str, str], dict] = {}
    for part in parts:
        for found in part.credentials:
            seen.setdefault((found["id"], found["value_sha256"]), found)
    return sorted(seen.values(), key=lambda f: (f["id"], f["value_sha256"]))


def merge(parts: list[ApkFacts], total_bytes: int, apk_sha256: str,
          size_scope: str = "merged-xapk",
          size_parts: list[dict] | None = None) -> ApkFacts:
    """Les faits d'un ensemble de parts : identité du base, poids et code de toutes.

    `parts[0]` est le base. C'est de lui que vient l'identité — handle, version_name,
    niveaux de SDK — et de lui seul : un split porte le même `version_code` mais aucun
    `version_name`. Le prendre pour référence marquerait `missing_version_name` un
    snapshot d'application qui en a un.

    Tout le reste s'agrège. Mesuré sur le XAPK Carrefour 23.6.0 : le base déclare
    `native_bytes: 0` et aucune ABI, les 17,4 Mo de code natif et l'unique `arm64-v8a`
    sont dans `config.arm64_v8a.apk`. Analyser le base seul publierait donc « application
    sans code natif », ce qui est faux, et perdrait le poste de poids qui porte le signal
    éco-conception du projet.

    Les classes se réunissent pour la même raison, dans l'autre sens : un split de
    fonctionnalité embarque son propre dex, donc ses propres classes de pisteur, que le
    base ne porte pas. Ne pas les lire ne produirait pas une erreur mais un pisteur en
    moins — un faux négatif silencieux, la pire erreur pour ce projet.
    """
    base = parts[0]
    return replace(
        base,
        permissions=sorted({p for part in parts for p in part.permissions}),
        apk_sha256=apk_sha256,
        size_scope=size_scope,
        total_bytes=total_bytes,
        dex_bytes=sum(part.dex_bytes for part in parts),
        res_bytes=sum(part.res_bytes for part in parts),
        assets_bytes=sum(part.assets_bytes for part in parts),
        native_bytes=sum(part.native_bytes for part in parts),
        native_abis=sorted({abi for part in parts for abi in part.native_abis}),
        native_bytes_by_abi=_sum_by_abi(parts),
        size_parts=size_parts or [],
        dex_method_count=sum(part.dex_method_count for part in parts),
        class_names=sorted({name for part in parts for name in part.class_names}),
        domains=sorted({host for part in parts for host in part.domains}),
        # Les identifiants de compte se réunissent comme les classes, et pour la même
        # raison : un split de fonctionnalité porte ses propres ressources, donc
        # parfois la seule occurrence d'une clé.
        #
        # **Ils se réunissent tels quels, jamais en rejouant `keys.scan`.** La première
        # version le rejouait sur `value or value_prefix`, et un secret n'ayant pas de
        # `value` par construction, elle re-empreignait les six caractères du préfixe :
        # les clés Maps de France Travail, BeReal et Deliveroo sortaient avec la même
        # empreinte — celle de la chaîne « AIzaSy » — et le corpus annonçait un compte
        # partagé qui n'existait pas. La troncature est irréversible : ce qui est déjà
        # tronqué ne peut plus être re-mesuré, seulement recopié.
        credentials=_merge_credentials(parts),
    )


def facts_xapk(path: Path, work_dir: Path) -> tuple[ApkFacts, list[str]]:
    """Faits d'un XAPK : explosion, analyse de chaque part, fusion. Rend aussi ses défauts.

    `apk_sha256` est celui du XAPK lui-même, pas d'un APK : il n'y a pas de binaire
    unique à empreindre. Il reste vérifiable — un tiers qui retélécharge le même XAPK
    retrouve la somme — mais il n'est **pas** comparable au sha publié par Exodus, qui
    analyse un APK seul. D'où le drapeau `merged_xapk` sur le snapshot : la comparabilité
    des empreintes est une propriété qu'on déclare, pas qu'on suppose.
    """
    from . import xapk

    bundle = xapk.explode(path, work_dir)
    parts = [facts(part) for part in bundle.parts]
    flags = ["merged_xapk"]

    # Une part qui n'appartient pas au même paquet est un XAPK mal fabriqué par le
    # miroir. L'identité venant du base, la série ne part pas sur la mauvaise
    # application ; mais son poids et ses classes seraient ceux de deux applications
    # mélangées, ce qui doit se lire dans la donnée et non se perdre dans un log.
    if {part.handle for part in parts} - {parts[0].handle}:
        flags.append("xapk_handle_mismatch")

    size_parts = [size_part(member.name,
                            *xapk.classify_part(member.name, is_base=(i == 0)), part)
                  for i, (member, part) in enumerate(zip(bundle.parts, parts))]
    return merge(parts, bundle.parts_bytes, sha256(path), size_parts=size_parts), flags


# `<domain includeSubdomains="true">exemple.fr</domain>` dans un network_security_config.
_DOMAIN_TAG = re.compile(r"<domain[^>]*>([^<]+)</domain>")


def _config_domains(apk) -> set[str]:
    """Les domaines déclarés dans le `network_security_config` de l'application.

    Ils valent mieux que ceux des chaînes : ils sont écrits par le développeur, pas
    déduits d'une expression régulière, et une application qui épingle un certificat y
    nomme l'hôte qu'elle contacte vraiment. Ils sont peu nombreux et souvent absents,
    d'où leur rôle de complément et non de source principale.

    La recherche porte sur `res/xml/`, l'emplacement conventionnel, et non sur toutes
    les ressources : un gros APK porte des milliers de XML compilés, tous décodables et
    aucun pertinent. Un XML illisible est sauté — c'est de la donnée tierce compilée par
    un outil qu'on ne contrôle pas, et son échec ne doit pas emporter l'analyse.
    """
    from androguard.core.axml import AXMLPrinter

    found: set[str] = set()
    for name in apk.get_files():
        if not (name.startswith("res/xml/") and name.endswith(".xml")):
            continue
        try:
            text = AXMLPrinter(apk.get_file(name)).get_xml().decode("utf-8", "replace")
        except Exception:                                       # noqa: BLE001
            continue
        if "domain-config" not in text and "<domain" not in text:
            continue
        found.update(host.strip().lower() for host in _DOMAIN_TAG.findall(text))
    return {host for host in found if host}


def _dotted(name: str) -> str:
    """Lcom/foo/Bar; -> com.foo.Bar, la forme sur laquelle portent les signatures."""
    return name.strip("L;").replace("/", ".")


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def snapshot_for(entry: CorpusEntry,
                 apk: ApkFacts,
                 tracker_ids: list[int],
                 tracker_names: dict[int, str],
                 catalog_version: str,
                 observed_at: str,
                 acquisition: str = "manual") -> tuple[Snapshot | None, str | None]:
    """Les faits d'un APK -> un snapshot apk-static, ou un refus motivé.

    Le handle du manifeste doit être celui du corpus. Un APK déposé à la main dans le
    mauvais dossier, ou un paquet renommé par un miroir, fabriquerait une série qui
    mélange deux applications — le même piège qu'au backfill git, ici sans garde-fou
    naturel puisque c'est un humain qui a choisi le fichier.

    Contrairement aux sources Exodus, `catalog_version` est renseigné : on sait avec
    quelles signatures on vient de travailler. C'est ce qui rendra les deltas de
    pisteurs enfin exploitables, une fois deux analyses statiques dans la même série.
    """
    from .catalog import name_trackers

    if apk.handle != entry.handle:
        return None, (f"le manifeste porte {apk.handle!r} et non {entry.handle!r}")

    flags: list[str] = []
    if not apk.version_name:
        flags.append("missing_version_name")
    if not tracker_ids:
        flags.append("empty_trackers")
    if len(apk.native_abis) > 1:
        # Quatre ABI livrées à un appareil qui n'en utilise qu'une : du poids inutile par
        # construction, et le vrai signal éco-conception du projet.
        flags.append("multi_abi")
    reference, reference_flags = reference_bytes(apk.size_scope, apk.total_bytes,
                                                 apk.size_parts, apk.native_bytes_by_abi)
    flags.extend(reference_flags)

    return Snapshot(
        handle=entry.handle,
        slug=entry.slug,
        app_name=entry.app_name,
        version_name=apk.version_name,
        version_code=apk.version_code,
        source="apk-static",
        acquisition=acquisition,
        apk_sha256=apk.apk_sha256,
        analyzer="apklab",
        analyzer_version=apk.analyzer_version,
        catalog_version=catalog_version,
        observed_at=observed_at,
        permissions=apk.permissions,
        trackers=name_trackers(tracker_ids, tracker_names),
        size_scope=apk.size_scope,
        total_bytes=apk.total_bytes,
        dex_bytes=apk.dex_bytes,
        res_bytes=apk.res_bytes,
        assets_bytes=apk.assets_bytes,
        native_bytes=apk.native_bytes,
        native_abis=apk.native_abis,
        native_bytes_by_abi=apk.native_bytes_by_abi,
        size_parts=apk.size_parts,
        reference_bytes=reference,
        dex_method_count=apk.dex_method_count,
        min_sdk=apk.min_sdk,
        target_sdk=apk.target_sdk,
        quality_flags=flags,
        provenance_ref=f"sha256:{apk.apk_sha256[:12]}",
    ), None
