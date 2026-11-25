"""Les sous-commandes de lecture et d'écriture locale, de bout en bout.

Dette 5 du design : `cli.py` n'était testée que sur les commandes coûteuses. Celles
qu'on lance vingt fois par jour — `diff`, `show`, `store ingest`, `validate` — ne
l'étaient pas, alors que ce sont elles qui décident de ce qu'on croit lire.
"""

import json

import pytest

from apklab import cli, corpus, store
from apklab.corpus import CorpusEntry
from apklab.models import Snapshot

SLUG = "carrefour"
HANDLE = "com.carrefour.fid.android"


@pytest.fixture
def lab(tmp_path, monkeypatch):
    corpus.write([CorpusEntry(slug=SLUG, handle=HANDLE, app_name="Carrefour")],
                 tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "CORPUS_PATH", tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(cli, "DELTA_ROOT", tmp_path / "deltas")
    return tmp_path


def snapshot(version, when, permissions=("android.permission.INTERNET",),
             trackers=(12,), **kw):
    return Snapshot(handle=HANDLE, slug=SLUG, app_name="Carrefour",
                    version_name=version, source=kw.pop("source", "exodus-git"),
                    observed_at=when, report_date=when, permissions=list(permissions),
                    trackers=[{"id": i, "name": f"T{i}"} for i in trackers], **kw)


def pose(lab, *snaps):
    store.ingest(lab / "snapshots", list(snaps), write=True)


def deux_points(lab):
    pose(lab,
         snapshot("23.0.0", "2026-01-01T00:00:00+00:00"),
         snapshot("23.6.0", "2026-02-01T00:00:00+00:00",
                  permissions=["android.permission.INTERNET",
                               "android.permission.CAMERA"]))


def run(lab, *args) -> int:
    return cli.main(["--site-repo", str(lab), *args])


# --- périmètre, commun à toutes les commandes -------------------------------

def test_sans_perimetre_la_commande_refuse(lab, capsys):
    """Jamais tout le corpus par défaut : `--app` ou `--all`, explicitement."""
    with pytest.raises(SystemExit):
        run(lab, "diff")
    assert "préciser --app <slug> ou --all" in capsys.readouterr().err


def test_un_slug_inconnu_est_dit(lab, capsys):
    with pytest.raises(SystemExit):
        run(lab, "diff", "--app", "inexistante")
    assert "slug inconnu dans le corpus : inexistante" in capsys.readouterr().err


# --- diff --------------------------------------------------------------------

def test_diff_dry_run_sort_les_deltas_sans_ecrire(lab, capsys):
    deux_points(lab)
    assert run(lab, "diff", "--app", SLUG) == 0
    out = capsys.readouterr()
    delta = json.loads(out.out.strip())
    assert delta["permissions"]["added"] == ["android.permission.CAMERA"]
    assert not (lab / "deltas").exists()
    assert "dry-run" in out.err


def test_diff_write_ecrit_le_fichier_de_deltas(lab):
    deux_points(lab)
    assert run(lab, "diff", "--app", SLUG, "--write") == 0
    deltas = json.loads((lab / "deltas" / f"{HANDLE}.json").read_text("utf-8"))
    assert len(deltas) == 1 and deltas[0]["to_version"] == "23.6.0"


def test_diff_reecrit_le_fichier_en_entier(lab):
    """Un point inséré au milieu change les fenêtres de ses voisins : un fichier
    complété par la fin garderait les anciennes à côté des nouvelles."""
    deux_points(lab)
    run(lab, "diff", "--app", SLUG, "--write")
    pose(lab, snapshot("23.3.0", "2026-01-15T00:00:00+00:00",
                       permissions=["android.permission.INTERNET",
                                    "android.permission.CAMERA"]))
    run(lab, "diff", "--app", SLUG, "--write")

    deltas = json.loads((lab / "deltas" / f"{HANDLE}.json").read_text("utf-8"))
    assert [d["to_version"] for d in deltas] == ["23.3.0", "23.6.0"]


def test_diff_sur_une_serie_sans_delta_n_ecrit_aucun_fichier(lab):
    pose(lab, snapshot("23.0.0", "2026-01-01T00:00:00+00:00"))
    assert run(lab, "diff", "--app", SLUG, "--write") == 0
    assert not (lab / "deltas" / f"{HANDLE}.json").exists()


# --- store ingest -------------------------------------------------------------

def test_store_ingest_lit_un_fichier_et_ecrit(lab):
    source = lab / "entrée.jsonl"
    source.write_text(json.dumps(snapshot("23.0.0", "2026-01-01T00:00:00+00:00")
                                 .to_dict()) + "\n", "utf-8")
    assert run(lab, "store", "ingest", str(source), "--write") == 0
    assert len(store.read(lab / "snapshots", HANDLE)) == 1


def test_store_ingest_dry_run_n_ecrit_rien(lab, capsys):
    source = lab / "entrée.jsonl"
    source.write_text(json.dumps(snapshot("23.0.0", "2026-01-01T00:00:00+00:00")
                                 .to_dict()) + "\n", "utf-8")
    assert run(lab, "store", "ingest", str(source)) == 0
    assert store.read(lab / "snapshots", HANDLE) == []
    assert "dry-run" in capsys.readouterr().err


def test_store_ingest_refuse_un_point_sans_analyse_de_pisteurs(lab, capsys):
    """Le refus vaut aussi pour un JSONL passé à la main, pas seulement pour `fetch`."""
    source = lab / "entrée.jsonl"
    source.write_text(json.dumps(snapshot(
        "23.0.0", "2026-01-01T00:00:00+00:00", trackers=(), source="apk-static",
        quality_flags=["trackers_not_analyzed"]).to_dict()) + "\n", "utf-8")

    assert run(lab, "store", "ingest", str(source), "--write") == 0
    assert store.read(lab / "snapshots", HANDLE) == []
    assert json.loads(capsys.readouterr().err.splitlines()[0])["refused"] == 1


# --- show ---------------------------------------------------------------------

def test_show_rend_la_serie_et_ses_deltas(lab, capsys):
    deux_points(lab)
    assert run(lab, "show", "--app", SLUG) == 0
    out = capsys.readouterr().out
    assert "2 snapshot(s)" in out
    assert "23.6.0" in out and "CAMERA" in out


def test_show_sans_snapshot_le_dit(lab, capsys):
    assert run(lab, "show", "--app", SLUG) == 0
    assert "aucun snapshot" in capsys.readouterr().out


def test_show_all_donne_une_ligne_par_application(lab, capsys):
    deux_points(lab)
    assert run(lab, "show", "--all") == 0
    lignes = capsys.readouterr().out.splitlines()
    assert lignes[0].startswith("application")
    assert lignes[1].startswith(SLUG) and "23.6.0" in lignes[1]


# --- corpus et validate --------------------------------------------------------

def test_corpus_list_sort_le_corpus(lab, capsys):
    assert run(lab, "corpus", "list") == 0
    assert HANDLE in capsys.readouterr().out


def test_validate_accepte_une_serie_conforme(lab, capsys):
    deux_points(lab)
    assert run(lab, "validate") == 0
    assert "2 snapshot(s) validé(s), 0 erreur(s)" in capsys.readouterr().out


def test_validate_refuse_un_champ_hors_schema(lab, capsys):
    """La CI s'appuie sur ce code de retour : il doit être non nul, pas seulement bruyant."""
    deux_points(lab)
    série = lab / "snapshots" / f"{HANDLE}.jsonl"
    lignes = série.read_text("utf-8").splitlines()
    faux = {**json.loads(lignes[0]), "source": "inventée"}
    série.write_text("\n".join([json.dumps(faux), *lignes[1:]]) + "\n", "utf-8")

    assert run(lab, "validate") == 1
    assert "source" in capsys.readouterr().err


def test_validate_accepts_the_reference_size_fields(lab, capsys):
    """Declared or not, a strict schema refuses every new static snapshot."""
    parts = [{"name": "base.apk", "kind": "base", "qualifier": None, "bytes": 1000,
              "dex_bytes": 600, "res_bytes": 300, "assets_bytes": 0, "native_bytes": 0}]
    pose(lab,
         snapshot("23.0.0", "2026-01-01T00:00:00+00:00"),
         snapshot("23.6.0", "2026-02-01T00:00:00+00:00", source="apk-static",
                  size_parts=parts, native_bytes_by_abi={"arm64-v8a": 0},
                  reference_bytes=1000))
    assert run(lab, "validate") == 0, capsys.readouterr().err


def test_validate_refuses_a_part_of_an_unknown_kind(lab, capsys):
    parts = [{"name": "x.apk", "kind": "mystery", "qualifier": None, "bytes": 1,
              "dex_bytes": 0, "res_bytes": 0, "assets_bytes": 0, "native_bytes": 0}]
    pose(lab, snapshot("23.0.0", "2026-01-01T00:00:00+00:00", source="apk-static",
                       size_parts=parts))
    assert run(lab, "validate") == 1
