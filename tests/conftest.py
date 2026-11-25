"""Fixtures partagées : un faux dépôt de site et un faux catalogue Exodus."""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

TRACKER_NAMES = {1: "T1", 2: "T2", 3: "T3"}

FIXTURE_APK = (Path(__file__).parent / "fixtures" / "apk"
               / "org.fdroid.fdroid.privileged_2130.apk")
HANDLE_APK = "org.fdroid.fdroid.privileged"

# Le corps de la bibliothèque native du split. Stocké sans compression comme APKPure
# stocke les parts d'un XAPK, donc son poids compressé est exactement sa longueur : la
# fusion se vérifie sur un nombre choisi et non sur un nombre observé.
LIB_BYTES = 4096
LIB_PAYLOAD = b"\x7fELF" + b"\0" * (LIB_BYTES - 4)


def apk_variant(dest: Path, *, add: dict[str, bytes] | None = None,
                drop: tuple[str, ...] = ()) -> Path:
    """Une variante de l'APK de fixture : membres ajoutés, membres retirés.

    L'archive est reconstruite plutôt qu'augmentée, pour pouvoir aussi enlever : un vrai
    split de configuration ne porte pas de dex, et laisser celui du base dans le split
    ferait compter son poids deux fois par la fusion.

    Le manifeste binaire, lui, est toujours conservé : c'est ce qui rend la variante
    réellement lisible par androguard. Un APK synthétisé de zéro ne le serait pas, son
    AndroidManifest.xml est du XML compilé.
    """
    with zipfile.ZipFile(FIXTURE_APK) as source, zipfile.ZipFile(dest, "w") as out:
        for info in source.infolist():
            if drop and info.filename.endswith(drop):
                continue
            out.writestr(info, source.read(info))
        for name, payload in (add or {}).items():
            out.writestr(name, payload)
    return dest


def build_xapk(path: Path, parts: dict[str, Path], manifest: dict | None = None,
               extra: dict[str, bytes] | None = None) -> Path:
    """Un XAPK : ses parts stockées sans compression, comme APKPure les stocke."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for name, source in parts.items():
            archive.write(source, name)
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
        if manifest is not None:
            archive.writestr("manifest.json", json.dumps(manifest))
    return path


def xapk_manifest(base_name: str, *split_names: str) -> dict:
    """Le manifeste d'un XAPK, au format mesuré sur APKPure le 2026-09-09."""
    return {
        "xapk_version": 2,
        "package_name": HANDLE_APK,
        "version_code": "2130",
        "version_name": "0.2.13",
        "split_configs": [name.removesuffix(".apk") for name in split_names],
        "split_apks": [{"file": base_name, "id": "base"},
                       *({"file": name, "id": name.removesuffix(".apk")}
                         for name in split_names)],
    }


@pytest.fixture
def xapk_deux_parts(tmp_path) -> Path:
    """Le XAPK synthétique du design : un base, et un split de configuration natif.

    Calqué sur le XAPK réel de Carrefour 23.6.0 — un base qui déclare `native_bytes: 0`
    et aucune ABI, un `config.arm64_v8a.apk` qui porte les seules bibliothèques natives
    et la seule ABI. C'est exactement le cas que la fusion doit rattraper : analyser le
    base seul publierait « application sans code natif », ce qui est faux, et perdrait le
    poste de poids qui porte le signal éco-conception.

    Les noms suivent APKPure et non le design : le base porte le nom du paquet et non
    `base.apk`, le split est `config.arm64_v8a.apk` et non `split_config.arm64_v8a.apk`.
    """
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    base = apk_variant(atelier / f"{HANDLE_APK}.apk")
    split = apk_variant(atelier / "config.arm64_v8a.apk",
                        add={"lib/arm64-v8a/libdemo.so": LIB_PAYLOAD},
                        drop=("classes.dex", "resources.arsc"))
    return build_xapk(tmp_path / f"{HANDLE_APK}@0.2.13.xapk",
                      {base.name: base, split.name: split},
                      xapk_manifest(base.name, split.name),
                      extra={"icon.png": b"\x89PNG" + b"\0" * 100})


def fiche(handle: str, version: str | None, report_date: str | None,
          permissions: list[str], trackers: list[int]) -> dict:
    """Une fiche du site, au format de public/data/compare/<slug>.json."""
    return {"app_name": handle.split(".")[-1].title(), "handle": handle,
            "version": version, "report_date": report_date,
            "permissions": permissions, "trackers": trackers}


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """Un dépôt de site minimal, sans git."""
    compare = tmp_path / "public" / "data" / "compare"
    compare.mkdir(parents=True)
    (compare / "trackers.json").write_text(
        json.dumps([{"id": i, "name": n} for i, n in TRACKER_NAMES.items()]), "utf-8")
    return tmp_path


def write_fiche(site: Path, slug: str, doc) -> Path:
    path = site / "public" / "data" / "compare" / f"{slug}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), "utf-8")
    return path


# Environnement git hermétique. Sans ça les tests héritent du ~/.gitconfig du poste :
# un commit.gpgsign à true fait demander sa passphrase à GPG et le test se bloque
# indéfiniment, alors qu'il passe en CI où aucune clé n'est configurée.
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], env=GIT_ENV,
                          capture_output=True, text=True, check=True, timeout=30)
    return proc.stdout


def init_repo(repo: Path) -> None:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
