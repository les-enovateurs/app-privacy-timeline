"""Les quatre rejets du backfill. Le plus important est handle_mismatch."""

import json

from apklab.backfill_git import RevisionBlob, snapshots_for
from apklab.corpus import CorpusEntry

from conftest import TRACKER_NAMES, fiche

ENTRY = CorpusEntry(slug="carrefour", handle="com.carrefour", app_name="Carrefour")


def rev(sha: str, when: str, doc) -> RevisionBlob:
    raw = doc if isinstance(doc, bytes) else json.dumps(doc).encode("utf-8")
    return RevisionBlob(sha=sha, authored_at=when, path="public/data/compare/carrefour.json",
                        raw=raw)


def build(*revs):
    return snapshots_for(ENTRY, list(revs), TRACKER_NAMES)


def test_handle_different_rejete():
    """git log --follow détecte les renommages par similarité, et deux fiches Exodus se
    ressemblent : la 1re révision de carrefour.json est le blob de fnac.json. Sans ce
    rejet, la série de Carrefour démarre sur les permissions de la Fnac."""
    snaps, issues = build(
        rev("aaa", "2025-01-01T00:00:00+00:00",
            fiche("com.fnac", "5.5", "2025-01-01T00:00:00Z", ["FNAC"], [1])),
        rev("bbb", "2025-02-01T00:00:00+00:00",
            fiche("com.carrefour", "21.6", "2025-01-15T00:00:00Z", ["A"], [1])),
    )

    assert [s.version_name for s in snaps] == ["21.6"]
    assert [(i.sha, i.reason) for i in issues] == [("aaa", "handle_mismatch")]


def test_blob_qui_nest_pas_un_objet_rejete():
    snaps, issues = build(rev("aaa", "2025-01-01T00:00:00+00:00", [{"handle": "com.carrefour"}]))
    assert snaps == []
    assert [i.reason for i in issues] == ["not_an_object"]


def test_json_illisible_rejete():
    snaps, issues = build(rev("aaa", "2025-01-01T00:00:00+00:00", b"{pas du json"))
    assert snaps == []
    assert [i.reason for i in issues] == ["invalid_json"]


def test_point_de_mesure_deja_vu_rejete():
    """Le site recommite des fiches inchangées ; même (version, report_date) = même point."""
    doc = fiche("com.carrefour", "21.6", "2025-01-15T00:00:00Z", ["A"], [1])
    snaps, issues = build(rev("aaa", "2025-01-01T00:00:00+00:00", doc),
                          rev("bbb", "2025-02-01T00:00:00+00:00", doc))
    assert len(snaps) == 1
    assert [i.reason for i in issues] == ["duplicate_point"]


def test_meme_version_rescannee_est_un_point_distinct():
    """Exodus re-scanne une même version : le report_date change, le point aussi."""
    snaps, _ = build(
        rev("aaa", "2025-01-01T00:00:00+00:00",
            fiche("com.carrefour", "21.6", "2025-01-15T00:00:00Z", ["A"], [1])),
        rev("bbb", "2025-02-01T00:00:00+00:00",
            fiche("com.carrefour", "21.6", "2025-02-15T00:00:00Z", ["A", "B"], [1])),
    )
    assert len(snaps) == 2


def test_chainage_temporel():
    snaps, _ = build(
        rev("aaa", "2025-01-01T00:00:00+00:00",
            fiche("com.carrefour", "1.0", "2025-01-01T00:00:00Z", ["A"], [])),
        rev("bbb", "2025-02-01T00:00:00+00:00",
            fiche("com.carrefour", "1.1", "2025-02-01T00:00:00Z", ["A"], [])),
    )
    assert snaps[0].previous_observed_at is None
    assert snaps[1].previous_observed_at == snaps[0].observed_at


def test_provenance_et_source():
    """Chaque ligne doit rester vérifiable à la main contre le git du site."""
    snaps, _ = build(rev("abc123", "2025-01-01T00:00:00+00:00",
                         fiche("com.carrefour", "1.0", "2025-01-01T00:00:00Z", [], [])))
    assert snaps[0].source == "exodus-git"
    assert snaps[0].provenance_ref == "abc123"
    assert snaps[0].observed_at == "2025-01-01T00:00:00+00:00"


def test_catalog_version_reste_null():
    """Le catalogue en vigueur au scan Exodus est irrécupérable depuis git. Null vaut
    inconnu : c'est ce qui force le marquage catalog_unknown sur toutes les deltas."""
    snaps, _ = build(rev("aaa", "2025-01-01T00:00:00+00:00",
                         fiche("com.carrefour", "1.0", "2025-01-01T00:00:00Z", [], [1])))
    assert snaps[0].catalog_version is None


def test_trackers_nommes_et_permissions_triees():
    snaps, _ = build(rev("aaa", "2025-01-01T00:00:00+00:00",
                         fiche("com.carrefour", "1.0", "2025-01-01T00:00:00Z",
                               ["B", "A"], [2, 1, 1])))
    assert snaps[0].permissions == ["A", "B"]
    assert snaps[0].trackers == [{"id": 1, "name": "T1"}, {"id": 2, "name": "T2"}]


def test_tracker_hors_catalogue_garde_son_id():
    snaps, _ = build(rev("aaa", "2025-01-01T00:00:00+00:00",
                         fiche("com.carrefour", "1.0", "2025-01-01T00:00:00Z", [], [9999])))
    assert snaps[0].trackers == [{"id": 9999, "name": None}]


def test_defauts_portes_par_le_snapshot():
    """Les trous des fiches du site sont dans la donnée, pas perdus dans un log."""
    snaps, _ = build(rev("aaa", "2025-01-01T00:00:00+00:00",
                         fiche("com.carrefour", None, None, ["A"], [])))
    assert sorted(snaps[0].quality_flags) == ["empty_trackers", "missing_report_date",
                                              "missing_version_name"]
