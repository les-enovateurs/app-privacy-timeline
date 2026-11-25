"""Lecture des rapports Exodus. Les fixtures sont des pages réelles, non retouchées.

Une page réelle plutôt qu'un extrait fabriqué : ce module ne se casse pas sur une logique
métier mais sur un changement de gabarit chez Exodus, et un HTML écrit à la main ne
détecterait jamais ce changement-là.
"""

from pathlib import Path

import pytest

from apklab import backfill_exodus as bx
from apklab.corpus import CorpusEntry

FIXTURES = Path(__file__).parent / "fixtures" / "exodus"
FACEBOOK = CorpusEntry(slug="facebook", handle="com.facebook.katana", app_name="Facebook")
NAMES = {65: "Facebook Ads", 66: "Facebook Analytics", 99: "Inconnu"}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


# ------------------------------------------------------------------------- liste

def test_liste_rend_les_25_rapports_et_la_page_suivante():
    refs, next_page = bx.parse_list(fixture("search_facebook_p1.html"))
    assert len(refs) == 25
    assert next_page == 2
    assert refs[0] == bx.ReportRef(777787, "577.0.0.50.72", "google")


def test_liste_vide_ne_rend_rien_et_pas_de_page_suivante():
    """Un handle jamais scanné rend une page 200 sans aucun rapport. Ce n'est pas une
    erreur : l'application sort du backfill, elle n'échoue pas."""
    refs, next_page = bx.parse_list(fixture("search_unknown.html"))
    assert refs == []
    assert next_page is None


def test_version_sans_magasin_reste_entiere():
    """Le suffixe n'est découpé que s'il ne contient pas d'espace, sinon « 2.1 - beta 3 »
    perdrait sa fin."""
    html = ('<a href="/fr/reports/1/" class="stretched-link report-link"></a>'
            '<div class="small"><b>Version 2.1 - beta 3</b></div>')
    refs, _ = bx.parse_list(html)
    assert refs == [bx.ReportRef(1, "2.1 - beta 3", None)]


def test_rapport_sans_ligne_de_version_ne_prend_pas_celle_du_suivant():
    """Le découpage par ancre est là pour ça : une regex unique reliant le lien à la
    ligne de version sauterait dans le bloc d'après."""
    html = ('<a href="/fr/reports/1/" class="stretched-link report-link"></a>'
            '<a href="/fr/reports/2/" class="stretched-link report-link"></a>'
            '<div class="small"><b>Version 9.9 - google</b></div>')
    refs, _ = bx.parse_list(html)
    assert refs[0].version_name is None
    assert refs[1].version_name == "9.9"


# ------------------------------------------------------------------------- dédup

def test_dedup_garde_un_rapport_par_version():
    """Exodus re-scanne la même version : 576.0.0.42.73 apparaît quatre fois sur la
    première page de Facebook."""
    refs, _ = bx.parse_list(fixture("search_facebook_p1.html"))
    kept, issues = bx.dedupe_by_version(refs)

    versions = [ref.version_name for ref in kept]
    assert len(versions) == len(set(versions))
    assert len(kept) + len(issues) == len(refs)
    assert all(issue.reason == "duplicate_version" for issue in issues)


def test_dedup_garde_le_plus_ancien_rapport_de_la_version():
    """Le premier scan date l'apparition de la version ; un re-scan de 2024 sur une
    version de 2019 placerait le point six ans trop tard."""
    refs = [bx.ReportRef(900, "1.0", "google"), bx.ReportRef(100, "1.0", "google")]
    kept, issues = bx.dedupe_by_version(refs)
    assert [ref.report_id for ref in kept] == [100]
    assert issues[0].ref == "900"


def test_dedup_conserve_les_rapports_sans_version():
    """Non dédoublonnables, mais ce sont des points de mesure réels."""
    refs = [bx.ReportRef(1, None, None), bx.ReportRef(2, None, None)]
    kept, issues = bx.dedupe_by_version(refs)
    assert [ref.report_id for ref in kept] == [1, 2]
    assert issues == []


# ------------------------------------------------------------------------ détail

def test_detail_rend_les_faits_du_bloc_technique():
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    assert report.handle == "com.facebook.katana"
    assert report.version_name == "576.0.0.42.73"
    assert report.version_code == 474227001
    assert report.apk_sha256 == \
        "2023f36cf0c81e69ae35c3ad6a120cbd449c0de4c4f346ec5b47ed96546af28e"
    assert report.created_at == "2026-08-31T07:31:00+00:00"
    assert report.updated_at is None
    assert report.store == "google play"


def test_detail_compte_ce_que_la_page_annonce():
    """9 pisteurs et 92 permissions au badge. Les pisteurs sont lus dans leur section :
    un balayage de toute la page en ramènerait dix, le dixième étant un lien du bandeau
    de navigation."""
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    assert len(report.tracker_ids) == 9
    assert len(report.permissions) == 92
    assert 65 in report.tracker_ids
    assert "android.permission.ACCESS_ADSERVICES_AD_ID" in report.permissions


def test_detail_garde_les_permissions_non_android():
    """Une permission propre au constructeur est une permission comme une autre."""
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    assert "com.sonyericsson.home.permission.BROADCAST_BADGE" in report.permissions


def test_detail_lit_la_date_de_re_scan():
    """« Rapport créé le 28 octobre 2019 20:31 et mis à jour le 19 juillet 2024 01:00 » :
    c'est la création qui date le point, la mise à jour qui qualifie ses pisteurs."""
    report = bx.parse_report(fixture("report_100000.html"), 100000)
    assert report.created_at == "2019-10-28T20:31:00+00:00"
    assert report.updated_at == "2024-07-19T01:00:00+00:00"


def test_detail_sans_section_permissions_est_une_erreur():
    with pytest.raises(bx.ParseError):
        bx.parse_report("<html><body>rien</body></html>", 1)


@pytest.mark.parametrize("text, expected", [
    ("31 août 2026 07:31", "2026-08-31T07:31:00+00:00"),
    ("1 décembre 2020 00:05", "2020-12-01T00:05:00+00:00"),
    ("7 mai 2026 10:53", "2026-05-07T10:53:00+00:00"),
    ("hier", None),
    ("31 brumaire 2026 07:31", None),
])
def test_date_francaise(text, expected):
    assert bx.parse_french_datetime(text) == expected


# ---------------------------------------------------------------------- snapshot

def test_snapshot_porte_la_provenance_et_le_binaire_analyse():
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    snap, issue = bx.snapshot_for(FACEBOOK, report, NAMES)

    assert issue is None
    assert snap.source == "exodus-web"
    assert snap.provenance_ref == "775051"
    assert snap.acquisition == "google-play"
    assert snap.apk_sha256.startswith("2023f36c")
    assert snap.observed_at == snap.report_date == "2026-08-31T07:31:00+00:00"
    assert snap.version_code == 474227001


def test_snapshot_prend_son_nom_du_corpus():
    """main-title rend « Open in app » sur cette page, et ailleurs des références de
    ressource non résolues du type @7F1400C5."""
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    snap, _ = bx.snapshot_for(FACEBOOK, report, NAMES)
    assert snap.app_name == "Facebook"


def test_snapshot_nomme_les_pisteurs_connus_et_laisse_les_autres_nuls():
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    snap, _ = bx.snapshot_for(FACEBOOK, report, NAMES)
    par_id = {t["id"]: t["name"] for t in snap.trackers}
    assert par_id[65] == "Facebook Ads"
    assert par_id[392] is None


def test_snapshot_sans_catalogue_connu():
    """La page publie ses pisteurs, pas le catalogue qui les a détectés — et pour un
    rapport de 2019 ce catalogue n'existe plus."""
    report = bx.parse_report(fixture("report_100000.html"), 100000)
    entry = CorpusEntry(slug="garmin", handle="com.garmin.android.apps.phonelink",
                        app_name="Garmin")
    snap, _ = bx.snapshot_for(entry, report, NAMES)
    assert snap.catalog_version is None
    assert "report_updated" in snap.quality_flags


def test_snapshot_refuse_un_autre_paquet():
    """Même garde-fou qu'au backfill git, où il a écarté 94 révisions."""
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    autre = CorpusEntry(slug="fnac", handle="com.fnac.android", app_name="Fnac")
    snap, issue = bx.snapshot_for(autre, report, NAMES)
    assert snap is None
    assert issue.reason == "handle_mismatch"


def test_snapshot_refuse_une_date_illisible():
    """Sans date de création le point n'a pas de place dans la série, et une place
    fausse fabriquerait des fenêtres d'incertitude fausses."""
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    report.created_at = None
    snap, issue = bx.snapshot_for(FACEBOOK, report, NAMES)
    assert snap is None
    assert issue.reason == "missing_report_date"


def test_snapshot_signale_une_version_divergente_entre_liste_et_detail():
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    ref = bx.ReportRef(775051, "999.0", "google")
    snap, _ = bx.snapshot_for(FACEBOOK, report, NAMES, ref)
    assert snap.version_name == "576.0.0.42.73"
    assert "version_mismatch" in snap.quality_flags


def test_snapshot_sans_version_du_tout_est_marque():
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    report.version_name = None
    snap, _ = bx.snapshot_for(FACEBOOK, report, NAMES, bx.ReportRef(775051, None, None))
    assert snap.version_name is None
    assert "missing_version_name" in snap.quality_flags


def test_magasin_inconnu_ne_donne_pas_d_acquisition():
    """Le schéma n'accepte que trois valeurs ; un poids venu d'une source non identifiée
    n'est comparable à rien."""
    report = bx.parse_report(fixture("report_775051.html"), 775051)
    report.store = "boutique inconnue"
    snap, _ = bx.snapshot_for(FACEBOOK, report, NAMES)
    assert snap.acquisition is None
