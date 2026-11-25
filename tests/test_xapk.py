"""Explosion d'un XAPK. Zip pur, aucun réseau, aucun androguard.

Les cas viennent des deux XAPK réels mesurés le 2026-09-09 : Carrefour 23.6.0, deux
parts dont un split natif et pas d'icône ; la calculatrice de Simple Mobile Tools 5.12.0,
dix-neuf parts dont une icône. Les noms de leurs membres sont ceux d'APKPure, pas ceux
que le design supposait.
"""

import json
import zipfile

import pytest

from apklab import xapk
from conftest import (HANDLE_APK, LIB_BYTES, apk_variant, build_xapk, xapk_manifest)


@pytest.fixture
def parts(tmp_path):
    """Un base et un split, sous les noms qu'emploie APKPure."""
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    base = apk_variant(atelier / f"{HANDLE_APK}.apk")
    split = apk_variant(atelier / "config.arm64_v8a.apk",
                        add={"lib/arm64-v8a/libdemo.so": b"\0" * LIB_BYTES},
                        drop=("classes.dex",))
    return base, split


# --------------------------------------------------------------------- is_xapk

def test_is_xapk_vrai_sur_une_archive_qui_contient_des_apk(xapk_deux_parts):
    assert xapk.is_xapk(xapk_deux_parts)


def test_is_xapk_faux_sur_un_apk(tmp_path):
    """Un APK est lui-même un zip, mais il ne contient jamais de membre .apk. Le test
    porte donc sur le contenu, jamais sur le suffixe."""
    assert not xapk.is_xapk(apk_variant(tmp_path / "app.apk"))


def test_is_xapk_juge_le_contenu_et_non_le_suffixe(tmp_path, parts):
    base, split = parts
    déguisé = build_xapk(tmp_path / "app.apk", {base.name: base, split.name: split})
    assert xapk.is_xapk(déguisé)


def test_is_xapk_faux_sur_un_fichier_qui_n_est_pas_un_zip(tmp_path):
    path = tmp_path / "coupe.xapk"
    path.write_bytes(b"PK\x03\x04 coupe au milieu")
    assert not xapk.is_xapk(path)


# --------------------------------------------------------------------- explode

def test_explode_designe_le_base_par_le_manifeste(tmp_path, xapk_deux_parts):
    """APKPure nomme le base d'après le paquet et ses splits `config.*` : chercher
    `base.apk` ou le préfixe `split_config.` ne trouverait ni l'un ni l'autre."""
    bundle = xapk.explode(xapk_deux_parts, tmp_path / "parts")

    assert bundle.base.name == f"{HANDLE_APK}.apk"
    assert [p.name for p in bundle.splits] == ["config.arm64_v8a.apk"]
    assert bundle.parts[0] == bundle.base


def test_explode_n_extrait_que_les_apk(tmp_path, xapk_deux_parts):
    """Un XAPK peut embarquer des Android/obb/* de plusieurs gigaoctets qui ne portent
    aucun fait cherché ici."""
    dest = tmp_path / "parts"
    xapk.explode(xapk_deux_parts, dest)
    assert sorted(p.name for p in dest.iterdir()) == [
        "config.arm64_v8a.apk", f"{HANDLE_APK}.apk"]


def test_explode_ignore_les_chemins_de_membre(tmp_path, parts):
    """Les membres sont extraits sous leur nom de base : un chemin remontant hors du
    répertoire cible n'a aucun effet, sans avoir à s'en défendre."""
    base, split = parts
    archive = tmp_path / "malveillant.xapk"
    with zipfile.ZipFile(archive, "w") as out:
        out.write(base, f"../../{base.name}")
        out.write(split, f"Android/obb/{HANDLE_APK}/{split.name}")

    dest = tmp_path / "parts"
    bundle = xapk.explode(archive, dest)

    assert {p.parent for p in bundle.parts} == {dest}
    assert not (tmp_path / base.name).exists()


def test_explode_poids_est_la_somme_des_parts_et_non_du_xapk(tmp_path, xapk_deux_parts):
    """`icon.png`, le manifeste et les en-têtes du zip sont l'emballage d'APKPure, pas
    l'application : les compter ferait dépendre le poids publié du conditionnement du
    miroir."""
    bundle = xapk.explode(xapk_deux_parts, tmp_path / "parts")
    somme = sum(p.stat().st_size for p in bundle.parts)

    assert bundle.parts_bytes == somme
    assert bundle.parts_bytes < xapk_deux_parts.stat().st_size


def test_explode_lit_le_manifeste(tmp_path, xapk_deux_parts):
    bundle = xapk.explode(xapk_deux_parts, tmp_path / "parts")
    assert bundle.manifest["package_name"] == HANDLE_APK
    assert bundle.manifest["split_configs"] == ["config.arm64_v8a"]


# ------------------------------------------------- désignation du base sans manifeste

def test_sans_manifeste_le_membre_sans_prefixe_de_split_est_le_base(tmp_path, parts):
    base, split = parts
    archive = build_xapk(tmp_path / "app.xapk", {base.name: base, split.name: split})
    assert xapk.explode(archive, tmp_path / "parts").base.name == base.name


def test_sans_manifeste_ni_nom_parlant_le_plus_gros_est_le_base(tmp_path, parts):
    """Le base porte tout le dex de l'application — 145 Mo contre 17 Mo pour le split
    natif de Carrefour. L'écart n'est jamais serré."""
    base, split = parts
    archive = build_xapk(tmp_path / "app.xapk", {"un.apk": base, "deux.apk": split})
    bundle = xapk.explode(archive, tmp_path / "parts")
    assert bundle.base.name == "un.apk"
    assert bundle.base.stat().st_size > bundle.splits[0].stat().st_size


def test_manifeste_illisible_ne_fait_pas_echouer(tmp_path, parts):
    base, split = parts
    archive = build_xapk(tmp_path / "app.xapk", {base.name: base, split.name: split},
                         extra={"manifest.json": b"{ pas du json"})
    bundle = xapk.explode(archive, tmp_path / "parts")
    assert bundle.manifest is None
    assert bundle.base.name == base.name


def test_manifeste_qui_designe_un_membre_absent_retombe_sur_l_heuristique(tmp_path, parts):
    base, split = parts
    manifeste = xapk_manifest("disparu.apk", split.name)
    archive = build_xapk(tmp_path / "app.xapk", {base.name: base, split.name: split},
                         manifeste)
    assert xapk.explode(archive, tmp_path / "parts").base.name == base.name


def test_manifeste_avec_un_chemin_de_base(tmp_path, parts):
    base, split = parts
    manifeste = {"split_apks": [{"file": f"apks/{base.name}", "id": "base"}]}
    archive = build_xapk(tmp_path / "app.xapk", {base.name: base, split.name: split},
                         manifeste)
    assert xapk.explode(archive, tmp_path / "parts").base.name == base.name


# ---------------------------------------------------------------------- refus

def test_archive_sans_apk_est_refusee(tmp_path):
    archive = tmp_path / "vide.xapk"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("manifest.json", json.dumps({"package_name": HANDLE_APK}))
    with pytest.raises(xapk.XapkError, match="aucun APK"):
        xapk.explode(archive, tmp_path / "parts")


def test_deux_membres_de_meme_nom_de_base_sont_refuses(tmp_path, parts):
    """Aplatir les chemins rendrait le second silencieusement invisible, et son poids
    comme ses classes manqueraient à la fusion."""
    base, split = parts
    archive = tmp_path / "collision.xapk"
    with zipfile.ZipFile(archive, "w") as out:
        out.write(base, split.name)
        out.write(split, f"Android/obb/{split.name}")

    with pytest.raises(xapk.XapkError, match="deux membres"):
        xapk.explode(archive, tmp_path / "parts")


# --------------------------------------------------------------- classify_part

def test_the_base_is_the_base_whatever_its_name():
    assert xapk.classify_part("com.carrefour.fid.android.apk", is_base=True) == ("base", None)


def test_a_config_arm64_split_is_an_abi_part():
    assert xapk.classify_part("config.arm64_v8a.apk", False) == ("abi", "arm64-v8a")
    assert xapk.classify_part("config.armeabi_v7a.apk", False) == ("abi", "armeabi-v7a")
    assert xapk.classify_part("config.x86_64.apk", False) == ("abi", "x86_64")


def test_a_config_xxhdpi_split_is_a_density_part():
    assert xapk.classify_part("config.xxhdpi.apk", False) == ("density", "xxhdpi")
    assert xapk.classify_part("config.hdpi.apk", False) == ("density", "hdpi")


def test_a_config_fr_split_is_a_language_part():
    assert xapk.classify_part("config.fr.apk", False) == ("language", "fr")


def test_play_split_config_names_classify_like_apkpure_ones():
    """Play names its splits `split_config.*`; read as a feature, an ABI split would
    enter the reference size whatever its ABI."""
    assert xapk.classify_part("split_config.arm64_v8a.apk", False) == ("abi", "arm64-v8a")
    assert xapk.classify_part("split_config.xxhdpi.apk", False) == ("density", "xxhdpi")


def test_a_split_without_config_qualifier_is_a_feature_part():
    assert xapk.classify_part("split_camera.apk", False) == ("feature", "camera")


def test_a_feature_module_config_split_classifies_by_its_qualifier():
    """Seen on the 2026-09-23 pass: `VoipTwilio.config.armeabi_v7a.apk`. As a whole
    feature it would add every ABI of the module to the reference."""
    assert xapk.classify_part("VoipTwilio.config.armeabi_v7a.apk", False) \
        == ("abi", "armeabi-v7a")
    assert xapk.classify_part("split_camera.config.xxhdpi.apk", False) == ("density", "xxhdpi")


def test_an_apkpure_feature_module_without_prefix_is_a_feature():
    """APKPure names Uber's modules `EatsDFMAPI.apk`, no `split_` prefix."""
    assert xapk.classify_part("EatsDFMAPI.apk", False) == ("feature", "EatsDFMAPI")
    assert xapk.classify_part("BarcodeScanner.apk", False) == ("feature", "BarcodeScanner")


def test_an_i18n_module_is_a_language_part():
    assert xapk.classify_part("i18n_ko.apk", False) == ("language", "ko")


def test_an_unrecognised_name_is_unknown_not_dropped():
    assert xapk.classify_part("config.ldrtl.apk", False) == ("unknown", "ldrtl")
    assert xapk.classify_part("icons.v2.apk", False) == ("unknown", None)
