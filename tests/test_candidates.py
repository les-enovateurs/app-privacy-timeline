"""Les candidats : trois soustractions, une corrélation, et un statut humain sacré.

La propriété qui compte ici n'est pas le tri — c'est qu'une passe ne réécrive jamais un
jugement humain. Un `rejected` qui redevient `pending` ferait relire chaque mois les
mêmes faux positifs, et le tri manuel s'arrêterait au deuxième passage.
"""

from apklab import candidates
from apklab.signatures import Tracker

CATALOGUE = [Tracker(1, "Google Ads", r"com\.google\.android\.gms\.ads"),
             Tracker(2, "Adjust", r"com\.adjust\.sdk"),
             Tracker(3, "Cassée", r"[unclosed")]


def trouve(docs, trackers=None, allowlist=(), **kw):
    """`find` au plancher de bruit désactivé : les cas de test comptent en unités."""
    return candidates.find(docs, CATALOGUE if trackers is None else trackers,
                           allowlist, min_classes=kw.pop("min_classes", 1), **kw)


def preuve(handle, packages, domains=(), version="1.0", at="2026-01-01T00:00:00+00:00"):
    return {"handle": handle, "version_name": version, "observed_at": at,
            "domains": list(domains),
            "packages": [{"package": p, "classes": n, "sample": [f"{p}.C{n}"]}
                         for p, n in packages]}


def test_un_pisteur_catalogue_n_est_pas_un_candidat():
    found = trouve([preuve("com.app.x", [("com.adjust.sdk", 40)])],
                            CATALOGUE, [])
    assert found == []


def test_une_signature_qui_nomme_une_classe_couvre_son_package():
    """AdMob se cherche par `…ads.AdView` : aucun préfixe ne contient ce texte."""
    found = trouve(
        [preuve("com.app.x", [("com.google.android.gms.ads.internal", 358)])],
        [Tracker(312, "Google AdMob", r"com\.google\.android\.gms\.ads\.AdView")], [])
    assert found == []


def test_retirer_le_dernier_label_ne_remonte_jamais_au_dessus_du_package():
    """Si `com.google.ads.` donnait `com.google`, tout candidat Google disparaîtrait."""
    assert candidates.signature_packages(r"com\.google\.ads\.") == ["com.google.ads"]
    found = trouve([preuve("com.app.x", [("com.google.maps.tiles", 162)])],
                            [Tracker(312, "Google AdMob", r"com\.google\.ads\.")], [])
    assert [c["package"] for c in found] == ["com.google.maps.tiles"]


def test_un_prefixe_trop_haut_est_retire_si_son_echantillon_est_complet():
    """`com.google.android` ne peut être couvert par aucune signature ; ses classes si."""
    doc = {"handle": "com.app.x", "version_name": "1.0", "domains": [],
           "packages": [{"package": "com.google.android", "classes": 1,
                         "sample": ["com.google.android.gms.ads.internal.Overlay"]}]}
    assert trouve([doc], CATALOGUE, []) == []


def test_un_prefixe_trop_haut_reste_candidat_si_l_echantillon_est_partiel():
    """Au-delà de l'échantillon, un pisteur inconnu peut loger sous le même parapluie."""
    doc = {"handle": "com.app.x", "version_name": "1.0", "domains": [],
           "packages": [{"package": "com.google.android", "classes": 900,
                         "sample": ["com.google.android.gms.ads.internal.Overlay"]}]}
    assert [c["package"] for c in trouve([doc], CATALOGUE, [])] == [
        "com.google.android"]


def test_une_signature_cassee_ne_fait_pas_echouer_la_passe():
    """Le catalogue est de la donnée tierce ; analyze a déjà crié sur cette entrée."""
    found = trouve([preuve("com.app.x", [("com.inconnu.sdk", 3)])],
                            CATALOGUE, [])
    assert [c["package"] for c in found] == ["com.inconnu.sdk"]


def test_une_lib_benigne_est_soustraite():
    found = trouve([preuve("com.app.x", [("androidx.core.app", 90),
                                                  ("com.inconnu.sdk", 3)])],
                            CATALOGUE, ["androidx"])
    assert [c["package"] for c in found] == ["com.inconnu.sdk"]


def test_l_allowlist_compare_des_labels_pas_des_chaines():
    """`androidx` ne doit pas couvrir `com.androidxyz.tracker`."""
    found = trouve([preuve("com.app.x", [("com.androidxyz.tracker", 5)])],
                            CATALOGUE, ["androidx", "com.androidx"])
    assert [c["package"] for c in found] == ["com.androidxyz.tracker"]


def test_le_paquet_de_l_application_n_est_pas_un_tiers():
    found = trouve([preuve("com.carrefour.fid.android",
                                    [("com.carrefour.market", 200),
                                     ("com.inconnu.sdk", 3)])], CATALOGUE, [])
    assert [c["package"] for c in found] == ["com.inconnu.sdk"]


def test_la_prevalence_ordonne_et_agrege():
    docs = [preuve("com.app.x", [("com.inconnu.sdk", 3), ("com.rare.lib", 50)]),
            preuve("com.app.y", [("com.inconnu.sdk", 4)])]
    found = trouve(docs, CATALOGUE, [])
    assert [(c["package"], c["app_count"], c["class_count"]) for c in found] == [
        ("com.inconnu.sdk", 2, 7), ("com.rare.lib", 1, 50)]
    assert [a["handle"] for a in found[0]["apps"]] == ["com.app.x", "com.app.y"]


def test_min_apps_filtre_apres_agregation():
    docs = [preuve("com.app.x", [("com.inconnu.sdk", 3), ("com.rare.lib", 50)]),
            preuve("com.app.y", [("com.inconnu.sdk", 4)])]
    found = trouve(docs, CATALOGUE, [], min_apps=2)
    assert [c["package"] for c in found] == ["com.inconnu.sdk"]


def test_les_endpoints_viennent_du_nom_du_package_a_l_envers():
    doc = preuve("com.app.x", [("com.inconnu.sdk", 3)],
                 domains=["api.inconnu.com", "inconnu.com", "cdn.autre.com"])
    found = trouve([doc], CATALOGUE, [])
    assert found[0]["endpoints"] == ["api.inconnu.com", "inconnu.com"]


def test_un_package_sans_tld_en_tete_retombe_sur_sa_marque():
    doc = preuve("com.app.x", [("okhttp3.internal.tls", 3)], domains=["a.okhttp3.com"])
    found = trouve([doc], CATALOGUE, [])
    assert found[0]["endpoints"] == ["a.okhttp3.com"]


def test_un_hote_qui_n_est_pas_un_endpoint_ne_correle_rien():
    """`developers.google.com` et `console.firebase.google.com` étaient collés comme
    preuves aux quinze premiers candidats de la première passe : une doc et une console
    d'éditeur, jamais contactées. Un tri manuel décidait donc sur une preuve fausse."""
    doc = preuve("com.app.x", [("com.inconnu.sdk", 3)],
                 domains=["developers.inconnu.com", "api.inconnu.com"])
    found = trouve([doc], CATALOGUE, [],
                   host_classes={"developers.inconnu.com": "doc"})
    assert found[0]["endpoints"] == ["api.inconnu.com"]


def test_un_endpoint_sous_tld_faible_demande_sa_declaration():
    """`user.id` a la forme d'un domaine, `adj.st` en est un : seule la table tranche,
    et son absence coûte un faux négatif visible, jamais une preuve inventée."""
    doc = preuve("com.app.x", [("io.marque.sdk", 3)], domains=["marque.io"])
    assert trouve([doc], CATALOGUE, [])[0]["endpoints"] == []
    found = trouve([doc], CATALOGUE, [], host_classes={"marque.io": "endpoint"})
    assert found[0]["endpoints"] == ["marque.io"]


def test_un_voisin_catalogue_est_rendu_au_trieur():
    """`com.google.android.gms.auth` n'est couvert par aucune signature ; savoir que son
    voisin `…gms.ads` est AdMob est ce qui permet de le trier en une seconde."""
    doc = preuve("com.app.x", [("com.vendor.sdk.core", 3)])
    trackers = [Tracker(9, "Vendor Ads", r"com\.vendor\.sdk\.ads")]
    found = trouve([doc], trackers, [])
    assert found[0]["catalogued_siblings"] == ["Vendor Ads"]


def test_un_voisin_lointain_n_est_pas_rendu():
    """Remonter plus haut que le parent rattacherait tout candidat Google à AdMob."""
    doc = preuve("com.app.x", [("com.vendor.autre.chose", 3)])
    trackers = [Tracker(9, "Vendor Ads", r"com\.vendor\.sdk\.ads")]
    assert trouve([doc], trackers, [])[0]["catalogued_siblings"] == []


def test_un_parent_reduit_a_un_tld_n_a_pas_de_voisinage():
    """`com` n'est le voisinage de personne : tout le catalogue vit dessous.

    Mesuré sur la passe du 2026-09-14 : `com.reactnativekeyboardcontroller` se voyait
    attribuer plus de deux cents voisins catalogués, ce qui inversait le sens du champ.
    """
    trackers = [Tracker(9, "Vendor Ads", r"com\.vendor\.sdk\.ads"),
                Tracker(10, "Autre", r"org\.autre\.tracker"),
                Tracker(11, "Marque", r"marque\.espion\.Sonde")]
    assert candidates.catalogued_siblings("com.modulern", trackers) == []
    assert candidates.catalogued_siblings("org.webrtc", trackers) == []
    # Un parent d'un seul label qui n'est pas un TLD est une marque : il dit quelque
    # chose, et le garde-fou ne doit pas l'emporter avec le reste.
    assert candidates.catalogued_siblings("marque.interne", trackers) == ["Marque"]


def test_un_statut_humain_survit_a_la_passe():
    existing = {"candidates": [{"package": "com.inconnu.sdk", "status": "rejected",
                                "note": "lib de crash interne", "app_count": 1}]}
    fresh = trouve([preuve("com.app.x", [("com.inconnu.sdk", 3)])],
                            CATALOGUE, [])
    doc, counts = candidates.merge(existing, fresh, "2026-09-10T00:00:00+00:00", "v1")
    assert doc["candidates"][0]["status"] == "rejected"
    assert doc["candidates"][0]["note"] == "lib de crash interne"
    assert counts["new"] == 0 and counts["rejected"] == 1


def test_un_candidat_juge_qui_disparait_est_garde_et_marque():
    existing = {"candidates": [{"package": "com.parti.sdk", "status": "validated",
                                "app_count": 1}]}
    doc, counts = candidates.merge(existing, [], "2026-09-10T00:00:00+00:00", "v1")
    assert doc["candidates"] == [{"package": "com.parti.sdk", "status": "validated",
                                  "app_count": 1, "absent": True}]
    assert counts["absent"] == 1


def test_un_pending_disparu_est_oublie():
    """Personne ne l'a encore regardé : le garder gonflerait la file de tri."""
    existing = {"candidates": [{"package": "com.parti.sdk", "status": "pending"}]}
    doc, counts = candidates.merge(existing, [], "2026-09-10T00:00:00+00:00", "v1")
    assert doc["candidates"] == [] and counts["absent"] == 0


def test_un_nouveau_candidat_nait_pending():
    fresh = trouve([preuve("com.app.x", [("com.inconnu.sdk", 3)])],
                            CATALOGUE, [])
    doc, counts = candidates.merge(None, fresh, "2026-09-10T00:00:00+00:00", "v1")
    assert doc["candidates"][0]["status"] == "pending" and counts["new"] == 1


def test_l_allowlist_ignore_commentaires_et_vides(tmp_path):
    path = tmp_path / "allowlist.txt"
    path.write_text("# entête\n\nandroidx  # ui\nokio\n", "utf-8")
    assert candidates.load_allowlist(path) == ["androidx", "okio"]
    assert candidates.load_allowlist(tmp_path / "absent.txt") == []


def test_un_label_de_quatre_caracteres_avec_un_chiffre_est_un_artefact_r8():
    """`di30`, `q2a0`, `x200` : le dictionnaire de R8 en full mode, pas un SDK.

    Cinq applications sans rien de commun — Duolingo, ChatGPT, Reddit, Strava, Twitch —
    portent exactement les mêmes 9 594 noms de cette forme. Le renommeur est
    déterministe ; le nom ne dit rien de ce qu'il contient.
    """
    assert candidates.obfusque("di30")
    assert candidates.obfusque("q2a0")
    assert candidates.obfusque("x200")


def test_un_label_de_quatre_lettres_reste_un_candidat():
    """Le chiffre est ce qui garde la règle étroite : `amex` est une marque."""
    assert not candidates.obfusque("amex")
    assert not candidates.obfusque("type")
    assert not candidates.obfusque("mono")


def test_le_pont_jni_de_xamarin_ne_rentre_pas_dans_la_file():
    """`crc64` + le CRC de l'assembly : un nom qui change à chaque build."""
    assert candidates.obfusque("crc6452ffdc5b34af3a0f")
    assert not candidates.obfusque("crc64")
    assert not candidates.obfusque("com.crc64ffffffffffffffff")


def test_un_artefact_de_forme_ne_devient_pas_un_candidat():
    """Hors file, et pas `rejected` : il n'aurait jamais dû y entrer."""
    found = trouve([preuve("com.app.x", [("di30", 400), ("crc6452ffdc5b34af3a0f", 90),
                                         ("com.inconnu.sdk", 3)])], CATALOGUE, [])
    assert [c["package"] for c in found] == ["com.inconnu.sdk"]


def test_la_racine_est_l_editeur_pas_le_package():
    assert candidates.racine("com.stripe.android.paymentsheet") == "com.stripe"
    assert candidates.racine("expo.modules.location") == "expo"
    assert candidates.racine("atd.aq") == "atd"
    assert candidates.racine("okhttp3") == "okhttp3"


def test_le_repli_par_editeur_ne_somme_pas_les_applications():
    """202 packages de Stripe vivent dans les mêmes applications : le max, pas la somme.

    Les additionner donnerait 2 000 applications à un corpus qui en compte 131.
    """
    groupes = candidates.par_editeur([
        {"package": "com.stripe.android", "app_count": 13, "class_count": 100,
         "endpoints": ["api.stripe.com"], "catalogued_siblings": []},
        {"package": "com.stripe.android.core", "app_count": 11, "class_count": 40,
         "endpoints": [], "catalogued_siblings": ["Stripe"]},
        {"package": "com.inconnu.sdk", "app_count": 2, "class_count": 9,
         "endpoints": [], "catalogued_siblings": []},
    ])
    assert [(g["racine"], g["app_count"], len(g["packages"]), g["class_count"])
            for g in groupes] == [("com.stripe", 13, 2, 140), ("com.inconnu", 2, 1, 9)]
    assert groupes[0]["endpoints"] == ["api.stripe.com"]
    assert groupes[0]["catalogued_siblings"] == ["Stripe"]
