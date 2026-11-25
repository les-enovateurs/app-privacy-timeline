"""Analyse statique, sur un vrai APK.

La fixture est `org.fdroid.fdroid.privileged` 0.2.13, 46 Ko, Apache-2.0 : un binaire
réel, minuscule, dont la licence autorise explicitement la copie — d'où l'exception à la
règle « aucun APK versionné », qui vise la redistribution de binaires tiers.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from xml.etree import ElementTree

from apklab import analyze, keys
from apklab.corpus import CorpusEntry
from conftest import apk_variant

APK = (Path(__file__).parent / "fixtures" / "apk"
       / "org.fdroid.fdroid.privileged_2130.apk")
HANDLE = "org.fdroid.fdroid.privileged"
ENTRY = CorpusEntry(slug="fdroid-privileged", handle=HANDLE, app_name="F-Droid Privileged")


@pytest.fixture(scope="module")
def apk():
    return analyze.facts(APK)


def test_identite_lue_du_manifeste(apk):
    assert apk.handle == HANDLE
    assert apk.version_name == "0.2.13"
    assert apk.version_code == 2130


def test_permissions_du_manifeste(apk):
    assert apk.permissions == ["android.permission.DELETE_PACKAGES",
                               "android.permission.INSTALL_PACKAGES"]


def test_niveaux_d_api(apk):
    """min_sdk qui monte = obsolescence logicielle mesurable ; target_sdk décide des
    restrictions de vie privée réellement appliquées."""
    assert apk.min_sdk == 8
    assert apk.target_sdk == 25


def test_poids_total_est_celui_du_fichier(apk):
    """Ce que l'utilisateur télécharge, en-têtes d'archive compris — pas la somme des
    entrées, qui ne fait pas le compte."""
    assert apk.total_bytes == APK.stat().st_size == 45943


def test_poids_par_nature(apk):
    assert apk.dex_bytes > 0
    assert apk.res_bytes > 0
    assert apk.assets_bytes == 0
    assert apk.native_bytes == 0
    assert apk.native_abis == []
    assert apk.dex_bytes + apk.res_bytes < apk.total_bytes


def test_inventaire_dex(apk):
    assert apk.dex_method_count == 185
    assert len(apk.class_names) == 23
    assert "android.content.pm.IPackageDeleteObserver" in apk.class_names


def test_classes_en_notation_pointee(apk):
    """Lcom/foo/Bar; -> com.foo.Bar : la forme sur laquelle portent les signatures."""
    assert all("/" not in name and not name.endswith(";") for name in apk.class_names)


def test_empreinte_du_binaire_analyse(apk):
    """Rend l'étude reproductible par un tiers sans redistribuer l'APK."""
    assert apk.apk_sha256 == \
        "1008525a17b4f6a93ac690f9c50dcb675b6bebf53d2879dbc98ba65a1cb2e28d"


def test_analyseur_identifie(apk):
    """Un écart entre deux snapshots peut venir de l'analyseur et non de l'application."""
    assert apk.analyzer_version.startswith("androguard ")


# ---------------------------------------------------------------------- snapshot

def test_snapshot_apk_static_porte_le_poids_et_le_catalogue(apk):
    snap, refus = analyze.snapshot_for(ENTRY, apk, [1], {1: "Teemo"},
                                       "2026-09-09/430/sha256:abc123abc123",
                                       "2026-09-09T10:00:00+00:00")
    assert refus is None
    assert snap.source == "apk-static"
    assert snap.acquisition == "manual"
    assert snap.size_scope == "single-apk"
    assert snap.total_bytes == 45943
    # Renseigné, contrairement aux sources Exodus : c'est ce qui rendra les deltas de
    # pisteurs exploitables entre deux analyses statiques.
    assert snap.catalog_version == "2026-09-09/430/sha256:abc123abc123"
    assert snap.trackers == [{"id": 1, "name": "Teemo"}]


def test_snapshot_refuse_un_manifeste_qui_n_est_pas_celui_du_corpus(apk):
    """Un APK déposé dans le mauvais dossier mélangerait deux applications dans une
    série ; ici c'est un humain qui a choisi le fichier, il n'y a pas d'autre garde-fou."""
    autre = CorpusEntry(slug="carrefour", handle="com.carrefour.fid.android",
                        app_name="Carrefour")
    snap, refus = analyze.snapshot_for(autre, apk, [], {},
                                       "v", "2026-09-09T10:00:00+00:00")
    assert snap is None
    assert HANDLE in refus


def test_absence_de_pisteur_est_marquee(apk):
    snap, _ = analyze.snapshot_for(ENTRY, apk, [], {}, "v", "2026-09-09T10:00:00+00:00")
    assert "empty_trackers" in snap.quality_flags


def test_multi_abi_est_marque(apk):
    """Quatre ABI livrées à un appareil qui n'en utilise qu'une : du poids inutile par
    construction, indépendamment de la croissance de l'application."""
    apk.native_abis = ["arm64-v8a", "armeabi-v7a", "x86", "x86_64"]
    snap, _ = analyze.snapshot_for(ENTRY, apk, [1], {}, "v", "2026-09-09T10:00:00+00:00")
    assert "multi_abi" in snap.quality_flags
    apk.native_abis = []


def test_poids_par_nature_lu_de_l_archive_seule():
    """sizes() n'a besoin ni d'androguard ni du manifeste : c'est de la lecture de zip."""
    buckets, native_by_abi = analyze.sizes(APK)
    assert buckets["dex_bytes"] > 0
    assert native_by_abi == {}


def test_native_bytes_are_counted_per_abi(tmp_path):
    variant = apk_variant(tmp_path / "multi.apk",
                          add={"lib/arm64-v8a/liba.so": b"\0" * 4096,
                               "lib/x86/liba.so": b"\0" * 100})
    buckets, native_by_abi = analyze.sizes(variant)
    assert native_by_abi == {"arm64-v8a": 4096, "x86": 100}
    assert buckets["native_bytes"] == 4196


def test_a_single_apk_is_one_base_part(apk):
    assert apk.size_parts == [{
        "name": APK.name, "kind": "base", "qualifier": None, "bytes": 45943,
        "dex_bytes": apk.dex_bytes, "res_bytes": apk.res_bytes,
        "assets_bytes": 0, "native_bytes": 0}]
    assert apk.native_bytes_by_abi == {}


def test_l_apk_de_fixture_ne_porte_aucun_identifiant_de_compte(apk):
    """Une extension F-Droid n'embarque ni SDK ni compte : la sortie doit être vide, et
    non « quelques faux identifiants » tirés de ses deux ressources."""
    assert apk.credentials == []


class FauxResources:
    """Les ressources d'un APK, au format rendu par androguard : du XML décompilé."""

    def __init__(self, strings: dict[str, str]):
        self._xml = ("<resources>" + "".join(
            f'<string name="{name}">{value}</string>'
            for name, value in strings.items()) + "</resources>").encode("utf-8")

    def get_packages_names(self):
        return ["com.app.x"]

    def get_string_resources(self, package):
        return self._xml


class FauxApk:
    """Un APK réduit à ce que lit `credential_pairs` : un manifeste et des ressources.

    L'APK de fixture ne peut pas servir ici : injecter une `meta-data` ou une ressource
    demanderait de recompiler un manifeste binaire et un `resources.arsc`, ce qu'aucun
    outil du projet ne sait faire. Le vrai binaire valide l'absence de faux positif, ce
    double valide la lecture des deux origines.
    """

    def __init__(self, metas: dict[str, str], strings: dict[str, str]):
        ns = analyze._ANDROID_NS
        self._manifest = ElementTree.Element("manifest")
        application = ElementTree.SubElement(self._manifest, "application")
        for name, value in metas.items():
            ElementTree.SubElement(application, "meta-data",
                                   {ns + "name": name, ns + "value": value})
        self._resources = FauxResources(strings)

    def get_android_manifest_xml(self):
        return self._manifest

    def get_android_resources(self):
        return self._resources


def test_les_deux_origines_sont_lues():
    """Le manifeste porte ce qu'un SDK lit au démarrage, les ressources ce que
    `google-services.json` y a déversé au build. Il faut les deux."""
    faux = FauxApk({"com.google.android.geo.API_KEY": "AIzaSy" + "m" * 33},
                   {"project_id": "mon-projet-firebase", "app_name": "Ma Boutique"})
    pairs = analyze.credential_pairs(faux)
    assert ("manifest-meta-data", "com.google.android.geo.API_KEY",
            "AIzaSy" + "m" * 33) in pairs
    assert ("resource-string", "project_id", "mon-projet-firebase") in pairs

    found = {f["id"]: f for f in keys.scan(pairs)}
    assert set(found) == {"google-maps", "firebase-project"}
    assert "value" not in found["google-maps"]
    assert found["firebase-project"]["value"] == "mon-projet-firebase"


def test_un_manifeste_illisible_n_emporte_pas_l_analyse():
    """Le manifeste d'un tiers est de la donnée compilée par un outil qu'on ne contrôle
    pas — son échec ne doit pas coûter les poids et les permissions déjà lus."""

    class Casse:
        def get_android_manifest_xml(self):
            raise ValueError("manifeste illisible")

        def get_android_resources(self):
            raise ValueError("resources.arsc illisible")

    assert analyze.credential_pairs(Casse()) == []


# ------------------------------------------------------------- reference size

def part(kind, qualifier=None, size=100):
    return {"name": f"{kind}.{qualifier}.apk", "kind": kind, "qualifier": qualifier,
            "bytes": size, "dex_bytes": 0, "res_bytes": 0, "assets_bytes": 0,
            "native_bytes": 0}


def test_reference_of_a_full_xapk_keeps_base_arm64_xxhdpi_and_features():
    parts = [part("base", size=1000), part("abi", "arm64-v8a", 300),
             part("abi", "armeabi-v7a", 250), part("density", "xxhdpi", 80),
             part("density", "hdpi", 40), part("language", "fr", 10),
             part("feature", "camera", 50)]
    assert analyze.reference_bytes("merged-xapk", 1730, parts,
                                   {"arm64-v8a": 300, "armeabi-v7a": 250}) == (1430, [])


def test_reference_of_a_multi_abi_apk_drops_the_other_abis():
    assert analyze.reference_bytes(
        "single-apk", 1000, [part("base", size=1000)],
        {"arm64-v8a": 200, "x86": 150, "x86_64": 170}) == (680, [])


def test_reference_of_an_apk_without_native_code_is_its_total():
    assert analyze.reference_bytes("single-apk", 1000, [part("base", size=1000)], {}) \
        == (1000, [])


def test_reference_is_null_when_native_code_has_no_arm64():
    assert analyze.reference_bytes("single-apk", 1000, [part("base", size=1000)],
                                   {"armeabi-v7a": 200}) == (None, ["reference_abi_missing"])
    assert analyze.reference_bytes(
        "merged-xapk", 1200, [part("base", size=1000), part("abi", "armeabi-v7a", 200)],
        {"armeabi-v7a": 200}) == (None, ["reference_abi_missing"])


def test_a_served_density_stands_in_for_a_missing_xxhdpi():
    """APKPure serves one density per download, rarely xxhdpi; at ~1 % of the size,
    refusing the whole reference over it costs more than it protects."""
    assert analyze.reference_bytes(
        "merged-xapk", 1040, [part("base", size=1000), part("density", "hdpi", 40)], {}) \
        == (1040, ["reference_density_substituted"])


def test_reference_of_an_xapk_without_density_parts_is_computed():
    """Resources are all in the base then, identical on both sides."""
    assert analyze.reference_bytes(
        "merged-xapk", 1300, [part("base", size=1000), part("abi", "arm64-v8a", 300)],
        {"arm64-v8a": 300}) == (1300, [])


def test_an_unknown_part_is_left_out_and_flagged():
    assert analyze.reference_bytes(
        "merged-xapk", 1100, [part("base", size=1000), part("unknown", "ldrtl", 100)], {}) \
        == (1000, ["reference_part_unknown"])


def test_snapshot_carries_the_reference_size(apk):
    snap, _ = analyze.snapshot_for(ENTRY, apk, [1], {}, "v", "2026-09-23T10:00:00+00:00")
    assert snap.reference_bytes == 45943
    assert snap.size_parts == apk.size_parts
    assert snap.native_bytes_by_abi == {}


def test_a_missing_reference_says_why_in_the_quality_flags(apk):
    armeabi_only = replace(apk, native_bytes_by_abi={"armeabi-v7a": 10})
    snap, _ = analyze.snapshot_for(ENTRY, armeabi_only, [1], {}, "v",
                                   "2026-09-23T10:00:00+00:00")
    assert snap.reference_bytes is None
    assert "reference_abi_missing" in snap.quality_flags
