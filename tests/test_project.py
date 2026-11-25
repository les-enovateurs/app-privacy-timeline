"""La projection est pure : des dicts vers des dicts.

Ce qui se joue ici n'est pas « produire un JSON » mais « ne produire que le JSON qui a
changé ». Le site est la source du backfill git : on écrit chez celui dont on a lu
l'histoire, et la moindre différence de format ou d'ordre y ressemble à un fait nouveau.
"""

import json

from apklab import project


def snap(**kw) -> dict:
    """Un snapshot du store, réduit aux champs que la projection lit."""
    return {"version_name": "1.2.3", "source": "exodus-git",
            "observed_at": "2026-04-18T18:17:00+02:00", "report_date": None,
            "permissions": ["android.permission.INTERNET"],
            "trackers": [{"id": 49, "name": "Google Firebase Analytics"}],
            "quality_flags": [], **kw}


# ------------------------------------------------------------------ site_date

def test_site_date_ramene_a_l_utc_du_site():
    """Les snapshots git portent le fuseau du committeur, +01:00 et +02:00 cohabitent.
    Les écrire tels quels ferait diverger une fiche de sa voisine sans qu'un fait bouge."""
    assert project.site_date("2026-04-18T18:17:00+02:00") == "2026-04-18T16:17:00.000Z"


def test_site_date_est_stable_sur_le_format_du_site():
    """La date d'une fiche relue puis réécrite doit rendre exactement la même chaîne,
    sinon les 131 fiches bougeraient au premier passage."""
    assert project.site_date("2026-04-18T16:17:00.000Z") == "2026-04-18T16:17:00.000Z"


# ----------------------------------------------------------------- fields_for

def test_report_date_retombe_sur_observed_at():
    """Une analyse statique n'est le rapport de personne. La date publiée reste celle de
    la mesure plutôt qu'une invention ou un champ vide."""
    fields = project.fields_for(snap(source="apk-static", report_date=None))
    assert fields["report_date"] == "2026-04-18T16:17:00.000Z"


def test_report_date_d_un_rapport_prime_sur_l_observation():
    fields = project.fields_for(snap(report_date="2019-10-28T20:31:00.000Z"))
    assert fields["report_date"] == "2019-10-28T20:31:00.000Z"


def test_les_pisteurs_sortent_en_ids_nus():
    """Le site attend des entiers ; son nom vient de son propre trackers.json. Le
    republier ici en figerait une copie qui divergerait."""
    fields = project.fields_for(snap(trackers=[{"id": 49, "name": "X"},
                                               {"id": 12, "name": "Y"}]))
    assert fields["trackers"] == [12, 49]


# ----------------------------------------------------------------- projection

def test_serie_vide_ne_projette_rien():
    assert project.projection([]) == (None, "no_snapshot")


def test_dernier_point_sans_analyse_de_pisteurs_fait_refuser():
    """Sa liste vide veut dire « pas cherché ». La publier écrirait « aucun pisteur » sur
    la fiche — le défaut même que le design reproche aux données existantes."""
    série = [snap(), snap(source="apk-static", trackers=[],
                          quality_flags=["empty_trackers", "trackers_not_analyzed"])]
    assert project.projection(série) == (None, "trackers_not_analyzed")


def test_version_ancienne_collectee_apres_ne_fait_pas_foi():
    """`run --depth` remonte le temps : une 1.107.0 téléchargée aujourd'hui arrive après
    la 1.124.0 vue en juin. La prendre pour le dernier point publierait les permissions
    d'une version vieille d'un an sous la date du jour."""
    série = [snap(version_name="1.124.0", observed_at="2026-06-17T07:58:13+02:00"),
             snap(version_name="1.107.0", observed_at="2026-09-11T09:52:46+00:00",
                  source="apk-static",
                  permissions=["android.permission.READ_CONTACTS"])]
    fields, refus = project.projection(série)

    assert refus is None
    assert fields["version"] == "1.124.0"
    assert fields["permissions"] == ["android.permission.INTERNET"]


def test_point_incomplet_sur_la_version_la_plus_recente_fait_toujours_refuser():
    """Le refus suit le point qui fait foi, pas la position : une archéologie complète
    ne doit pas faire passer une dernière version sans pisteurs analysés."""
    série = [snap(version_name="1.124.0", source="apk-static", trackers=[],
                  quality_flags=["trackers_not_analyzed"]),
             snap(version_name="1.107.0", source="apk-static",
                  observed_at="2026-09-11T09:52:46+00:00")]
    assert project.projection(série) == (None, "trackers_not_analyzed")


def test_deux_analyses_de_la_meme_version_gardent_la_derniere():
    """Même numéro des deux côtés : c'est la position dans la série qui départage, comme
    avant l'archéologie."""
    série = [snap(source="apk-static", permissions=["android.permission.CAMERA"]),
             snap(source="apk-static", permissions=["android.permission.NFC"])]
    fields, _ = project.projection(série)
    assert fields["permissions"] == ["android.permission.NFC"]


def test_liste_vide_sans_le_drapeau_est_un_fait_et_se_projette():
    """Exodus n'a rien trouvé : c'est une information, pas une absence de mesure. Toute la
    raison d'être du drapeau tient dans cet écart."""
    fields, refus = project.projection([snap(trackers=[],
                                             quality_flags=["empty_trackers"])])
    assert refus is None
    assert fields["trackers"] == []


def test_c_est_le_dernier_point_qui_fait_foi():
    série = [snap(version_name="1.0.0"), snap(version_name="2.0.0")]
    assert project.projection(série)[0]["version"] == "2.0.0"


# ------------------------------------------------------------------ apply_to

FICHE = {"app_name": "Carrefour", "handle": "com.carrefour.fid.android",
         "version": "23.0.0", "report_date": "2026-04-18T16:17:00.000Z",
         "permissions": ["android.permission.INTERNET"],
         "trackers": [12, 49], "slug_playstore": "com.carrefour.fid.android"}


def test_les_champs_du_site_survivent():
    """slug_playstore porte sur 95 fiches, et les champs du vieux scraper Exodus sur
    trois. Ce dépôt ne sait pas les reproduire : les écraser les perdrait."""
    fields = project.fields_for(snap())
    doc = project.apply_to(FICHE, "Carrefour", "com.carrefour.fid.android", fields)
    assert doc["slug_playstore"] == "com.carrefour.fid.android"


def test_l_ordre_des_cles_existantes_est_conserve():
    """Quatre ordres de clés cohabitent sur le site. Réordonner réécrirait le fichier
    entier, et un diff de revue « fichier entier changé » n'est pas lu."""
    désordre = {"handle": "com.x", "app_name": "X", "trackers": [1],
                "permissions": [], "version": "1", "report_date": None}
    doc = project.apply_to(désordre, "X", "com.x", project.fields_for(snap()))
    assert list(doc.keys()) == list(désordre.keys())


def test_un_reordonnancement_n_est_pas_un_changement():
    """80 des 131 fiches portent leurs pisteurs dans l'ordre d'Exodus. Trier pour trier
    produirait un diff de 80 fichiers où aucun fait n'a bougé."""
    fiche = dict(FICHE, trackers=[49, 12])
    fields = project.fields_for(snap(trackers=[{"id": 12, "name": "a"},
                                               {"id": 49, "name": "b"}]))
    doc = project.apply_to(fiche, "Carrefour", "com.carrefour.fid.android", fields)
    assert doc["trackers"] == [49, 12]


def test_un_ensemble_qui_change_est_reecrit_trie():
    fields = project.fields_for(snap(trackers=[{"id": 49, "name": "b"},
                                               {"id": 3, "name": "c"}]))
    doc = project.apply_to(FICHE, "Carrefour", "com.carrefour.fid.android", fields)
    assert doc["trackers"] == [3, 49]


def test_app_name_n_est_ecrit_qu_a_la_creation():
    """C'est le libellé d'affichage du site. Le republier annulerait un renommage fait
    là-bas ; nos snapshots ne le portent que parce qu'ils l'y ont pris."""
    doc = project.apply_to(dict(FICHE, app_name="Carrefour Drive"), "Carrefour",
                           "com.carrefour.fid.android", project.fields_for(snap()))
    assert doc["app_name"] == "Carrefour Drive"

    créé = project.apply_to(None, "Carrefour", "com.carrefour.fid.android",
                            project.fields_for(snap()))
    assert créé["app_name"] == "Carrefour"
    assert créé["handle"] == "com.carrefour.fid.android"


# ----------------------------------------------------------------- site_ahead

def test_site_plus_recent_est_signale():
    """Le site est la source du backfill : il peut nous dépasser si update-exodus.py y a
    tourné depuis. L'écraser ferait reculer le site, et sans bruit."""
    fields = project.fields_for(snap(report_date="2025-09-13T10:00:00.000Z"))
    assert project.site_ahead(FICHE, fields) is True


def test_site_plus_ancien_ne_bloque_pas():
    fields = project.fields_for(snap(report_date="2026-06-01T10:00:00.000Z"))
    assert project.site_ahead(FICHE, fields) is False


def test_fiche_absente_ou_sans_date_ne_bloque_pas():
    fields = project.fields_for(snap())
    assert project.site_ahead(None, fields) is False
    assert project.site_ahead({"report_date": None}, fields) is False


# --------------------------------------------------------------------- dumps

def test_dumps_reproduit_le_format_du_site():
    """Mesuré sur les 131 fiches : indentation 2, unicode non échappé, aucun saut de
    ligne final. C'est ce qui fait qu'un diff montre les permissions une par une."""
    texte = project.dumps({"app_name": "Café", "permissions": ["A", "B"]})

    assert texte.startswith('{\n  "app_name": "Café"')
    assert not texte.endswith("\n")
    assert json.loads(texte)["app_name"] == "Café"


def test_dumps_relit_une_fiche_du_site_a_l_identique():
    """La propriété qui valide l'étage : relire puis réécrire une fiche sans rien changer
    doit rendre les mêmes octets, sinon les 131 fiches bougeraient au premier passage."""
    original = json.dumps(FICHE, ensure_ascii=False, indent=2)
    assert project.dumps(json.loads(original)) == original


# ------------------------------------------------------------------- changes

def test_changes_dit_ce_qui_bouge():
    fields = project.fields_for(snap(version_name="24.0.0",
                                     permissions=["android.permission.CAMERA"]))
    bougé = project.changes(FICHE, fields)

    assert bougé["version"] == ["23.0.0", "24.0.0"]
    assert bougé["permissions"]["added"] == ["android.permission.CAMERA"]
    assert bougé["permissions"]["removed"] == ["android.permission.INTERNET"]
    assert bougé["trackers"]["removed"] == [12]


def test_changes_sur_une_fiche_creee():
    bougé = project.changes(None, project.fields_for(snap()))
    assert bougé["version"] == [None, "1.2.3"]
    assert bougé["permissions"]["removed"] == []


# --- retraits de pisteurs non confirmés ---------------------------------------

def statique(version, trackers, catalog="2026-09-09/432/sha256:aaa", when=None):
    return {"source": "apk-static", "version_name": version,
            "observed_at": when or "2026-09-10T00:00:00+00:00",
            "catalog_version": catalog, "permissions": [],
            "trackers": [{"id": i, "name": f"T{i}"} for i in trackers],
            "quality_flags": []}


def test_un_ajout_de_pisteur_se_publie_tout_de_suite():
    """Un pisteur trouvé dans le binaire est un fait : ses classes sont là."""
    fields = {"trackers": [1, 2, 3], "version": "2.0"}
    sortie, retenus = project.hold_removals({"trackers": [1, 2]}, fields,
                                            [statique("2.0", [1, 2, 3])])
    assert sortie["trackers"] == [1, 2, 3] and retenus == []


def test_un_retrait_sur_une_seule_analyse_est_retenu():
    """Vinted : 27 pisteurs chez Exodus, 18 chez nous, delta déclaré inexploitable."""
    fields = {"trackers": [1, 2], "version": "2.0"}
    sortie, retenus = project.hold_removals({"trackers": [1, 2, 9]}, fields,
                                            [statique("2.0", [1, 2])])
    assert sortie["trackers"] == [1, 2, 9] and retenus == [9]


def test_un_retrait_se_publie_si_sa_derniere_presence_est_comparable():
    """Vu par une analyse statique, absent de la suivante, même catalogue : c'est un
    retrait mesuré des deux côtés."""
    fields = {"trackers": [1], "version": "2.0"}
    série = [statique("1.9", [1, 9], when="2026-09-01T00:00:00+00:00"),
             statique("2.0", [1])]
    sortie, retenus = project.hold_removals({"trackers": [1, 9]}, fields, série)
    assert sortie["trackers"] == [1] and retenus == []


def test_un_retrait_dont_la_presence_vient_d_exodus_est_retenu():
    """Le pisteur a été vu avec un catalogue inconnu : son absence n'est pas comparable
    à sa présence, et c'est précisément ce que `diff` appelle `catalog_unknown`."""
    fields = {"trackers": [1], "version": "2.0"}
    série = [{"source": "exodus-web", "version_name": "1.9", "catalog_version": None,
              "observed_at": "2026-09-01T00:00:00+00:00", "permissions": [],
              "trackers": [{"id": 1, "name": "T1"}, {"id": 9, "name": "T9"}],
              "quality_flags": []},
             statique("2.0", [1])]
    sortie, retenus = project.hold_removals({"trackers": [1, 9]}, fields, série)
    assert sortie["trackers"] == [1, 9] and retenus == [9]


def test_deux_analyses_concordantes_ne_valent_pas_confrontation():
    """Grindr : nos deux analyses trouvent 19 pisteurs, la fiche en porte 31, et l'une
    de nos analyses porte sur la version qu'Exodus avait lui-même scannée. Ce n'est pas
    un retrait dans le temps, c'est un désaccord sur la même version."""
    fields = {"trackers": [1], "version": "2.0"}
    série = [statique("1.9", [1], when="2026-09-01T00:00:00+00:00"),
             statique("2.0", [1])]
    sortie, retenus = project.hold_removals({"trackers": [1, 9]}, fields, série)
    assert sortie["trackers"] == [1, 9] and retenus == [9]


def test_un_pisteur_de_catalogue_different_est_retenu():
    """Le pisteur a pu disparaître du catalogue, pas de l'application."""
    fields = {"trackers": [1], "version": "2.0"}
    série = [statique("1.9", [1, 9], catalog="2026-06-01/430/sha256:bbb",
                      when="2026-09-01T00:00:00+00:00"),
             statique("2.0", [1])]
    sortie, retenus = project.hold_removals({"trackers": [1, 9]}, fields, série)
    assert sortie["trackers"] == [1, 9] and retenus == [9]


def test_une_fiche_sans_pisteur_ne_retient_rien():
    fields = {"trackers": [1], "version": "2.0"}
    sortie, retenus = project.hold_removals(None, fields, [statique("2.0", [1])])
    assert sortie["trackers"] == [1] and retenus == []
