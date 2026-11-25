"""Explosion d'un XAPK APKPure en ses parts. Pure manipulation de zip, aucun réseau.

Un XAPK est un zip qui contient plusieurs APK : le base et ses splits de configuration.
Mesuré le 2026-09-09, APKPure sert du XAPK pour **tout**, pas seulement pour les grosses
applications — la calculatrice de Simple Mobile Tools, 7 Mo, en arrive avec dix-neuf
parts. La fusion n'est donc pas un cas particulier à traiter, c'est le cas courant.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "manifest.json"

# Préfixes de nom de split, dans les deux conventions rencontrées. Utilisés seulement en
# secours : le manifeste du XAPK désigne le base explicitement.
SPLIT_PREFIXES = ("config.", "split_config.", "split_")

CONFIG_PREFIXES = ("split_config.", "config.")

# File names use `_` where Android ABI names use `-`.
ABI_QUALIFIERS = {"arm64_v8a": "arm64-v8a", "armeabi_v7a": "armeabi-v7a",
                  "armeabi": "armeabi", "x86": "x86", "x86_64": "x86_64",
                  "mips": "mips", "mips64": "mips64"}
DENSITY_QUALIFIERS = ("ldpi", "mdpi", "tvdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi",
                      "nodpi", "anydpi")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2}|_r[A-Z]{2})?$")


class XapkError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bundle:
    base: Path
    splits: list[Path]
    manifest: dict | None
    parts_bytes: int

    @property
    def parts(self) -> list[Path]:
        """Le base d'abord : c'est de lui que la fusion tire l'identité."""
        return [self.base, *self.splits]


def is_xapk(path: Path) -> bool:
    """Vrai si l'archive contient des APK. Le suffixe ne décide pas.

    Un APK est lui-même un zip, mais il ne contient jamais de membre `.apk` : le test
    porte donc sur le contenu et un `.apk` renommé en `.xapk`, ou l'inverse, est traité
    pour ce qu'il est. C'est apkeep qui choisit le suffixe, et un dépôt manuel n'a
    aucune raison de respecter sa convention.
    """
    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as archive:
        return any(name.endswith(".apk") for name in archive.namelist())


def classify_part(name: str, is_base: bool) -> tuple[str, str | None]:
    """`(kind, qualifier)` of a bundle part, read from its file name alone.

    A feature module's own config split (`VoipTwilio.config.armeabi_v7a`) classifies by
    its qualifier, so the reference keeps only its arm64/xxhdpi one.
    """
    if is_base:
        return "base", None
    stem = name.removesuffix(".apk")
    for prefix in CONFIG_PREFIXES:
        if stem.startswith(prefix):
            return _config_kind(stem[len(prefix):])
    if ".config." in stem:
        return _config_kind(stem.split(".config.", 1)[1])
    if "." in stem:
        return "unknown", None
    if stem.startswith("i18n_") and _LANGUAGE.match(stem.removeprefix("i18n_")):
        return "language", stem.removeprefix("i18n_")
    # APKPure drops the `split_` prefix on feature modules: `EatsDFMAPI.apk`.
    return "feature", stem.removeprefix("split_")


def _config_kind(qualifier: str) -> tuple[str, str | None]:
    if qualifier in ABI_QUALIFIERS:
        return "abi", ABI_QUALIFIERS[qualifier]
    if qualifier in DENSITY_QUALIFIERS:
        return "density", qualifier
    if _LANGUAGE.match(qualifier):
        return "language", qualifier
    return "unknown", qualifier


def explode(path: Path, dest: Path) -> Bundle:
    """Extrait les APK d'un XAPK dans `dest` et désigne le base.

    Seuls les membres `.apk` et le manifeste sont extraits, sous leur nom de base. Deux
    raisons : un XAPK peut embarquer des `Android/obb/*` de plusieurs gigaoctets qui ne
    portent aucun fait cherché ici, et n'extraire que des noms de fichier aplatis retire
    tout intérêt à un chemin de membre malveillant (`../`, chemin absolu) sans avoir à
    s'en défendre.
    """
    dest.mkdir(parents=True, exist_ok=True)
    manifest: dict | None = None
    apks: dict[str, Path] = {}

    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename).name
            if name == MANIFEST_NAME:
                manifest = _manifest(archive.read(info))
            elif name.endswith(".apk"):
                if name in apks:
                    raise XapkError(f"deux membres nommés {name} dans {path.name}")
                target = dest / name
                with archive.open(info) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
                apks[name] = target

    if not apks:
        raise XapkError(f"aucun APK dans {path.name}")

    base = apks.pop(_base_name(manifest, apks))
    splits = [apks[name] for name in sorted(apks)]
    # Le poids retenu est la somme des parts et non la taille du XAPK : `icon.png`, le
    # manifeste et les en-têtes du zip sont l'emballage d'APKPure, pas l'application.
    # Les compter ferait dépendre le poids publié du conditionnement du miroir. L'écart
    # est mince — 2 484 octets sur les 155 Mo de Carrefour — mais il n'a pas de sens.
    parts_bytes = base.stat().st_size + sum(p.stat().st_size for p in splits)
    return Bundle(base=base, splits=splits, manifest=manifest, parts_bytes=parts_bytes)


def _manifest(raw: bytes) -> dict | None:
    """Le manifeste du XAPK, ou rien. Son absence n'est pas une erreur.

    Il n'est jamais une source de faits : sur Carrefour 23.6.0 il déclare 15 permissions
    quand le manifeste du base en porte 36. C'est une fiche de présentation d'APKPure,
    utile pour désigner le base, pas pour décrire l'application.
    """
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _base_name(manifest: dict | None, apks: dict[str, Path]) -> str:
    """Le nom du membre qui est le base.

    Le manifeste le désigne par `split_apks[].id == "base"`, et c'est la seule méthode
    fiable : APKPure nomme le base d'après le paquet — `com.carrefour.fid.android.apk` —
    et non `base.apk`, et ses splits `config.arm64_v8a.apk` et non
    `split_config.arm64_v8a.apk`. Chercher un nom ou un préfixe fixe ne trouverait ni
    l'un ni l'autre.

    À défaut de manifeste, deux replis : l'unique membre qui ne porte pas de préfixe de
    split, puis le plus gros. Le base porte tout le dex de l'application — 145 Mo contre
    17 Mo pour le split natif de Carrefour, 5 Mo contre 200 Ko pour les splits de langue
    de la calculatrice — l'écart n'est jamais serré.
    """
    for entry in (manifest or {}).get("split_apks") or []:
        if isinstance(entry, dict) and entry.get("id") == "base":
            name = PurePosixPath(str(entry.get("file", ""))).name
            if name in apks:
                return name

    nus = [name for name in apks if not name.startswith(SPLIT_PREFIXES)]
    if len(nus) == 1:
        return nus[0]
    return max(apks, key=lambda name: apks[name].stat().st_size)
