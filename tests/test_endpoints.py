"""Le classement des hôtes : ce qui est un endpoint, et tout ce qui n'en est pas un.

Le piège de cet étage est symétrique de celui de `evidence` : là-bas un nom de package
passait pour un domaine, ici un domaine réel passe pour un endpoint. `schemas.android.com`
existe, se résout, et n'est jamais contacté ; le publier ferait dire au dataset une
chose fausse sur l'application.
"""

from pathlib import Path

from apklab import endpoints

# La vraie table du dépôt : ces trois tests portent sur son contenu autant que sur le
# moteur, et une table de fixture ne dirait rien de ce qui est réellement classé.
HOST_CLASSES = Path(__file__).resolve().parents[1] / "signatures" / "host-classes.txt"


def table(*lines: str) -> dict[str, str]:
    return {host: cls for cls, host in (line.split() for line in lines)}


def test_un_hote_ordinaire_est_un_endpoint():
    assert endpoints.classify("api.adjust.com") == "endpoint"


def test_un_label_unique_n_est_pas_un_hote():
    """`gdpr`, `ssrv`, `graph` : des bases d'URL concaténées dans le code d'un SDK."""
    assert endpoints.classify("gdpr") == "invalid"
    assert endpoints.classify("") == "invalid"


def test_un_tld_inconnu_est_refuse():
    assert endpoints.classify("iamcache.braze") == "invalid"
    assert endpoints.classify("toto.png") == "invalid"


def test_une_ip_n_est_pas_un_hote():
    """`10.0.2.2` est le loopback de l'émulateur, présent dans deux applications."""
    assert endpoints.classify("10.0.2.2") == "invalid"


def test_un_suffixe_public_nu_est_refuse():
    """`www.blablacar.co.uk` est un hôte, `co.uk` seul n'en est pas un."""
    assert endpoints.classify("co.uk") == "invalid"
    assert endpoints.classify("www.blablacar.co.uk") == "endpoint"


def test_un_condensat_est_refuse():
    """Mesurés : un md5 nu, et deux identifiants clients OAuth de Google."""
    assert endpoints.classify("73b4a5a485454c8bb2b91075f3baa531") == "invalid"
    assert endpoints.classify(
        "123456789012-abcdefghijklmnopqrstuvwx01234567.apps.googleusercontent.com"
    ) == "invalid"


def test_un_tld_faible_est_du_code_par_defaut():
    """`user.id` et `dispatchers.io` ont la forme d'un domaine et n'en sont pas."""
    assert endpoints.classify("user.id") == "code"
    assert endpoints.classify("dispatchers.io") == "code"


def test_un_endpoint_sous_tld_faible_se_declare():
    """Sans la déclaration, Adjust et Screeb sortiraient du dataset."""
    declared = table("endpoint adj.st", "endpoint .screeb.app")
    assert endpoints.classify("adj.st", declared) == "endpoint"
    assert endpoints.classify("t.screeb.app", declared) == "endpoint"


def test_un_gabarit_d_exemple_est_reconnu_sur_son_label():
    """Le label entier, jamais la sous-chaîne : `api.facetec.com` contient `test`."""
    assert endpoints.classify("www.example.com") == "placeholder"
    assert endpoints.classify("api.facetec.com") == "endpoint"


def test_l_hote_exact_gagne_sur_le_suffixe():
    declared = table("doc .example.org", "endpoint api.example.org")
    assert endpoints.classify("www.example.org", declared) == "doc"
    assert endpoints.classify("api.example.org", declared) == "endpoint"


def test_la_structure_gagne_sur_la_table():
    """Aucune curation ne doit pouvoir déclarer endpoint ce qui n'est pas un hôte."""
    assert endpoints.classify("gdpr", table("endpoint gdpr")) == "invalid"


def test_la_table_se_lit_avec_ses_commentaires(tmp_path: Path):
    path = tmp_path / "host-classes.txt"
    path.write_text("# un commentaire\ndoc .w3.org\nendpoint adj.st  # inline\n"
                    "classe-inconnue foo.com\nligne cassée ici encore\n\n", "utf-8")
    assert endpoints.load_classes(path) == {".w3.org": "doc", "adj.st": "endpoint"}


def test_une_table_absente_ne_casse_rien(tmp_path: Path):
    assert endpoints.load_classes(tmp_path / "rien.txt") == {}


def test_le_proprietaire_se_lit_sur_la_marque_du_domaine():
    """Un SDK nomme ses classes d'après son domaine, à l'envers."""
    packages = ["com.braze.push", "com.adjust.sdk", "kotlinx.coroutines"]
    assert endpoints.owners("sdk.iad-01.braze.com", packages) == ["com.braze.push"]


def test_la_marque_est_le_label_du_domaine_pas_le_sous_domaine():
    """`b.applovin.com` appartient à AppLovin, pas au package `b` — la leçon de
    `candidates`, où le package `b` héritait de cet endpoint sur une lettre commune."""
    assert endpoints.owners("b.applovin.com", ["b.applovin", "com.b"]) == ["b.applovin"]


def test_une_marque_trop_courte_ne_correle_rien():
    assert endpoints.owners("t.me", ["com.t.sdk"]) == []


def test_l_agregat_compte_les_applications_et_pas_les_preuves():
    docs = [
        {"handle": "com.a", "domains": ["api.tiers.com"],
         "packages": [{"package": "com.tiers.sdk", "classes": 3}]},
        {"handle": "com.b", "domains": ["api.tiers.com", "user.id"], "packages": []},
    ]
    found = endpoints.collect(docs)
    assert [(h["host"], h["app_count"], h["class"]) for h in found] == [
        ("api.tiers.com", 2, "endpoint"), ("user.id", 1, "code")]
    assert found[0]["owner_packages"] == ["com.tiers.sdk"]


def test_un_hote_de_la_marque_du_paquet_est_premier_parti():
    docs = [{"handle": "fr.vinted", "domains": ["static-assets.vinted.com",
                                                "api.tiers.com"], "packages": []}]
    found = {h["host"]: h for h in endpoints.collect(docs)}
    assert found["static-assets.vinted.com"]["first_party"] is True
    assert found["api.tiers.com"]["first_party"] is False
    assert [h["host"] for h in endpoints.third_party(found.values())] == ["api.tiers.com"]


def test_une_marque_qui_differe_du_paquet_n_est_pas_devinee():
    """Comuto publie `com.comuto` et contacte `www.blablacar.fr` : deviner la parenté
    marquerait un jour premier parti l'endpoint d'un tiers homonyme."""
    docs = [{"handle": "com.comuto", "domains": ["www.blablacar.fr"], "packages": []}]
    assert endpoints.collect(docs)[0]["first_party"] is False


def test_le_bilan_par_classe_couvre_toutes_les_classes():
    docs = [{"handle": "com.a", "domains": ["api.tiers.com", "user.id"], "packages": []}]
    assert endpoints.counts(endpoints.collect(docs)) == {
        "endpoint": 1, "doc": 0, "placeholder": 0, "code": 1, "invalid": 0, "infra": 0}


def test_un_serveur_de_temps_n_est_pas_un_destinataire():
    """`pool.ntp.org` passe les règles structurelles — c'est un vrai domaine — et
    apparaissait 40 fois sur le corpus. « Cette application parle à huit serveurs
    tiers » là où le fait est « elle lit l'heure »."""
    table = endpoints.load_classes(HOST_CLASSES)
    assert endpoints.classify("0.pool.ntp.org", table) == "infra"
    assert endpoints.classify("2.datadog.pool.ntp.org", table) == "infra"


def test_un_hote_virtuel_de_webview_n_est_pas_contacte():
    """`appassets.androidplatform.net` sert des fichiers embarqués dans l'APK :
    WebViewAssetLoader s'en sert comme origine, aucune requête ne part."""
    table = endpoints.load_classes(HOST_CLASSES)
    assert endpoints.classify("appassets.androidplatform.net", table) == "infra"


def test_la_classe_infra_ne_traverse_pas_vers_le_site():
    hosts = [{"host": "api.tiers.com", "class": "endpoint", "first_party": False},
             {"host": "0.pool.ntp.org", "class": "infra", "first_party": False}]
    assert [h["host"] for h in endpoints.third_party(hosts)] == ["api.tiers.com"]


def test_la_marque_doit_ouvrir_le_package():
    """`play.google.com` se voyait attribuer `co.reachfive.identity.sdk.google`, un
    module d'intégration qui nomme Google sans lui appartenir."""
    packages = ["co.reachfive.identity.sdk.google", "com.google.android.gms.auth"]
    assert endpoints.owners("play.google.com", packages) == [
        "com.google.android.gms.auth"]


def test_le_compte_de_proprietaires_ne_s_arrete_pas_a_la_liste():
    """Une marque partagée par trente packages n'est attribuable à aucun ; lire
    `len(owner_packages)` annoncerait cinq et laisserait croire à une attribution."""
    docs = [{"handle": "com.a", "domains": ["play.google.com"],
             "packages": [{"package": f"com.google.p{n}", "classes": 3}
                          for n in range(9)]}]
    found = endpoints.collect(docs)[0]
    assert found["owner_package_count"] == 9
    assert len(found["owner_packages"]) == 5
