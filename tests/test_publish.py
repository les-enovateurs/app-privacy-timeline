"""La projection publique, hors CLI.

Une propriété porte tout le module : **rien ne traverse qui ne soit nommé**. Les tests
qui comptent ici ne vérifient pas qu'un champ attendu est présent — ça, une régression le
dirait bruyamment — mais qu'un champ *inattendu* ne l'est pas. C'est le sens de
`test_un_champ_inconnu_du_store_ne_traverse_pas` : il tient la liste blanche en place le
jour où le store gagnera un champ que personne n'a relu.

Le reste porte sur la phrase que le lecteur du site pourra écrire sans se tromper : un
delta inexploitable ne traverse pas, un hôte de documentation ne traverse pas, un
candidat non relu ne traverse pas, et un poids absent reste absent plutôt que nul.
"""

import json

from apklab import export, publish
from apklab.corpus import CorpusEntry


def entry(slug="x", handle="com.app.x", app_name="X"):
    return CorpusEntry(slug=slug, handle=handle, app_name=app_name, aliases=[])


def snap(version="1.0", source="apk-static", observed_at="2026-09-10T00:00:00+00:00",
         **extra):
    base = {"handle": "com.app.x", "slug": "x", "version_name": version,
            "source": source, "observed_at": observed_at, "permissions": [],
            "trackers": [], "quality_flags": []}
    base.update(extra)
    return base


def doc_prive(*, snapshots=None, deltas=None, hosts=None, accounts=None,
              candidates=None):
    return export.app_document(
        entry(), snapshots if snapshots is not None else [snap()], deltas or [],
        hosts=hosts or [], accounts=accounts or [], candidates=candidates or [])


# --------------------------------------------------------------- la liste blanche

def test_un_champ_inconnu_du_store_ne_traverse_pas():
    """Le test qui justifie la liste blanche.

    Un champ ajouté au store demain — chemin de collecte, nom de machine, empreinte de
    certificat — arrive du bon côté de la frontière sans que personne n'y pense. Une
    liste noire aurait publié tout ce qu'elle n'interdit pas explicitement.
    """
    privé = doc_prive(snapshots=[snap(chemin_de_collecte="/home/jp/apk/x.apk",
                                      dex_bytes=42)])
    public = publish.public_document(privé)
    assert "chemin_de_collecte" not in json.dumps(public)
    assert public["static"]["dex_bytes"] == 42


def test_les_snapshots_bruts_ne_traversent_pas():
    """Le dataset privé porte toute la série ; le site n'en reçoit que la dernière
    analyse statique et l'histoire du poids. Les permissions d'une version passée ne
    traversent que par les deltas, jamais comme une liste."""
    public = publish.public_document(doc_prive(snapshots=[
        snap("1.0", total_bytes=100, permissions=["android.permission.READ_CONTACTS"]),
        snap("2.0", total_bytes=200, permissions=["android.permission.CAMERA"]),
    ]))
    assert "snapshots" not in public
    assert public["static"]["version_name"] == "2.0"
    assert public["static"]["permissions"] == ["android.permission.CAMERA"]
    assert "READ_CONTACTS" not in json.dumps(public)
    assert [p["total_bytes"] for p in public["size_history"]] == [100, 200]


def test_trackers_not_searched_are_not_published_as_none_found():
    public = publish.public_document(doc_prive(snapshots=[
        snap(trackers=[], quality_flags=["trackers_not_analyzed"]),
    ]))
    assert "trackers" not in public["static"]
    assert public["static"]["permissions"] == []


# ------------------------------------------------------------- l'analyse statique

def test_le_poids_vient_de_la_derniere_analyse_statique_pas_du_dernier_point():
    """Le dernier point peut être un rapport Exodus, qui ne pèse rien. Publier ses
    champs absents donnerait un `dex_bytes: null` affiché « 0 octet »."""
    privé = doc_prive(snapshots=[
        snap("1.0", source="apk-static", dex_bytes=1000, min_sdk=24),
        snap("1.1", source="exodus-web", observed_at="2026-09-11T00:00:00+00:00"),
    ])
    public = publish.public_document(privé)
    assert public["static"]["dex_bytes"] == 1000
    assert public["static"]["version_name"] == "1.0"
    # `latest` reste le dernier point tout court : c'est la version que le site affiche,
    # et elle n'a pas à mentir parce que l'analyse statique est plus vieille.
    assert public["latest"]["version_name"] == "1.1"


def test_la_mesure_vient_de_la_version_la_plus_recente_pas_de_la_derniere_collecte():
    """Constaté en production le 2026-09-11, sur Carrefour.

    Remonter la 22.4.1 d'il y a un an la place en dernier dans la série, qui est ordonnée
    par date d'observation. La fiche publiait donc 142 Mo comme poids courant, quand la
    23.6.0 en vigueur en pèse 163. Même erreur que dans `evidence.load_all` : « le
    dernier » n'est pas « le plus récent » dès qu'on remonte le temps.
    """
    privé = doc_prive(snapshots=[
        snap("23.6.0", observed_at="2026-09-09T14:38:52+00:00", total_bytes=162557438),
        snap("22.4.1", observed_at="2026-09-11T06:49:11+00:00", total_bytes=141637334),
    ])
    public = publish.public_document(privé)
    assert public["static"]["version_name"] == "23.6.0"
    assert public["static"]["total_bytes"] == 162557438


def test_le_tri_des_versions_statiques_est_naturel():
    """`5.12.0` suit `5.9.0`, ce qu'un tri de chaînes affirmerait à l'envers."""
    privé = doc_prive(snapshots=[
        snap("5.12.0", observed_at="2026-01-01T00:00:00+00:00", total_bytes=200),
        snap("5.9.0", observed_at="2026-02-01T00:00:00+00:00", total_bytes=100),
    ])
    assert publish.public_document(privé)["static"]["total_bytes"] == 200


def test_pas_d_analyse_statique_donne_un_static_absent():
    """Absent, pas vide : un objet aux champs nuls se lirait comme une mesure à zéro."""
    public = publish.public_document(doc_prive(snapshots=[snap(source="exodus-git")]))
    assert public["static"] is None


def test_un_champ_de_mesure_absent_ne_devient_pas_nul():
    public = publish.public_document(doc_prive(snapshots=[snap(dex_bytes=10)]))
    assert "native_bytes" not in public["static"]



# ------------------------------------------------------------ l'histoire du poids

def test_l_histoire_du_poids_est_ordonnee_par_version():
    """`5.12.0` suit `5.9.0`, même si la 5.9.0 a été téléchargée après."""
    privé = doc_prive(snapshots=[
        snap("5.12.0", observed_at="2026-01-01T00:00:00+00:00", total_bytes=200),
        snap("5.9.0", observed_at="2026-02-01T00:00:00+00:00", total_bytes=100),
    ])
    série = publish.public_document(privé)["size_history"]
    assert [p["version_name"] for p in série] == ["5.9.0", "5.12.0"]


def test_une_version_ne_donne_qu_un_point():
    """Deux analyses d'une même version dessineraient une marche là où il n'y a qu'une
    reprise de collecte. Le point gardé est la dernière observation."""
    privé = doc_prive(snapshots=[
        snap("1.0", observed_at="2026-01-01T00:00:00+00:00", total_bytes=100),
        snap("1.0", observed_at="2026-02-01T00:00:00+00:00", total_bytes=105),
    ])
    série = publish.public_document(privé)["size_history"]
    assert [p["total_bytes"] for p in série] == [105]


def test_un_point_sans_poids_n_entre_pas_dans_l_histoire():
    """Un rapport Exodus ne pèse rien : entré comme zéro, une courbe le lirait comme un
    effondrement."""
    privé = doc_prive(snapshots=[
        snap("1.0", total_bytes=100),
        snap("1.1", source="exodus-web", observed_at="2026-09-11T00:00:00+00:00"),
        snap("1.2", total_bytes=None, observed_at="2026-09-12T00:00:00+00:00"),
    ])
    série = publish.public_document(privé)["size_history"]
    assert [p["version_name"] for p in série] == ["1.0"]


def test_un_changement_de_variante_est_declare():
    """APKPure sert tantôt un APK mono-ABI, tantôt un XAPK qui porte les quatre : le
    poids double sans qu'une ligne de code ait bougé. Le site doit pouvoir refuser de
    tracer cet écart, donc le fait traverse."""
    privé = doc_prive(snapshots=[
        snap("1.0", total_bytes=100, native_abis=["arm64-v8a"], size_scope="apk"),
        snap("1.1", total_bytes=200, observed_at="2026-09-11T00:00:00+00:00",
             native_abis=["arm64-v8a", "armeabi-v7a", "x86", "x86_64"],
             size_scope="merged-xapk"),
        snap("1.2", total_bytes=210, observed_at="2026-09-12T00:00:00+00:00",
             native_abis=["arm64-v8a", "armeabi-v7a", "x86", "x86_64"],
             size_scope="merged-xapk"),
    ])
    série = publish.public_document(privé)["size_history"]
    assert [p["abi_count"] for p in série] == [1, 4, 4]
    assert "variant_change" not in série[0]
    assert série[1]["variant_change"] is True
    assert "variant_change" not in série[2]


def test_l_histoire_du_poids_ne_porte_que_des_mesures_de_taille():
    """La liste blanche vaut ici aussi : un champ du store n'entre pas dans la série
    parce qu'il était sur le même snapshot."""
    privé = doc_prive(snapshots=[snap("1.0", total_bytes=100, apk_sha256="deadbeef",
                                      chemin_de_collecte="/home/jp/x.apk")])
    point = publish.public_document(privé)["size_history"][0]
    assert set(point) <= set(publish.SIZE_HISTORY_FIELDS) | {"abi_count", "variant_change"}


def test_pas_d_analyse_statique_donne_une_histoire_vide():
    """Vide, et non absente : une liste est ce qu'un consommateur itère sans test."""
    public = publish.public_document(doc_prive(snapshots=[snap(source="exodus-git")]))
    assert public["size_history"] == []


# ---------------------------------------------------------------------- les deltas

def delta(**over):
    base = {"window": {"from": "2026-01-01T00:00:00+00:00",
                        "to": "2026-02-01T00:00:00+00:00"},
            "from_version": "1.0", "to_version": "1.1",
            "from_source": "exodus-git", "to_source": "exodus-web",
            "permissions": {"added": [], "removed": [], "exploitable": True},
            "trackers": {"added": [], "removed": [], "flags": [], "exploitable": True},
            "size": None, "series_flags": [], "has_change": True}
    base.update(over)
    return base


def test_un_retrait_de_pisteurs_inexploitable_ne_traverse_pas():
    """La dette 9 du design, payée ici.

    Deux catalogues différents de part et d'autre : le fait est « le catalogue a changé »,
    et une fiche qui afficherait « trois pisteurs retirés » serait fausse.
    """
    d = delta(trackers={"added": [], "removed": [{"id": "a", "name": "A"}],
                        "flags": ["catalog_unknown"], "exploitable": False})
    assert publish._public_delta(d) is None


def test_un_ajout_de_permissions_traverse_meme_sur_changement_de_source():
    """Les permissions sont lues du manifeste de l'APK, pas d'un catalogue : elles
    survivent au changement de source qui invalide les pisteurs."""
    d = delta(permissions={"added": ["android.permission.CAMERA"], "removed": [],
                           "exploitable": True},
              trackers={"added": [], "removed": [{"id": "a", "name": "A"}],
                        "flags": ["analyzer_shift"], "exploitable": False})
    out = publish._public_delta(d)
    assert out["permissions"]["added"] == ["android.permission.CAMERA"]
    assert "trackers" not in out


def test_un_poids_en_regression_de_version_ne_traverse_pas():
    """Un poids qui baisse parce que la source a servi une version antérieure n'est pas
    un gain d'éco-conception."""
    d = delta(size={"size_scope": "apk", "flags": ["version_regressed"],
                    "exploitable": False, "dex_bytes_delta": -500})
    assert publish._public_delta(d) is None


def test_une_serie_qui_recule_ne_publie_aucun_delta():
    """Le cas vu sur Grindr : `26.15.1 → 26.2.0`, la source a servi un APK plus ancien.

    Le dataset privé garde ce delta et son bloc permissions exploitable — la comparaison
    de manifestes est valide dans les deux sens, et le privé décrit la collecte. Mais une
    fiche en tirerait « permission retirée », alors que l'application n'a rien retiré.
    """
    d = delta(from_version="26.15.1", to_version="26.2.0",
              permissions={"added": [], "removed": ["android.permission.CAMERA"],
                           "exploitable": True},
              series_flags=["version_regressed"])
    assert publish._public_delta(d) is None


def test_un_poids_identique_ne_fait_pas_un_changement():
    """Deux analyses d'une même version : `size` existe, tous ses écarts valent zéro, et
    `has_change` se déclare vrai parce que le bloc est là. La fiche afficherait une carte
    « de la 26.15.1 à la 26.15.1 » sans rien dedans."""
    d = delta(from_version="26.15.1", to_version="26.15.1",
              permissions={"added": [], "removed": [], "exploitable": True},
              size={"size_scope": "merged-xapk", "flags": [], "exploitable": True,
                    "total_bytes_delta": 0, "dex_bytes_delta": 0,
                    "native_bytes_delta": 0, "res_bytes_delta": 0,
                    "assets_bytes_delta": 0})
    assert publish._public_delta(d) is None


def test_un_poids_qui_bouge_traverse_avec_son_perimetre():
    d = delta(size={"size_scope": "merged-xapk", "flags": [], "exploitable": True,
                     "total_bytes_delta": 19096677, "dex_bytes_delta": -58398095})
    out = publish._public_delta(d)
    assert out["size"]["total_bytes_delta"] == 19096677
    # Le périmètre voyage avec l'écart : un poids d'archive fusionnée ne se compare pas à
    # un APK unique, et le lecteur doit pouvoir le savoir sans aller le chercher.
    assert out["size"]["size_scope"] == "merged-xapk"


def test_le_delta_publie_une_fenetre_jamais_une_date():
    """Règle 1 du moteur de diff, conservée jusqu'au site : les runs sont manuels et
    irréguliers, affirmer un jour précis serait une invention."""
    out = publish._public_delta(delta(permissions={"added": ["p"], "removed": [],
                                                   "exploitable": True}))
    assert set(out["window"]) == {"from", "to"}
    assert "report_date" not in out


def test_les_drapeaux_internes_ne_traversent_pas():
    """`series_flags` et `from_source` disent comment le fait a été collecté, pas ce
    qu'il est. Le lecteur du site n'en tire aucune phrase."""
    out = publish._public_delta(delta(permissions={"added": ["p"], "removed": [],
                                                   "exploitable": True},
                                      series_flags=["source_changed"]))
    assert "series_flags" not in out
    assert "from_source" not in out


def test_deux_chaines_depuis_le_meme_point_ne_publient_qu_un_delta():
    """Le cas vu sur Grindr : `26.2.0 → 26.13.0` par le rapport Exodus et
    `26.2.0 → 26.15.1` par l'analyse statique, portant le même ajout de permissions. Les
    deux sont vrais ; publier les deux fait lire deux fois le même ajout."""
    a = delta(from_version="26.2.0", to_version="26.13.0",
              window={"from": "2026-02-01T00:00:00+00:00", "to": "2026-08-04T00:00:00+00:00"},
              permissions={"added": ["p"], "removed": [], "exploitable": True})
    b = delta(from_version="26.2.0", to_version="26.15.1",
              window={"from": "2026-02-01T00:00:00+00:00", "to": "2026-09-10T00:00:00+00:00"},
              permissions={"added": ["p"], "removed": [], "exploitable": True})
    gardés = publish._linear([a, b])
    assert [d["to_version"] for d in gardés] == ["26.15.1"]


def test_deux_departs_distincts_publient_deux_deltas():
    """La règle porte sur le départ, pas sur l'arrivée : une série qui avance
    normalement garde tous ses pas."""
    a = delta(from_version="1.0", to_version="1.1",
              window={"from": "2026-01-01T00:00:00+00:00", "to": "2026-02-01T00:00:00+00:00"},
              permissions={"added": ["p"], "removed": [], "exploitable": True})
    b = delta(from_version="1.1", to_version="1.2",
              window={"from": "2026-02-01T00:00:00+00:00", "to": "2026-03-01T00:00:00+00:00"},
              permissions={"added": ["q"], "removed": [], "exploitable": True})
    assert [d["to_version"] for d in publish._linear([a, b])] == ["1.1", "1.2"]


def test_les_deltas_publies_sont_ordonnes_dans_le_temps():
    """Le site les retourne pour lire du plus récent au plus ancien ; encore faut-il
    qu'ils arrivent triés."""
    tard = delta(from_version="2.0", to_version="2.1",
                 window={"from": "2026-05-01T00:00:00+00:00", "to": "2026-06-01T00:00:00+00:00"},
                 permissions={"added": ["p"], "removed": [], "exploitable": True})
    tôt = delta(from_version="1.0", to_version="1.1",
                window={"from": "2026-01-01T00:00:00+00:00", "to": "2026-02-01T00:00:00+00:00"},
                permissions={"added": ["q"], "removed": [], "exploitable": True})
    assert [d["to_version"] for d in publish._linear([tard, tôt])] == ["1.1", "2.1"]


# -------------------------------------------------------------------- les endpoints

def host(name, klass="endpoint", first_party=False, app_count=3):
    return {"host": name, "class": klass, "first_party": first_party,
            "app_count": app_count, "apps": ["com.app.x", "com.app.y", "com.app.z"],
            "owner_packages": ["com.tiers.sdk"], "owner_package_count": 1}


def test_seuls_les_hotes_classes_endpoint_et_tiers_traversent():
    """Le piège nommé dans le brief : « cette application contacte N domaines » où N
    compte de la documentation, des gabarits et des identifiants de code."""
    hosts = [host("api.tiers.com"), host("www.w3.org", klass="doc"),
             host("example.com", klass="placeholder"), host("user.id", klass="code"),
             host("api.app.x", first_party=True)]
    out = publish._public_endpoints(hosts)
    assert [h["host"] for h in out["hosts"]] == ["api.tiers.com"]
    assert out["third_party_count"] == 1
    assert out["excluded_count"] == 4


def test_le_bloc_porte_la_base_de_sa_mesure():
    """L'analyse est statique : un nom relevé dans un binaire, pas une requête vue
    partir. La réserve voyage dans l'objet, pas dans une note de bas de page — un
    consommateur qui ne lit que `third_party_count` doit la trouver là."""
    assert publish._public_endpoints([host("api.tiers.com")])["basis"] == "strings-in-binary"


def test_l_hote_garde_sa_prevalence_et_perd_la_liste_des_handles():
    """La prévalence sur le corpus est le fait opposable — « dix applications portent
    cet hôte » — et c'est elle qui traverse. La liste des handles n'apprendrait rien de
    plus et ferait porter le corpus entier par chaque document."""
    out = publish._public_endpoints([host("api.tiers.com", app_count=10)])
    assert out["hosts"][0]["app_count"] == 10
    assert "apps" not in out["hosts"][0]
    # `owner_packages` reste : c'est le package qui porte la marque du destinataire, et
    # donc ce qui rend le fait opposable plutôt qu'une simple chaîne de caractères.
    assert out["hosts"][0]["owner_packages"] == ["com.tiers.sdk"]


def test_la_classe_de_l_hote_ne_traverse_pas():
    """Tous les hôtes publiés sont de classe `endpoint` par construction : republier le
    champ inviterait un consommateur à filtrer un ensemble déjà filtré."""
    out = publish._public_endpoints([host("api.tiers.com")])
    assert "class" not in out["hosts"][0]


# ----------------------------------------------------------------- les comptes SDK

def test_aucune_valeur_de_compte_sdk_ne_traverse():
    """Rien n'est secret — préfixe de six caractères et empreinte tronquée, tout vient
    du binaire distribué. Mais un compte nomme un sous-traitant par éditeur, et le
    publier est un geste que ce module ne prend pas."""
    accounts = [{"id": "firebase", "label": "projet Firebase", "app_count": 1,
                 "apps": ["com.app.x"], "value_prefix": "mon-es",
                 "value_sha256": "59dccc63a2d4", "secret": False, "shared": False,
                 "names": ["google_app_id"], "origins": ["resource-string"]}]
    out = publish._public_accounts(accounts)
    assert out == {"by_sdk": {"firebase": 1}}
    assert "mon-es" not in json.dumps(out)
    assert "59dccc63a2d4" not in json.dumps(out)


# ------------------------------------------------------------ les candidats pisteurs

def test_aucun_nom_de_package_candidat_ne_traverse():
    """Un `pending` n'est signé par personne : le publier nommerait un éditeur sur une
    preuve non relue. Un `rejected` nommé se lirait comme une accusation retirée, alors
    que le fait est qu'il n'y avait rien à dire. Restent les compteurs, qui décrivent la
    méthode et non un tiers."""
    cands = [{"package": "com.editeur.sdk", "status": "pending", "app_count": 4,
              "sample_classes": ["com.editeur.sdk.Tracker"], "class_count": 12,
              "endpoints": ["collect.editeur.com"], "catalogued_siblings": []},
             {"package": "com.autre.sdk", "status": "rejected", "app_count": 2,
              "sample_classes": [], "class_count": 3, "endpoints": [],
              "catalogued_siblings": []}]
    out = publish._public_candidates(cands)
    assert out == {"examined": 2, "by_status": {"pending": 1, "rejected": 1}}
    texte = json.dumps(out)
    assert "com.editeur.sdk" not in texte
    assert "collect.editeur.com" not in texte


def test_le_compte_des_pending_est_publie_sans_leurs_noms():
    """Le volume relu est une information sur la méthode, et la taire donnerait à croire
    que le tri est terminé."""
    out = publish._public_candidates([{"package": "a", "status": "pending"}] * 230)
    assert out["by_status"]["pending"] == 230


# ------------------------------------------------------------------- le manifeste

def test_le_manifeste_porte_l_empreinte_de_chaque_document():
    """Le manifeste privé ne le fait pas : là-bas dataset et index sont écrits sur le
    même disque. Ici ils traversent un réseau et se font recopier par un script qui n'a
    pas produit la donnée."""
    public = publish.public_document(doc_prive(snapshots=[snap(dex_bytes=1)]))
    texte = publish.dumps(public)
    ligne = publish.index_entry(public, "apps/x.json", texte)
    import hashlib
    assert ligne["sha256"] == hashlib.sha256(texte.encode("utf-8")).hexdigest()
    assert ligne["file"] == "apps/x.json"


def test_le_manifeste_ne_publie_pas_les_applications_ecartees():
    """`skipped` dirait quelles applications sont au corpus sans y être analysées : une
    information sur le plan de collecte, pas sur les applications."""
    m = publish.manifest(generated_at="2026-09-10T00:00:00+00:00", apps=[],
                         catalog_version="2026-09-09/432")
    assert "skipped" not in m
    assert "sources" not in m
    assert m["visibility"] == "public"


def test_deux_passes_produisent_les_memes_octets():
    """Sans quoi la porte d'idempotence côté CI republierait à chaque tour."""
    privé = doc_prive(snapshots=[snap(dex_bytes=1)])
    assert publish.dumps(publish.public_document(privé)) == \
           publish.dumps(publish.public_document(privé))


# ------------------------------------------------------------ reference phone

def test_a_switch_between_two_single_abis_is_a_variant_change():
    """`abi_count` stays 1 from arm64 alone to armeabi-v7a alone; the binary doesn't."""
    private = doc_prive(snapshots=[
        snap("1.0", total_bytes=100, native_abis=["arm64-v8a"], size_scope="merged-xapk"),
        snap("1.1", total_bytes=60, observed_at="2026-09-11T00:00:00+00:00",
             native_abis=["armeabi-v7a"], size_scope="merged-xapk"),
    ])
    series = publish.public_document(private)["size_history"]
    assert [p["abi_count"] for p in series] == [1, 1]
    assert series[1]["variant_change"] is True


def test_reference_size_joins_the_history_only_when_measured():
    private = doc_prive(snapshots=[
        snap("1.0", total_bytes=100),
        snap("1.1", total_bytes=120, observed_at="2026-09-11T00:00:00+00:00",
             reference_bytes=90),
    ])
    series = publish.public_document(private)["size_history"]
    assert "reference_bytes" not in series[0]
    assert series[1]["reference_bytes"] == 90


def test_an_exploitable_reference_crosses_under_an_unexploitable_size():
    d = delta(size={"size_scope": "merged-xapk", "flags": ["abi_changed"],
                    "exploitable": False, "total_bytes_delta": -87000000,
                    "reference": {"reference_bytes_delta": 1200, "flags": [],
                                  "exploitable": True}})
    out = publish._public_delta(d)
    assert "size" not in out
    assert out["size_reference"] == {"reference_bytes_delta": 1200,
                                     "size_scope": "merged-xapk"}


def test_an_unexploitable_reference_does_not_cross():
    d = delta(size={"size_scope": "merged-xapk", "flags": ["abi_changed"],
                    "exploitable": False, "total_bytes_delta": -87000000,
                    "reference": {"reference_bytes_delta": None,
                                  "flags": ["reference_missing"], "exploitable": False}})
    assert publish._public_delta(d) is None


def test_an_unchanged_reference_is_not_a_change():
    d = delta(size={"size_scope": "merged-xapk", "flags": ["abi_changed"],
                    "exploitable": False, "total_bytes_delta": 5000,
                    "reference": {"reference_bytes_delta": 0, "flags": [],
                                  "exploitable": True}})
    assert publish._public_delta(d) is None
