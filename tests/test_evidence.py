"""Les preuves : reconnaître un hôte sans confondre avec un nom de package, agréger.

Le piège central de cet étage est qu'un nom de package Java est un domaine écrit à
l'envers. Une reconnaissance naïve rendrait donc autant de « domaines » que de classes,
et `candidates` corrélerait chaque package avec lui-même.
"""

import json

from apklab import evidence


def test_une_url_donne_son_hote():
    assert evidence.hosts(["https://api.adjust.com/session?x=1"]) == ["api.adjust.com"]


def test_un_nom_de_package_n_est_pas_un_domaine():
    """`com.google.android.gms` finit sur `gms`, qui n'est pas un TLD."""
    assert evidence.hosts(["com.google.android.gms", "androidx.core.app"]) == []


def test_un_hote_nu_est_reconnu_sur_son_tld():
    assert evidence.hosts(["graph.facebook.com", "exemple.fr"]) == ["exemple.fr",
                                                                   "graph.facebook.com"]


def test_un_gabarit_garde_son_hote():
    """`%s.example.com` est la forme sous laquelle un SDK écrit souvent son endpoint."""
    assert evidence.hosts(["%s.crashlytics.com", "{region}.api.exemple.io"]) == [
        "api.exemple.io", "crashlytics.com"]


def test_ni_chemin_ni_phrase():
    assert evidence.hosts(["res/layout/main.xml", "une phrase avec des points.",
                           "", "a." * 200]) == []


def test_les_packages_sont_agreges_a_trois_labels():
    found = evidence.packages(["com.foo.bar.A", "com.foo.bar.baz.B", "com.foo.qux.C"])
    assert [(p["package"], p["classes"]) for p in found] == [
        ("com.foo.bar", 2), ("com.foo.qux", 1)]


def test_une_classe_courte_est_comptee_sous_ce_qu_elle_a():
    """Un dex obfusqué est justement celui où un SDK inconnu est le plus probable."""
    found = evidence.packages(["a.b.C", "D"])
    assert [p["package"] for p in found] == ["D", "a.b"]


def test_un_parapluie_est_eclate_un_label_plus_bas():
    """`com.google.android` emportait 15 408 classes de Grindr en un seul candidat."""
    classes = ([f"com.vendor.umbrella.gms.C{n}" for n in range(evidence.SPLIT_ABOVE)]
               + [f"com.vendor.umbrella.ads.C{n}" for n in range(10)])
    found = evidence.packages(classes)
    assert [p["package"] for p in found] == ["com.vendor.umbrella.ads",
                                             "com.vendor.umbrella.gms"]


def test_un_parapluie_sous_le_seuil_reste_au_grain_de_depart():
    classes = [f"com.vendor.umbrella.gms.C{n}" for n in range(evidence.SPLIT_ABOVE)]
    assert [p["package"] for p in evidence.packages(classes)] == ["com.vendor.umbrella"]


def test_la_descente_a_un_plancher():
    """Sans plancher, un dex obfusqué éclaterait en milliers de nœuds d'une classe."""
    classes = [f"com.a.b.c.d.e.f.g.C{n}" for n in range(evidence.SPLIT_ABOVE + 1)]
    found = evidence.packages(classes)
    assert [p["package"] for p in found] == [".".join(["com", "a", "b", "c", "d", "e"])]


def test_l_echantillon_est_borne():
    found = evidence.packages([f"com.foo.bar.C{n}" for n in range(20)])
    assert found[0]["classes"] == 20 and len(found[0]["sample"]) == evidence.SAMPLE


def test_une_version_avec_une_barre_ne_sort_pas_de_son_repertoire(tmp_path):
    path = evidence.path_for(tmp_path, "com.foo", "1.0/../../etc")
    assert path.parent == tmp_path / "com.foo" and path.name == "1.0_.._.._etc.json"


def test_load_all_ne_garde_que_la_version_la_plus_recente(tmp_path):
    """Deux versions d'une même application compteraient sa prévalence deux fois."""
    for version, at in (("1.0", "2026-01-01T00:00:00+00:00"),
                        ("2.0", "2026-02-01T00:00:00+00:00")):
        evidence.write(tmp_path, evidence.build(
            handle="com.foo", version_name=version, apk_sha256="ab", observed_at=at,
            analyzer_version="androguard 4", class_names=["com.foo.a.B"], domains=[]))
    docs = evidence.load_all(tmp_path)
    assert [d["version_name"] for d in docs] == ["2.0"]


def test_le_document_est_relisible(tmp_path):
    doc = evidence.build(handle="com.foo", version_name="1.0", apk_sha256="ab",
                         observed_at="2026-01-01T00:00:00+00:00",
                         analyzer_version="androguard 4",
                         class_names=["com.acme.sdk.Tracker"],
                         domains=["API.Acme.com ", ""])
    path = evidence.write(tmp_path, doc)
    assert json.loads(path.read_text("utf-8")) == doc
    assert doc["domains"] == ["api.acme.com"]


# ------------------------------------------------- quelle version fait autorité

def _pose(root, handle, version, observed_at, domaine):
    """Une preuve minimale sur le disque, pour n'éprouver que le choix de version."""
    doc = evidence.build(handle=handle, version_name=version, apk_sha256="0" * 64,
                         observed_at=observed_at, analyzer_version="test",
                         class_names=[], domains=[domaine], credentials=[])
    evidence.write(root, doc)


def test_la_version_la_plus_recente_gagne_pas_le_dernier_telechargement(tmp_path):
    """Le piège du backfill, et la raison du tri.

    Remonter une vieille version la fait arriver avec l'`observed_at` du jour. Trier
    là-dessus en ferait « la plus récente », et `endpoints`, `keys` et `candidates`
    décriraient un binaire de trois ans comme l'état actuel de l'application.
    """
    _pose(tmp_path, "com.app.x", "5.12.0", "2026-01-01T00:00:00+00:00", "actuel.example")
    _pose(tmp_path, "com.app.x", "4.44.1", "2026-09-11T00:00:00+00:00", "perime.example")
    docs = evidence.load_all(tmp_path)
    assert len(docs) == 1
    assert docs[0]["version_name"] == "5.12.0"
    assert docs[0]["domains"] == ["actuel.example"]


def test_le_tri_des_versions_est_naturel_pas_alphabetique(tmp_path):
    """`5.12.0` suit `5.9.0` dans le temps et le précède dans l'ordre des chaînes."""
    _pose(tmp_path, "com.app.x", "5.9.0", "2026-01-01T00:00:00+00:00", "vieux.example")
    _pose(tmp_path, "com.app.x", "5.12.0", "2026-02-01T00:00:00+00:00", "neuf.example")
    assert evidence.load_all(tmp_path)[0]["version_name"] == "5.12.0"


def test_a_version_egale_la_date_departage(tmp_path):
    """Deux analyses d'une même version : la plus récente des deux, faute de mieux."""
    _pose(tmp_path, "com.app.x", "5.12.0", "2026-01-01T00:00:00+00:00", "premier.example")
    _pose(tmp_path, "com.app.x", "5.12.0", "2026-02-01T00:00:00+00:00", "second.example")
    assert evidence.load_all(tmp_path)[0]["domains"] == ["second.example"]


def test_une_application_par_handle_quelles_que_soient_ses_versions(tmp_path):
    """La garantie que `candidates` attend : la prévalence compte des applications."""
    _pose(tmp_path, "com.app.x", "1.0", "2026-01-01T00:00:00+00:00", "a.example")
    _pose(tmp_path, "com.app.x", "2.0", "2026-02-01T00:00:00+00:00", "b.example")
    _pose(tmp_path, "com.app.y", "1.0", "2026-01-01T00:00:00+00:00", "c.example")
    assert sorted(d["handle"] for d in evidence.load_all(tmp_path)) == ["com.app.x", "com.app.y"]
