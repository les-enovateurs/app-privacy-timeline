"""Catalogue de signatures et détection. Le contrat repris d'exodus-core, testé."""

import json

import pytest

from apklab import signatures


def catalogue(*entries) -> dict:
    return {"trackers": {str(e["id"]): e for e in entries}}


def tracker(id_, name, code_signature):
    return {"id": id_, "name": name, "code_signature": code_signature}


def test_catalogue_est_indexe_par_cle_et_non_par_champ():
    """/api/trackers rend {"trackers": {"1": {...}}} : l'id est la clé."""
    trackers = signatures.parse_catalog(catalogue(tracker(1, "Teemo", "com.databerries.")))
    assert trackers == [signatures.Tracker(1, "Teemo", "com.databerries.")]


def test_pisteur_sans_signature_de_code_est_ecarte():
    """Exodus en catalogue certains sur leur seule signature réseau : on ne les
    détecterait jamais dans un dex, les garder gonflerait le catalogue pour rien."""
    trackers = signatures.parse_catalog(catalogue(tracker(1, "Réseau seul", ""),
                                                  tracker(2, "Code", "com.foo.")))
    assert [t.id for t in trackers] == [2]


def test_charge_inattendue_leve():
    with pytest.raises(signatures.CatalogError):
        signatures.parse_catalog({"resultat": []})


def test_version_de_catalogue_porte_date_taille_et_empreinte():
    raw = json.dumps(catalogue(tracker(1, "T", "a."))).encode("utf-8")
    version = signatures.catalog_version(raw, "2026-09-09")
    assert version.startswith("2026-09-09/1/sha256:")
    assert len(version.rsplit(":", 1)[1]) == 12


def test_deux_catalogues_differents_ont_deux_versions():
    a = json.dumps(catalogue(tracker(1, "T", "a."))).encode("utf-8")
    b = json.dumps(catalogue(tracker(1, "T", "a."), tracker(2, "U", "b."))).encode("utf-8")
    assert signatures.catalog_version(a, "2026-09-09") != \
        signatures.catalog_version(b, "2026-09-09")


def test_load_rend_les_pisteurs_et_la_version_figee(tmp_path):
    """Le fichier porte les deux : séparer la liste de sa version laisserait une analyse
    revendiquer un catalogue qui n'est pas celui avec lequel elle a tourné."""
    doc = catalogue(tracker(1, "T", "com.foo."))
    doc["catalog_version"] = "2026-09-09/1/sha256:abc123abc123"
    path = tmp_path / "trackers.json"
    path.write_text(json.dumps(doc), "utf-8")

    trackers, version = signatures.load(path)
    assert [t.name for t in trackers] == ["T"]
    assert version == "2026-09-09/1/sha256:abc123abc123"


# ---------------------------------------------------------------------- détection

CLASSES = ["com.example.app.Main", "com.google.android.gms.ads.AdView",
           "androidx.core.View", "com.databerries.LocationService"]
TRACKERS = signatures.parse_catalog(catalogue(
    tracker(1, "Teemo", "com.databerries."),
    tracker(52, "Google AdMob", "com.google.android.gms.ads."),
    tracker(99, "Absent", "com.absent.sdk."),
))


def test_detection_rend_les_pisteurs_presents():
    assert signatures.detect(CLASSES, TRACKERS).tracker_ids == [1, 52]


def test_signature_cherchee_au_milieu_d_un_nom_complet():
    """Une signature n'est pas un préfixe exact : elle doit matcher dans le nom entier,
    comme chez Exodus."""
    trackers = signatures.parse_catalog(catalogue(tracker(7, "T", "android.gms.ads")))
    assert signatures.detect(CLASSES, trackers).tracker_ids == [7]


def test_aucune_classe_aucun_pisteur():
    assert signatures.detect([], TRACKERS).tracker_ids == []


def test_signature_illisible_est_consignee_et_sautee():
    """Le catalogue est de la donnée tierce : une entrée cassée ne doit ni faire échouer
    l'analyse ni disparaître en silence."""
    trackers = signatures.parse_catalog(catalogue(tracker(3, "Cassé", "com.foo.[(„"),
                                                  tracker(1, "Teemo", "com.databerries.")))
    detection = signatures.detect(CLASSES, trackers)
    assert detection.tracker_ids == [1]
    assert [id_ for id_, _ in detection.broken] == [3]


# --- corrections locales du catalogue -----------------------------------------

CORRECTION = {"corrections": [{
    "id": 1, "name": "T1", "upstream_signature": "com.ancien",
    "code_signature": r"com.ancien|com\.nouveau\.",
    "why": "SDK renommé", "observed_in": ["com.app.x 1.0"]}]}


def fichier_catalogue(tmp_path, signature="com.ancien"):
    path = tmp_path / "trackers.json"
    path.write_text(json.dumps({
        "catalog_version": "2026-09-09/1/sha256:abc123abc123",
        "trackers": {"1": {"id": 1, "name": "T1", "code_signature": signature}}}), "utf-8")
    return path


def corrections(tmp_path, doc=None):
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps(doc if doc is not None else CORRECTION), "utf-8")
    return path


def test_une_correction_etend_la_signature(tmp_path):
    """Braze se cherche par `com.appboy` chez Exodus ; le SDK s'appelle `com.braze`."""
    trackers, version = signatures.load(fichier_catalogue(tmp_path), corrections(tmp_path))
    assert signatures.detect(["com.nouveau.Sdk"], trackers).tracker_ids == [1]
    assert signatures.detect(["com.ancien.Sdk"], trackers).tracker_ids == [1]


def test_une_correction_change_l_identifiant_de_fichier_catalogue(tmp_path):
    """Une analyse faite avec un catalogue corrigé n'est pas comparable à une autre."""
    _, brut = signatures.load(fichier_catalogue(tmp_path))
    _, corrigé = signatures.load(fichier_catalogue(tmp_path), corrections(tmp_path))
    assert brut != corrigé and corrigé.startswith(brut) and "+corr:1/" in corrigé


def test_une_correction_ne_peut_pas_retirer_une_detection(tmp_path):
    """Le seul faux pas irrattrapable : un faux négatif que personne ne voit."""
    remplace = {"corrections": [{**CORRECTION["corrections"][0],
                                 "code_signature": r"com\.nouveau\."}]}
    with pytest.raises(signatures.CatalogError, match="ne contient pas"):
        signatures.load(fichier_catalogue(tmp_path), corrections(tmp_path, remplace))


def test_une_correction_sans_motif_est_refusee(tmp_path):
    """Sans motif, personne ne saura laquelle jeter quand Exodus aura corrigé."""
    muette = {"corrections": [{"id": 1, "code_signature": "com.ancien|com.nouveau"}]}
    with pytest.raises(signatures.CatalogError, match="sans motif"):
        signatures.load(fichier_catalogue(tmp_path), corrections(tmp_path, muette))


def test_une_correction_orpheline_est_consignee_et_sautee(tmp_path):
    """Le catalogue bouge sous nos pieds : une correction périmée ne doit pas empêcher
    toutes les commandes de tourner, seulement se voir et se retirer."""
    inconnu = {"corrections": [{**CORRECTION["corrections"][0], "id": 999}]}
    trackers, version = signatures.load(fichier_catalogue(tmp_path),
                                        corrections(tmp_path, inconnu))
    assert signatures.LAST_ORPHANS == [999]
    assert "+corr" not in version and len(trackers) == 1


def test_sans_fichier_de_corrections_rien_ne_change(tmp_path):
    trackers, version = signatures.load(fichier_catalogue(tmp_path), tmp_path / "absent.json")
    assert "+corr" not in version and len(trackers) == 1
