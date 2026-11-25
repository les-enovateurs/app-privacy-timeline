"""Les quatre règles du moteur de diff. C'est là que se joue la crédibilité des études."""

from apklab import store
from apklab.diff import build_delta, deltas_for
from apklab.models import Snapshot

T1 = {"id": 1, "name": "T1"}
T2 = {"id": 2, "name": "T2"}
CAT_A = "2026-01-01/400/sha256:aaa"
CAT_B = "2026-06-01/432/sha256:bbb"
T_A = "2026-01-01T00:00:00+00:00"
T_B = "2026-02-01T00:00:00+00:00"


def snap(when, **kw):
    base = dict(handle="x", slug="x", app_name="X", version_name="1.0",
                observed_at=when, permissions=[], trackers=[])
    base.update(kw)
    return Snapshot(**base).to_dict()


# --- règle 1 : une fenêtre, jamais une date --------------------------------

def test_delta_porte_une_fenetre_et_aucune_date_isolee():
    """Les runs sont manuels et irréguliers ; affirmer un jour précis serait inventé."""
    delta = build_delta(snap("2026-01-01T00:00:00+00:00"), snap("2026-02-01T00:00:00+00:00"))
    assert delta["window"] == {"from": "2026-01-01T00:00:00+00:00",
                               "to": "2026-02-01T00:00:00+00:00"}
    dates_isolees = [k for k, v in delta.items() if k.endswith("_at") and not isinstance(v, dict)]
    assert dates_isolees == []


# --- règle 2 : les trackers dépendent du catalogue -------------------------

def test_catalogue_inconnu_rend_la_delta_de_trackers_inexploitable():
    """Null signifie inconnu, pas identique."""
    delta = build_delta(snap("2026-01-01T00:00:00+00:00", trackers=[T1]),
                        snap("2026-02-01T00:00:00+00:00", trackers=[T1, T2]))
    assert delta["trackers"]["flags"] == ["catalog_unknown"]
    assert delta["trackers"]["exploitable"] is False


def test_catalogue_change_marque_analyzer_shift():
    """Si le catalogue grossit et qu'une app gagne un tracker, c'est peut-être le
    catalogue et pas l'app."""
    delta = build_delta(snap("2026-01-01T00:00:00+00:00", catalog_version=CAT_A, trackers=[T1]),
                        snap("2026-02-01T00:00:00+00:00", catalog_version=CAT_B,
                             trackers=[T1, T2]))
    assert delta["trackers"]["flags"] == ["analyzer_shift"]
    assert delta["trackers"]["exploitable"] is False


def test_meme_catalogue_rend_la_delta_de_trackers_exploitable():
    delta = build_delta(snap("2026-01-01T00:00:00+00:00", catalog_version=CAT_A, trackers=[T1]),
                        snap("2026-02-01T00:00:00+00:00", catalog_version=CAT_A,
                             trackers=[T1, T2]))
    assert delta["trackers"]["flags"] == []
    assert delta["trackers"]["exploitable"] is True
    assert delta["trackers"]["added"] == [T2]


# --- règle 3 : les permissions survivent au changement de source -----------

def test_permissions_exploitables_a_travers_un_changement_de_source():
    """Lues du manifeste et non d'un catalogue. C'est ce qui sauve les diffs du backfill."""
    delta = build_delta(snap("2026-01-01T00:00:00+00:00", source="exodus-git",
                             permissions=["A", "B"]),
                        snap("2026-02-01T00:00:00+00:00", source="apk-static",
                             analyzer="androguard", permissions=["A", "C"]))
    assert delta["permissions"]["exploitable"] is True
    assert delta["permissions"]["added"] == ["C"]
    assert delta["permissions"]["removed"] == ["B"]
    assert "source_changed" in delta["series_flags"]


# --- règle 4 : le poids n'est comparable qu'à périmètre égal ---------------

def test_poids_emis_entre_deux_apk_static_de_meme_perimetre():
    avant = snap("2026-01-01T00:00:00+00:00", source="apk-static", analyzer="androguard",
                 size_scope="single-apk", total_bytes=100, dex_bytes=40)
    apres = snap("2026-02-01T00:00:00+00:00", source="apk-static", analyzer="androguard",
                 size_scope="single-apk", total_bytes=160, dex_bytes=55)
    size = build_delta(avant, apres)["size"]
    assert size["size_scope"] == "single-apk"
    assert size["total_bytes_delta"] == 60
    assert size["dex_bytes_delta"] == 15


def test_poids_declare_lui_meme_une_version_en_recul():
    """Le bloc poids se déclare inexploitable, comme le bloc trackers pour le catalogue.

    Un consommateur qui ne lit que la valeur tracerait une baisse de poids là où la
    source a simplement servi une version antérieure.
    """
    avant = snap("2026-01-01T00:00:00+00:00", source="apk-static", analyzer="androguard",
                 version_name="23.6.0", size_scope="single-apk", total_bytes=160, native_abis=[])
    apres = snap("2026-02-01T00:00:00+00:00", source="apk-static", analyzer="androguard",
                 version_name="23.4.0", size_scope="single-apk", total_bytes=100, native_abis=[])
    delta = build_delta(avant, apres)
    assert delta["size"]["total_bytes_delta"] == -60
    assert delta["size"]["exploitable"] is False
    assert delta["size"]["flags"] == ["version_regressed"]
    assert "version_regressed" in delta["series_flags"]


def test_poids_exploitable_quand_la_version_avance():
    avant = snap("2026-01-01T00:00:00+00:00", source="apk-static", analyzer="androguard",
                 version_name="23.4.0", size_scope="single-apk", total_bytes=100, native_abis=[])
    apres = snap("2026-02-01T00:00:00+00:00", source="apk-static", analyzer="androguard",
                 version_name="23.6.0", size_scope="single-apk", total_bytes=160, native_abis=[])
    size = build_delta(avant, apres)["size"]
    assert size["exploitable"] is True and size["flags"] == []


def test_size_is_not_exploitable_when_the_source_serves_other_abis():
    """Grindr 2026-09-23: arm64+x86+x86_64 then armeabi-v7a alone, same merged-xapk, −87 MB."""
    avant = snap(T_A, source="apk-static", analyzer="androguard", size_scope="merged-xapk",
                 native_abis=["arm64-v8a", "x86", "x86_64"], total_bytes=185)
    apres = snap(T_B, source="apk-static", analyzer="androguard", size_scope="merged-xapk",
                 native_abis=["armeabi-v7a"], total_bytes=97)
    size = build_delta(avant, apres)["size"]
    assert size["total_bytes_delta"] == -88
    assert size["flags"] == ["abi_changed"]
    assert size["exploitable"] is False


def test_abi_order_is_not_a_change():
    avant = snap(T_A, source="apk-static", analyzer="androguard", size_scope="merged-xapk",
                 native_abis=["x86", "arm64-v8a"], total_bytes=100)
    apres = snap(T_B, source="apk-static", analyzer="androguard", size_scope="merged-xapk",
                 native_abis=["arm64-v8a", "x86"], total_bytes=110)
    assert build_delta(avant, apres)["size"]["exploitable"] is True


def test_unknown_abis_are_not_assumed_identical():
    avant = snap(T_A, source="apk-static", analyzer="androguard", size_scope="merged-xapk",
                 native_abis=None, total_bytes=100)
    apres = snap(T_B, source="apk-static", analyzer="androguard", size_scope="merged-xapk",
                 native_abis=["arm64-v8a"], total_bytes=110)
    assert build_delta(avant, apres)["size"]["flags"] == ["abi_unknown"]


def test_poids_supprime_si_le_perimetre_change():
    """Un XAPK APKPure contre un APK unique Play : un +40 % serait un artefact de source."""
    avant = snap("2026-01-01T00:00:00+00:00", source="apk-static", analyzer="androguard",
                 size_scope="single-apk", total_bytes=100)
    apres = snap("2026-02-01T00:00:00+00:00", source="apk-static", analyzer="androguard",
                 size_scope="merged-xapk", total_bytes=140)
    assert build_delta(avant, apres)["size"] is None


def test_poids_supprime_hors_analyse_statique():
    """Ni Exodus ni git ne portent le poids."""
    avant = snap("2026-01-01T00:00:00+00:00", source="exodus-git")
    apres = snap("2026-02-01T00:00:00+00:00", source="apk-static", analyzer="androguard",
                 size_scope="single-apk", total_bytes=100)
    assert build_delta(avant, apres)["size"] is None


# --- défauts de série -------------------------------------------------------

def test_version_regressee_signalee():
    delta = build_delta(snap("2026-01-01T00:00:00+00:00", version_name="2.0"),
                        snap("2026-02-01T00:00:00+00:00", version_name="1.9"))
    assert "version_regressed" in delta["series_flags"]


def test_chaine_rompue_signalee():
    cur = snap("2026-02-01T00:00:00+00:00", previous_observed_at="2025-01-01T00:00:00+00:00")
    delta = build_delta(snap("2026-01-01T00:00:00+00:00"), cur)
    assert "chain_broken" in delta["series_flags"]


def test_serie_saine_sans_defaut():
    cur = snap("2026-02-01T00:00:00+00:00", version_name="1.1",
               previous_observed_at="2026-01-01T00:00:00+00:00")
    assert build_delta(snap("2026-01-01T00:00:00+00:00"), cur)["series_flags"] == []


def test_has_change_faux_quand_seule_la_version_bouge():
    cur = snap("2026-02-01T00:00:00+00:00", version_name="1.1",
               previous_observed_at="2026-01-01T00:00:00+00:00")
    assert build_delta(snap("2026-01-01T00:00:00+00:00"), cur)["has_change"] is False


# --- série complète ---------------------------------------------------------

def test_deltas_for_paires_consecutives_dans_l_ordre(tmp_path):
    root = tmp_path / "snapshots"
    store.ingest(root, [Snapshot(handle="x", slug="x", app_name="X", version_name=v,
                                 observed_at=w, permissions=p)
                        for v, w, p in [("1.0", "2026-01-01T00:00:00+00:00", ["A"]),
                                        ("1.1", "2026-02-01T00:00:00+00:00", ["A", "B"]),
                                        ("1.2", "2026-03-01T00:00:00+00:00", ["A", "B", "C"])]],
                 write=True)

    deltas = deltas_for(root, "x")

    assert [(d["from_version"], d["to_version"]) for d in deltas] == [("1.0", "1.1"),
                                                                      ("1.1", "1.2")]
    assert all(d["permissions"]["added"] for d in deltas)
    assert all(d["series_flags"] == [] for d in deltas)


def test_serie_a_un_seul_point_ne_produit_aucune_delta(tmp_path):
    root = tmp_path / "snapshots"
    store.ingest(root, [Snapshot(handle="x", slug="x", app_name="X", version_name="1.0",
                                 observed_at="2026-01-01T00:00:00+00:00")], write=True)
    assert deltas_for(root, "x") == []


def test_a_change_of_binary_supplier_is_flagged():
    """`source_changed` wouldn't see it: `source` is `apk-static` on both sides."""
    a = snap(T_A, source="apk-static", acquisition="apkpure")
    b = snap(T_B, source="apk-static", acquisition="google-play")
    assert "acquisition_changed" in build_delta(a, b)["series_flags"]


def test_the_same_supplier_raises_no_flag():
    a = snap(T_A, source="apk-static", acquisition="apkpure")
    b = snap(T_B, source="apk-static", acquisition="apkpure")
    assert build_delta(a, b)["series_flags"] == []


def test_backfill_then_collection_does_not_stack_both_flags():
    """`exodus-git` snapshots carry no `acquisition`; without the `elif` every move to a
    downloaded binary would raise a second flag saying nothing new."""
    a = snap(T_A, source="exodus-git", acquisition=None)
    b = snap(T_B, source="apk-static", acquisition="apkpure")
    assert build_delta(a, b)["series_flags"] == ["source_changed"]


# --- reference phone ---------------------------------------------------------

def static(when, densities=None, **kw):
    """An apk-static snapshot; `densities` builds its size_parts, `None` means old."""
    if densities is not None:
        kw["size_parts"] = [{"name": "base.apk", "kind": "base", "qualifier": None,
                             "bytes": 1000}] + [
            {"name": f"config.{d}.apk", "kind": "density", "qualifier": d, "bytes": 10}
            for d in densities]
    kw.setdefault("native_abis", ["arm64-v8a"])
    return snap(when, source="apk-static", analyzer="androguard",
                size_scope="merged-xapk", **kw)


def test_reference_stays_exploitable_across_an_abi_change():
    """The whole point: 144 deltas died on `abi_changed`, this one survives it."""
    size = build_delta(
        static(T_A, ["xxhdpi"], native_abis=["arm64-v8a", "x86"], reference_bytes=1000),
        static(T_B, ["xxhdpi"], native_abis=["arm64-v8a"], reference_bytes=1100))["size"]
    assert "abi_changed" in size["flags"]
    assert size["exploitable"] is False
    assert size["reference"] == {"reference_bytes_delta": 100, "flags": [],
                                 "exploitable": True}


def test_changed_densities_make_the_global_size_unexploitable():
    size = build_delta(static(T_A, ["xxhdpi"], reference_bytes=1000),
                       static(T_B, ["xxhdpi", "hdpi"], reference_bytes=1000))["size"]
    assert "density_changed" in size["flags"]
    assert size["exploitable"] is False
    assert size["reference"]["exploitable"] is True


def test_one_old_side_marks_density_unknown_and_reference_missing():
    size = build_delta(static(T_A), static(T_B, ["xxhdpi"], reference_bytes=1000))["size"]
    assert "density_unknown" in size["flags"]
    assert size["reference"] == {"reference_bytes_delta": None,
                                 "flags": ["reference_missing"], "exploitable": False}


def test_a_null_reference_on_a_new_side_is_missing_too():
    size = build_delta(static(T_A, ["xxhdpi"], reference_bytes=1000),
                       static(T_B, ["hdpi"], reference_bytes=None))["size"]
    assert size["reference"]["flags"] == ["reference_missing"]


def test_a_reference_measured_on_two_different_densities_is_unexploitable():
    size = build_delta(static(T_A, ["xxhdpi"], reference_bytes=1000),
                       static(T_B, ["hdpi"], reference_bytes=990))["size"]
    assert size["reference"]["flags"] == ["reference_density_changed"]
    assert size["reference"]["exploitable"] is False


def test_the_same_substituted_density_on_both_sides_compares():
    size = build_delta(static(T_A, ["hdpi"], reference_bytes=1000),
                       static(T_B, ["hdpi"], reference_bytes=1100))["size"]
    assert size["reference"] == {"reference_bytes_delta": 100, "flags": [],
                                 "exploitable": True}


def test_an_extra_density_beside_xxhdpi_does_not_touch_the_reference():
    size = build_delta(static(T_A, ["xxhdpi"], reference_bytes=1000),
                       static(T_B, ["xxhdpi", "hdpi"], reference_bytes=1000))["size"]
    assert size["reference"]["flags"] == []


def test_two_old_sides_carry_no_reference():
    size = build_delta(static(T_A), static(T_B))["size"]
    assert "reference" not in size
    assert not {"density_changed", "density_unknown"} & set(size["flags"])


def test_a_reference_going_back_in_version_is_not_a_gain():
    size = build_delta(
        static(T_A, ["xxhdpi"], version_name="2.0", reference_bytes=1100),
        static(T_B, ["xxhdpi"], version_name="1.0", reference_bytes=1000))["size"]
    assert size["reference"]["flags"] == ["version_regressed"]
    assert size["reference"]["exploitable"] is False


def test_reference_needs_the_same_size_scope():
    prev = static(T_A, ["xxhdpi"], reference_bytes=1000)
    cur = {**static(T_B, ["xxhdpi"], reference_bytes=900), "size_scope": "single-apk"}
    assert build_delta(prev, cur)["size"] is None
