"""Les trois règles d'ingestion, l'insertion hors ordre et le ré-chaînage."""

import pytest

from apklab import store
from apklab.models import Snapshot


def snap(when, perms=("A",), version="1.0", source="exodus-git", quality_flags=()):
    return Snapshot(handle="x", slug="x", app_name="X", version_name=version,
                    observed_at=when, permissions=list(perms), source=source,
                    quality_flags=list(quality_flags))


@pytest.fixture
def root(tmp_path):
    return tmp_path / "snapshots"


def test_serie_vide_puis_ecriture(root):
    assert store.read(root, "x") == []
    stats = store.ingest(root, [snap("2026-01-01T00:00:00+00:00")], write=True)
    assert stats["appended"] == 1
    assert len(store.read(root, "x")) == 1


def test_dry_run_necrit_rien(root):
    store.ingest(root, [snap("2026-01-01T00:00:00+00:00")], write=False)
    assert not root.exists()


def test_rejeu_exact_ignore(root):
    """Rend backfill-git --all --write relançable sans dupliquer la série."""
    serie = [snap("2026-01-01T00:00:00+00:00"), snap("2026-02-01T00:00:00+00:00", ["A", "B"])]
    store.ingest(root, serie, write=True)
    stats = store.ingest(root, serie, write=True)
    assert stats == {"appended": 0, "replayed": 2, "unchanged": 0, "refused": 0,
                     "handles": 0}
    assert len(store.read(root, "x")) == 2


def test_empreinte_inchangee_ignoree(root):
    """Décision 3 : un snapshot n'est écrit que si l'empreinte change."""
    store.ingest(root, [snap("2026-01-01T00:00:00+00:00")], write=True)
    stats = store.ingest(root, [snap("2026-02-01T00:00:00+00:00")], write=True)
    assert stats["unchanged"] == 1
    assert len(store.read(root, "x")) == 1


def test_permission_retiree_puis_remise_garde_trois_points(root):
    """La dédup porte sur le dernier point, pas sur tout le fichier : sinon le retour
    en arrière disparaîtrait de l'historique."""
    store.ingest(root, [snap("2026-01-01T00:00:00+00:00", ["A", "B"]),
                        snap("2026-02-01T00:00:00+00:00", ["A"]),
                        snap("2026-03-01T00:00:00+00:00", ["A", "B"])], write=True)
    rows = store.read(root, "x")
    assert [len(r["permissions"]) for r in rows] == [2, 1, 2]


def test_permission_remise_lors_d_un_run_ulterieur(root):
    """Le cas réel : trois runs espacés de plusieurs mois, pas une seule ingestion.
    Le 3e run réécrit une empreinte déjà présente *plus haut* dans le fichier ; elle
    doit quand même être ajoutée, sinon le retour en arrière est invisible."""
    store.ingest(root, [snap("2026-01-01T00:00:00+00:00", ["A", "B"])], write=True)
    store.ingest(root, [snap("2026-02-01T00:00:00+00:00", ["A"])], write=True)
    stats = store.ingest(root, [snap("2026-03-01T00:00:00+00:00", ["A", "B"])], write=True)

    assert stats["appended"] == 1
    rows = store.read(root, "x")
    assert [len(r["permissions"]) for r in rows] == [2, 1, 2]
    assert rows[0]["fingerprint"] == rows[2]["fingerprint"]


def test_rechainage_a_l_ecriture(root):
    """Seul le store sait ce qui a fini dans la série ; un chaînage calculé en amont
    pointerait sur un snapshot que la dédup vient d'écarter."""
    store.ingest(root, [snap("2026-01-01T00:00:00+00:00", ["A"]),
                        snap("2026-02-01T00:00:00+00:00", ["A", "B"])], write=True)
    rows = store.read(root, "x")
    assert rows[0]["previous_observed_at"] is None
    assert rows[1]["previous_observed_at"] == rows[0]["observed_at"]


def test_rechainage_saute_les_snapshots_ecartes(root):
    """Trois entrées, celle du milieu inchangée : le 3e point chaîne sur le 1er."""
    store.ingest(root, [snap("2026-01-01T00:00:00+00:00", ["A"]),
                        snap("2026-02-01T00:00:00+00:00", ["A"]),
                        snap("2026-03-01T00:00:00+00:00", ["A", "B"])], write=True)
    rows = store.read(root, "x")
    assert len(rows) == 2
    assert rows[1]["previous_observed_at"] == "2026-01-01T00:00:00+00:00"


def test_un_fichier_par_handle(root):
    autre = Snapshot(handle="y", slug="y", app_name="Y", version_name="1.0",
                     observed_at="2026-01-01T00:00:00+00:00")
    store.ingest(root, [snap("2026-01-01T00:00:00+00:00"), autre], write=True)
    assert store.path_for(root, "x").exists()
    assert store.path_for(root, "y").exists()


def test_meme_empreinte_source_differente_est_un_point_distinct(root):
    """exodus-git et apk-static ne portent pas la même confiance ; l'un ne masque pas
    l'autre même à empreinte égale."""
    store.ingest(root, [snap("2026-01-01T00:00:00+00:00", source="exodus-git")], write=True)
    stats = store.ingest(root, [snap("2026-02-01T00:00:00+00:00", source="apk-static")],
                         write=True)
    assert stats["appended"] == 1


# ------------------------------------------------------- insertion hors ordre

def test_snapshot_anterieur_se_range_a_sa_place(root):
    """Le backfill Exodus découvre un rapport de 2019 après un point déjà écrit. Il ne
    peut pas finir en fin de fichier : la série cesserait d'être une série."""
    store.ingest(root, [snap("2026-02-01T00:00:00+00:00", ["A", "B"])], write=True)
    stats = store.ingest(root, [snap("2019-01-01T00:00:00+00:00", ["A"])], write=True)

    assert stats["appended"] == 1
    rows = store.read(root, "x")
    assert [r["observed_at"] for r in rows] == ["2019-01-01T00:00:00+00:00",
                                                "2026-02-01T00:00:00+00:00"]


def test_insertion_rechaine_le_successeur(root):
    """Sans recalcul, le point de 2026 resterait chaîné sur rien et sa fenêtre
    d'incertitude s'étendrait sur sept ans de plus que ce que la donnée permet."""
    store.ingest(root, [snap("2026-02-01T00:00:00+00:00", ["A", "B"])], write=True)
    store.ingest(root, [snap("2019-01-01T00:00:00+00:00", ["A"])], write=True)

    rows = store.read(root, "x")
    assert rows[0]["previous_observed_at"] is None
    assert rows[1]["previous_observed_at"] == "2019-01-01T00:00:00+00:00"


def test_insertion_ne_supprime_jamais_une_ligne_deja_ecrite(root):
    """La ligne existante devient redondante mais elle a une provenance propre et des
    deltas déjà publiés s'y adossent."""
    store.ingest(root, [snap("2026-02-01T00:00:00+00:00", ["A"], source="exodus-git")],
                 write=True)
    store.ingest(root, [snap("2019-01-01T00:00:00+00:00", ["A"], source="exodus-git")],
                 write=True)
    assert len(store.read(root, "x")) == 2


def test_inchange_se_juge_sur_le_precedent_chronologique(root):
    """Un rapport plus ancien que le dernier point, mais identique à celui qui le précède
    dans le temps, n'apporte rien."""
    store.ingest(root, [snap("2019-01-01T00:00:00+00:00", ["A"]),
                        snap("2026-01-01T00:00:00+00:00", ["A", "B"])], write=True)
    stats = store.ingest(root, [snap("2020-01-01T00:00:00+00:00", ["A"])], write=True)

    assert stats["unchanged"] == 1
    assert len(store.read(root, "x")) == 2


def test_tri_temporel_et_non_alphabetique(root):
    """Les observed_at ne portent pas tous le même fuseau : heure du committeur pour
    exodus-git, UTC pour exodus-web. 2026-03-01T01:00+02:00 suit 2026-02-28T23:30+00:00
    dans l'ordre des chaînes alors qu'il le précède dans le temps — il vaut 23:00 UTC."""
    store.ingest(root, [snap("2026-03-01T01:00:00+02:00", ["A"], source="exodus-git")],
                 write=True)
    store.ingest(root, [snap("2026-02-28T23:30:00+00:00", ["A", "B"], source="exodus-web")],
                 write=True)

    rows = store.read(root, "x")
    assert [r["source"] for r in rows] == ["exodus-git", "exodus-web"]


def test_lot_hors_ordre_est_trie_avant_ingestion(root):
    """Les rapports Exodus arrivent dans l'ordre des ids, pas des dates de scan."""
    stats = store.ingest(root, [snap("2026-03-01T00:00:00+00:00", ["A", "B"]),
                                snap("2026-01-01T00:00:00+00:00", ["A"])], write=True)
    assert stats["appended"] == 2
    rows = store.read(root, "x")
    assert [r["observed_at"] for r in rows] == ["2026-01-01T00:00:00+00:00",
                                                "2026-03-01T00:00:00+00:00"]


def test_un_snapshot_sans_analyse_de_pisteurs_n_entre_pas_dans_la_serie(root):
    """Sa liste vide veut dire « pas cherché ». Rangée entre deux points qui, eux, ont
    cherché, elle produit une oscillation −8 puis +8 que rien ne distingue d'un vrai
    retrait — et le store étant append-only, elle y resterait pour toujours."""
    refusé = snap("2026-02-01T00:00:00+00:00", version="2.0",
                  quality_flags=["trackers_not_analyzed"])
    stats = store.ingest(root, [snap("2026-01-01T00:00:00+00:00"), refusé], write=True)

    assert stats["refused"] == 1 and stats["appended"] == 1
    assert [row["version_name"] for row in store.read(root, "x")] == ["1.0"]
