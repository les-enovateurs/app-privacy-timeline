"""Rendu lisible. Ce qui est testé, c'est ce qu'un delta doit laisser voir."""

from apklab import render


def snap(when, version="1.0", perms=("android.permission.A",), trackers=(1,),
         source="exodus-git", flags=()):
    return {"handle": "com.example.app", "slug": "example", "app_name": "Example",
            "observed_at": when, "version_name": version, "source": source,
            "permissions": list(perms),
            "trackers": [{"id": i, "name": f"T{i}"} for i in trackers],
            "quality_flags": list(flags)}


def delta(added=(), removed=(), trk_added=(), trk_removed=(), flags=("catalog_unknown",),
          series_flags=(), size=None):
    return {"window": {"from": "2026-01-01T00:00:00+00:00", "to": "2026-02-01T00:00:00+00:00"},
            "permissions": {"added": list(added), "removed": list(removed),
                            "exploitable": True},
            "trackers": {"added": [{"id": i, "name": f"T{i}"} for i in trk_added],
                         "removed": [{"id": i, "name": f"T{i}"} for i in trk_removed],
                         "flags": list(flags), "exploitable": not flags},
            "size": size, "series_flags": list(series_flags)}


def test_point_montre_les_comptes_et_les_defauts():
    line = render.point(snap("2026-01-01T00:00:00+00:00", flags=["empty_trackers"]))
    assert "2026-01-01" in line
    assert "1 perms" in line and "1 pisteurs" in line
    assert "[empty_trackers]" in line


def test_delta_montre_la_fenetre_et_jamais_une_date_seule():
    lines = render.delta(delta(added=["android.permission.RECORD_AUDIO"]))
    assert lines[0] == "  │ fenêtre 2026-01-01 → 2026-02-01"


def test_permissions_affichees_sans_leur_prefixe():
    """Le préfixe est le même sur toute la colonne, il ne porte aucune information."""
    lines = render.delta(delta(added=["android.permission.RECORD_AUDIO"]))
    assert "+1 RECORD_AUDIO" in lines[1]
    assert "android.permission" not in lines[1]


def test_liste_longue_tronquee():
    perms = [f"android.permission.P{n}" for n in range(10)]
    lines = render.delta(delta(added=perms))
    assert "… +6" in lines[1]


def test_delta_de_pisteurs_inexploitable_est_affiche_avec_son_motif():
    """Le masquer laisserait croire qu'il n'y a rien eu."""
    lines = render.delta(delta(trk_added=[7]))
    assert "+1 T7" in lines[-1]
    assert "inexploitable" in lines[-1]
    assert "catalogue inconnu" in lines[-1]


def test_delta_de_pisteurs_exploitable_n_a_pas_de_motif():
    lines = render.delta(delta(trk_added=[7], flags=[]))
    assert "inexploitable" not in lines[-1]


def test_changement_de_version_seul_est_dit():
    assert "version seule" in render.delta(delta())[-1]


def test_timeline_intercale_les_deltas_entre_leurs_bornes():
    series = [snap("2026-01-01T00:00:00+00:00"), snap("2026-02-01T00:00:00+00:00", "1.1")]
    lines = render.timeline(series, [delta(added=["android.permission.B"])])
    assert lines[0].startswith("  ● 2026-01-01")
    assert lines[-1].startswith("  ● 2026-02-01")
    assert any("fenêtre" in line for line in lines[1:-1])


def test_serie_vide_le_dit():
    assert render.timeline([], []) == ["  (aucun snapshot)"]


def test_overview_montre_les_applications_sans_snapshot():
    """Un trou est justement ce qu'on cherche à voir : disneyplus n'a aucun point parce
    que toutes ses révisions sont des faux positifs de renommage."""
    lines = render.overview([("disneyplus", [], []), ("example", [snap("2026-01-01T00:00:00+00:00")], [])])
    assert lines[1].startswith("disneyplus")
    assert "—" in lines[1]
    assert lines[2].startswith("example")


def test_overview_compte_les_deltas_exploitables():
    series = [snap("2026-01-01T00:00:00+00:00"), snap("2026-02-01T00:00:00+00:00")]
    lines = render.overview([("example", series, [delta(trk_added=[1]), delta(flags=[])])])
    assert "(1/2 pisteurs exploitables)" in lines[1]


def size_block(reference=None, flags=("abi_changed",)):
    block = {"size_scope": "merged-xapk", "flags": list(flags),
             "exploitable": not flags, "total_bytes_delta": -87}
    if reference is not None:
        block["reference"] = reference
    return block


def test_reference_size_is_shown_with_its_own_verdict():
    lines = render.delta(delta(flags=[], size=size_block(
        {"reference_bytes_delta": 12, "flags": [], "exploitable": True})))
    size_line, ref = lines[-2], lines[-1]
    assert "ABI différentes" in size_line
    assert "poids réf. arm64/xxhdpi" in ref and "+12 o" in ref
    assert "inexploitable" not in ref


def test_a_missing_reference_is_shown_barred():
    lines = render.delta(delta(flags=[], size=size_block(
        {"reference_bytes_delta": None, "flags": ["reference_missing"],
         "exploitable": False})))
    assert "—" in lines[-1]
    assert "référence absente d'un côté" in lines[-1]


def test_an_old_size_block_renders_without_a_reference_line():
    lines = render.delta(delta(flags=[], size=size_block()))
    assert not any("réf." in line for line in lines)


def test_density_flags_have_french_labels():
    lines = render.delta(delta(flags=[], size=size_block(flags=["density_changed"])))
    assert "densités différentes" in lines[-1]
