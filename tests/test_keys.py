"""Les identifiants de compte SDK : reconnaissance, et troncature à la publication.

Deux propriétés tiennent ici. Un secret ne doit jamais ressortir en entier — un dataset
public qui republie une clé cliente fournit le moyen de la consommer aux frais de
l'éditeur. Et un identifiant qui n'est *pas* un secret doit ressortir entier, parce que
c'est le nom du compte qui fait le fait dans un rapport RGPD.
"""

from apklab import keys


def test_une_cle_maps_est_reconnue_par_son_nom():
    found = keys.scan([("manifest-meta-data", "com.google.android.geo.API_KEY",
                        "AIzaSyB" + "x" * 32)])
    assert [(f["id"], f["label"]) for f in found] == [
        ("google-maps", "clé API Google Maps")]


def test_un_secret_est_tronque_et_empreint():
    valeur = "AIzaSyB" + "x" * 32
    found = keys.scan([("manifest-meta-data", "com.google.android.geo.API_KEY",
                        valeur)])[0]
    assert "value" not in found
    assert found["value_prefix"] == "AIzaSy"
    assert len(found["value_sha256"]) == keys.DIGEST
    assert found["value_length"] == len(valeur)


def test_un_identifiant_de_compte_reste_entier():
    """L'identifiant d'application Facebook *est* le fait : il nomme le compte."""
    found = keys.scan([("resource-string", "facebook_app_id", "1234567890123456")])[0]
    assert found["secret"] is False
    assert found["value"] == "1234567890123456"


def test_une_forme_de_valeur_trouve_ce_qu_aucun_nom_ne_cherchait():
    """La seule voie qui découvre : `AIza…` est une clé Google sous n'importe quel nom."""
    found = keys.scan([("resource-string", "cle_maison", "AIza" + "b" * 35)])
    assert [f["id"] for f in found] == ["google-api-key"]


def test_un_nom_precis_gagne_sur_une_forme_generique():
    """Sans l'ordre, la clé Maps sortirait sous l'étiquette « clé API Google »."""
    found = keys.scan([("manifest-meta-data", "com.google.android.geo.API_KEY",
                        "AIza" + "c" * 35)])
    assert [f["id"] for f in found] == ["google-maps"]


def test_un_dsn_sentry_est_reconnu_sur_sa_forme():
    found = keys.scan([("resource-string", "peu_importe",
                        "https://" + "a" * 32 + "@o1.ingest.sentry.io/42")])
    assert [f["id"] for f in found] == ["sentry-dsn"]


def test_une_valeur_inconnue_n_est_pas_un_identifiant():
    assert keys.scan([("resource-string", "app_name", "Ma Boutique"),
                      ("resource-string", "vide", ""),
                      ("manifest-meta-data", "com.android.vending.splits", "config")]) == []


def test_une_valeur_demesuree_est_ignoree():
    """Une ressource de 4 ko n'est pas une clé — c'est un certificat ou du HTML."""
    assert keys.scan([("resource-string", "google_api_key", "A" * 4096)]) == []


def test_la_meme_cle_rangee_deux_fois_ne_fait_qu_un_fait():
    """Les outils de build déversent la même clé dans le manifeste et les ressources ;
    la publier deux fois laisserait croire à deux comptes."""
    valeur = "AIzaSyB" + "d" * 32
    found = keys.scan([("resource-string", "cle_maison", valeur),
                       ("manifest-meta-data", "com.google.android.geo.API_KEY", valeur)])
    assert len(found) == 1
    # Le rangement le plus précis gagne, quel que soit l'ordre de lecture.
    assert found[0]["id"] == "google-maps"


def test_deux_cles_distinctes_de_la_meme_famille_restent_deux_faits():
    found = keys.scan([("resource-string", "google_api_key", "AIza" + "e" * 35),
                       ("resource-string", "google_crash_reporting_api_key",
                        "AIza" + "f" * 35)])
    assert len(found) == 2


def test_l_empreinte_permet_de_voir_une_rotation():
    """Sans elle, « la clé a changé entre deux versions » demanderait de republier la
    clé pour être vérifiable."""
    avant = keys.scan([("resource-string", "google_api_key", "AIzaSy" + "g" * 33)])[0]
    apres = keys.scan([("resource-string", "google_api_key", "AIzaSy" + "h" * 33)])[0]
    assert avant["value_sha256"] != apres["value_sha256"]
    assert avant["value_prefix"] == apres["value_prefix"] == "AIzaSy"
