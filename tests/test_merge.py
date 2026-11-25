"""La fusion des parts d'un XAPK.

Deux étages. `merge` est une fonction pure sur des `ApkFacts` : elle se teste sur des
parts fabriquées, y compris le cas qu'aucun XAPK synthétique ne peut produire — une
classe de pisteur présente dans le seul split, un dex valide n'étant pas synthétisable.
`facts_xapk` se teste sur le XAPK synthétique, avec le vrai androguard.
"""

from dataclasses import replace

from apklab import analyze
from conftest import HANDLE_APK, LIB_BYTES

BASE = analyze.ApkFacts(
    handle=HANDLE_APK, version_name="0.2.13", version_code=2130,
    min_sdk=8, target_sdk=25,
    permissions=["android.permission.INSTALL_PACKAGES"],
    apk_sha256="a" * 64, size_scope="single-apk",
    total_bytes=1000, dex_bytes=600, res_bytes=300, assets_bytes=100,
    native_bytes=0, native_abis=[], dex_method_count=185,
    class_names=["org.fdroid.Base"], analyzer_version="androguard 4.1.0",
)

# Un split de configuration natif, calqué sur le config.arm64_v8a.apk de Carrefour :
# tout le code natif, l'unique ABI, aucun dex, aucune permission.
SPLIT_NATIF = replace(
    BASE, version_name=None, permissions=[], total_bytes=200,
    dex_bytes=0, res_bytes=0, assets_bytes=0,
    native_bytes=180, native_abis=["arm64-v8a"], dex_method_count=0, class_names=[],
    native_bytes_by_abi={"arm64-v8a": 180},
)


def test_identite_vient_du_base():
    """Un split porte le même version_code mais aucun version_name. Le prendre pour
    référence marquerait `missing_version_name` une application qui en a un."""
    merged = analyze.merge([BASE, SPLIT_NATIF], 1200, "b" * 64)

    assert merged.handle == HANDLE_APK
    assert merged.version_name == "0.2.13"
    assert merged.version_code == 2130
    assert (merged.min_sdk, merged.target_sdk) == (8, 25)


def test_le_code_natif_du_split_n_est_plus_perdu():
    """Le cas mesuré sur Carrefour 23.6.0 : le base déclare 0 octet natif et aucune ABI,
    les 17,4 Mo et l'unique arm64-v8a sont dans le split. Analyser le base seul
    publierait « application sans code natif », ce qui est faux."""
    assert (BASE.native_bytes, BASE.native_abis) == (0, [])

    merged = analyze.merge([BASE, SPLIT_NATIF], 1200, "b" * 64)

    assert merged.native_bytes == 180
    assert merged.native_abis == ["arm64-v8a"]


def test_un_pisteur_loge_dans_un_split_est_vu():
    """Le faux négatif silencieux que la fusion existe pour éviter : un split de
    fonctionnalité embarque son propre dex, donc ses propres classes de pisteur, que le
    base ne porte pas."""
    feature = replace(SPLIT_NATIF, dex_method_count=12,
                      class_names=["com.google.android.gms.ads.Mobile"])

    merged = analyze.merge([BASE, feature], 1200, "b" * 64)

    assert merged.class_names == ["com.google.android.gms.ads.Mobile", "org.fdroid.Base"]
    assert merged.dex_method_count == 185 + 12


def test_permissions_sont_l_union_triee_et_dedoublonnee():
    autre = replace(SPLIT_NATIF, permissions=["android.permission.INTERNET",
                                              "android.permission.INSTALL_PACKAGES"])
    merged = analyze.merge([BASE, autre], 1200, "b" * 64)
    assert merged.permissions == ["android.permission.INSTALL_PACKAGES",
                                  "android.permission.INTERNET"]


def test_les_postes_de_poids_s_additionnent():
    merged = analyze.merge([BASE, SPLIT_NATIF], 1200, "b" * 64)
    assert (merged.dex_bytes, merged.res_bytes, merged.assets_bytes) == (600, 300, 100)
    assert merged.total_bytes == 1200


def test_les_abis_de_plusieurs_splits_se_reunissent():
    """Le vrai signal éco-conception : une application qui embarque quatre ABI gaspille
    structurellement, et sans fusion on n'en verrait aucune."""
    armeabi = replace(SPLIT_NATIF, native_bytes=90, native_abis=["armeabi-v7a"])
    merged = analyze.merge([BASE, SPLIT_NATIF, armeabi], 1290, "b" * 64)

    assert merged.native_abis == ["arm64-v8a", "armeabi-v7a"]
    assert merged.native_bytes == 270


def test_le_perimetre_et_l_empreinte_sont_ceux_de_l_ensemble():
    merged = analyze.merge([BASE, SPLIT_NATIF], 1200, "b" * 64)
    assert merged.size_scope == "merged-xapk"
    assert merged.apk_sha256 == "b" * 64


# ------------------------------------------------- facts_xapk, avec androguard

def test_facts_xapk_sur_le_xapk_synthetique(tmp_path, xapk_deux_parts):
    merged, flags = analyze.facts_xapk(xapk_deux_parts, tmp_path / "parts")

    assert merged.handle == HANDLE_APK
    assert merged.version_name == "0.2.13"
    assert merged.native_bytes == LIB_BYTES
    assert merged.native_abis == ["arm64-v8a"]
    assert merged.size_scope == "merged-xapk"
    assert "merged_xapk" in flags


def test_facts_xapk_recupere_ce_que_le_base_seul_perdrait(tmp_path, xapk_deux_parts):
    """Le test qui dit pourquoi la fusion existe, sur androguard et non sur des faits
    fabriqués : le base extrait du XAPK, analysé seul, ne déclare aucun octet natif et
    aucune ABI. Ce sont les deux faits que la fusion rattrape."""
    from apklab import xapk

    bundle = xapk.explode(xapk_deux_parts, tmp_path / "seul")
    base_seul = analyze.facts(bundle.base)
    assert (base_seul.native_bytes, base_seul.native_abis) == (0, [])

    merged, _ = analyze.facts_xapk(xapk_deux_parts, tmp_path / "parts")
    assert (merged.native_bytes, merged.native_abis) == (LIB_BYTES, ["arm64-v8a"])


def test_facts_xapk_empreinte_le_xapk_lui_meme(tmp_path, xapk_deux_parts):
    """Il n'y a pas de binaire unique à empreindre. Le sha du XAPK reste vérifiable — un
    tiers qui le retélécharge retrouve la somme — mais il n'est pas comparable au sha
    publié par Exodus, qui analyse un APK seul. D'où le drapeau."""
    merged, flags = analyze.facts_xapk(xapk_deux_parts, tmp_path / "parts")
    assert merged.apk_sha256 == analyze.sha256(xapk_deux_parts)
    assert "merged_xapk" in flags


def test_facts_xapk_poids_total_exclut_l_emballage(tmp_path, xapk_deux_parts):
    merged, _ = analyze.facts_xapk(xapk_deux_parts, tmp_path / "parts")
    assert merged.total_bytes < xapk_deux_parts.stat().st_size


def test_facts_xapk_marque_une_part_etrangere(tmp_path, xapk_deux_parts, monkeypatch):
    """Un XAPK mal fabriqué par le miroir. L'identité venant du base, la série ne part
    pas sur la mauvaise application ; mais son poids et ses classes seraient ceux de deux
    applications mélangées, ce qui doit se lire dans la donnée et non se perdre dans un
    log.

    Le paquet d'une part se lit dans son AndroidManifest.xml, du XML binaire compilé : le
    faire mentir demanderait de réécrire la table de chaînes de l'archive. C'est
    l'analyse d'une part qui est simulée, pas la règle testée.
    """
    vrai = analyze.facts
    vus: list = []

    def facts(path, size_scope="single-apk"):
        part = vrai(path, size_scope=size_scope)
        vus.append(part)
        return part if len(vus) == 1 else replace(part, handle="com.autre.appli")

    monkeypatch.setattr(analyze, "facts", facts)
    merged, flags = analyze.facts_xapk(xapk_deux_parts, tmp_path / "parts")

    assert len(vus) == 2
    assert merged.handle == HANDLE_APK
    assert "xapk_handle_mismatch" in flags


def test_les_identifiants_de_compte_ne_sont_jamais_re_empreints():
    """Une valeur tronquée ne peut plus être re-mesurée, seulement recopiée. La première
    version rejouait `keys.scan` sur `value_prefix`, et les clés Maps de trois
    applications sortaient avec l'empreinte de la chaîne « AIzaSy » — un compte partagé
    qui n'existait pas."""
    secret = {"id": "google-maps", "label": "clé API Google Maps",
              "name": "com.google.android.geo.API_KEY", "origin": "manifest-meta-data",
              "secret": True, "value_prefix": "AIzaSy", "value_sha256": "0123456789ab",
              "value_length": 39}
    autre = {**secret, "value_sha256": "ffffffffffff"}
    base = analyze.ApkFacts(
        handle="com.app.x", version_name="1.0", version_code=1, min_sdk=21,
        target_sdk=34, permissions=[], apk_sha256="ab", size_scope="single-apk",
        total_bytes=1, dex_bytes=1, res_bytes=0, assets_bytes=0, native_bytes=0,
        native_abis=[], dex_method_count=1, credentials=[secret])
    split = replace(base, credentials=[secret, autre])

    fusion = analyze.merge([base, split], total_bytes=2, apk_sha256="cd")
    assert fusion.credentials == [secret, autre]
    assert [f["value_length"] for f in fusion.credentials] == [39, 39]


def test_native_bytes_per_abi_add_up_across_parts():
    armeabi = replace(SPLIT_NATIF, native_bytes=90, native_abis=["armeabi-v7a"],
                      native_bytes_by_abi={"armeabi-v7a": 90})
    merged = analyze.merge([BASE, SPLIT_NATIF, armeabi], 1290, "b" * 64)
    assert merged.native_bytes_by_abi == {"arm64-v8a": 180, "armeabi-v7a": 90}


def test_merge_takes_part_names_from_its_caller():
    """Names live in the bundle, not in the facts: without them, no parts."""
    assert analyze.merge([BASE, SPLIT_NATIF], 1200, "b" * 64).size_parts == []


def test_facts_xapk_records_each_part(tmp_path, xapk_deux_parts):
    merged, _ = analyze.facts_xapk(xapk_deux_parts, tmp_path / "parts")
    assert [(p["name"], p["kind"], p["qualifier"]) for p in merged.size_parts] == [
        (f"{HANDLE_APK}.apk", "base", None),
        ("config.arm64_v8a.apk", "abi", "arm64-v8a")]
    assert sum(p["bytes"] for p in merged.size_parts) == merged.total_bytes
    assert merged.native_bytes_by_abi == {"arm64-v8a": LIB_BYTES}
