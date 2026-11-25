"""La commande export de bout en bout.

La propriété qui compte : **la sortie ne quitte pas le dépôt privé par accident.**
Décision 12 — le dataset n'est pas publié, et `../reboot` est un dépôt public. Le reste
tient en deux points : un document de corpus absent arrête la commande au lieu de
produire des tranches vides, et un document inchangé n'est pas réécrit.
"""

import json

import pytest

from apklab import cli, corpus, export, store
from apklab.models import Snapshot


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "CORPUS_PATH", tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(cli, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(cli, "ENDPOINTS_PATH", tmp_path / "endpoints.json")
    monkeypatch.setattr(cli, "KEYS_PATH", tmp_path / "keys.json")
    monkeypatch.setattr(cli, "CANDIDATES_PATH", tmp_path / "candidates.json")
    monkeypatch.setattr(cli, "DIST_ROOT", tmp_path / "dist")

    corpus.write([corpus.CorpusEntry(slug="x", handle="com.app.x", app_name="X")],
                 tmp_path / "corpus.json")
    (tmp_path / "endpoints.json").write_text(json.dumps({"hosts": [
        {"host": "api.tiers.com", "apps": ["com.app.x", "com.app.y"], "app_count": 2,
         "class": "endpoint", "first_party": False},
        {"host": "api.autre.com", "apps": ["com.app.y"], "app_count": 1,
         "class": "endpoint", "first_party": False}]}), "utf-8")
    (tmp_path / "keys.json").write_text(json.dumps({"accounts": [
        {"id": "firebase-project", "apps": ["com.app.x"], "app_count": 1,
         "value": "projet-x", "secret": False, "shared": False}]}), "utf-8")
    (tmp_path / "candidates.json").write_text(json.dumps({"candidates": [
        {"package": "com.tiers.sdk", "status": "pending",
         "apps": [{"handle": "com.app.x", "class_count": 12}], "app_count": 1}]}),
        "utf-8")
    return tmp_path


def pose_snapshot(lab, version="1.0", observed_at="2026-09-10T00:00:00+00:00",
                  permissions=("android.permission.INTERNET",)):
    store.ingest(lab / "snapshots", [Snapshot(
        handle="com.app.x", slug="x", app_name="X", version_name=version,
        source="apk-static", acquisition="apkpure", observed_at=observed_at,
        permissions=list(permissions), trackers=[{"id": 1, "name": "Un pisteur"}],
        catalog_version="cat:1", size_scope="single-apk", total_bytes=1024)],
        write=True)


def run(lab, *args) -> int:
    return cli.main(["--site-repo", str(lab), "export", "--app", "x", *args])


def test_dry_run_n_ecrit_pas(lab, capsys):
    pose_snapshot(lab)
    assert run(lab) == 0
    assert not (lab / "dist").exists()
    assert "dry-run" in capsys.readouterr().err


def test_write_ecrit_le_document_et_le_manifeste(lab, capsys):
    pose_snapshot(lab)
    assert run(lab, "--write") == 0

    doc = json.loads((lab / "dist" / "apps" / "x.json").read_text("utf-8"))
    assert doc["handle"] == "com.app.x"
    assert doc["latest"]["version_name"] == "1.0"
    assert [h["host"] for h in doc["endpoints"]] == ["api.tiers.com"]
    assert [a["id"] for a in doc["accounts"]] == ["firebase-project"]
    assert [c["package"] for c in doc["candidates"]] == ["com.tiers.sdk"]

    man = json.loads((lab / "dist" / "manifest.json").read_text("utf-8"))
    assert man["app_count"] == 1
    assert man["visibility"] == "private"
    assert man["apps"][0]["file"] == "apps/x.json"


def test_le_manifeste_porte_l_empreinte_des_documents_sources(lab):
    """C'est ce qui permet de savoir de quelle passe viennent les tranches."""
    pose_snapshot(lab)
    assert run(lab, "--write") == 0
    man = json.loads((lab / "dist" / "manifest.json").read_text("utf-8"))
    fichiers = {s["file"]: s for s in man["sources"]}
    assert set(fichiers) == {"endpoints.json", "keys.json", "candidates.json"}
    assert all(s["present"] and len(s["sha256"]) == 64 for s in fichiers.values())


def test_une_sortie_hors_du_depot_est_refusee(lab, tmp_path, capsys):
    pose_snapshot(lab)
    dehors = tmp_path.parent / "reboot-public"
    assert run(lab, "--out", str(dehors), "--write") == 1
    assert "hors du dépôt privé" in capsys.readouterr().err
    assert not dehors.exists()


def test_allow_outside_est_la_seule_facon_d_en_sortir(lab, tmp_path):
    pose_snapshot(lab)
    dehors = tmp_path.parent / "reboot-public"
    assert run(lab, "--out", str(dehors), "--allow-outside", "--write") == 0
    assert (dehors / "apps" / "x.json").exists()


def test_un_document_de_corpus_absent_arrete_la_commande(lab, capsys):
    pose_snapshot(lab)
    (lab / "keys.json").unlink()
    assert run(lab, "--write") == 1
    err = capsys.readouterr().err
    assert "keys.json" in err and "jointure" in err
    assert not (lab / "dist").exists()


def test_un_document_inchange_n_est_pas_reecrit(lab, capsys):
    pose_snapshot(lab)
    assert run(lab, "--write") == 0
    avant = (lab / "dist" / "apps" / "x.json").stat().st_mtime_ns
    assert run(lab, "--write") == 0
    après = (lab / "dist" / "apps" / "x.json").stat().st_mtime_ns
    assert avant == après
    assert "1 inchangé(s)" in capsys.readouterr().err


def test_une_permission_de_plus_rend_le_document_a_ecrire(lab, capsys):
    pose_snapshot(lab)
    assert run(lab, "--write") == 0
    pose_snapshot(lab, version="2.0", observed_at="2026-09-11T00:00:00+00:00",
                  permissions=["android.permission.INTERNET",
                               "android.permission.CAMERA"])
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "dist" / "apps" / "x.json").read_text("utf-8"))
    assert doc["counts"]["snapshots"] == 2
    assert doc["counts"]["deltas"] == 1
    assert doc["deltas"][0]["permissions"]["added"] == ["android.permission.CAMERA"]


def test_une_application_sans_snapshot_est_declaree_pas_omise(lab, capsys):
    """Un dataset qui la ferait disparaître mentirait sur la taille du corpus."""
    assert run(lab, "--write") == 0
    man = json.loads((lab / "dist" / "manifest.json").read_text("utf-8"))
    assert man["app_count"] == 0
    assert man["skipped"] == [{"slug": "x", "handle": "com.app.x",
                               "reason": "no_snapshot"}]
    assert not (lab / "dist" / "apps").exists()


def test_le_rapport_de_run_est_ecrit(lab):
    pose_snapshot(lab)
    assert run(lab, "--write") == 0
    rapports = list((lab / "runs").glob("*.json"))
    assert len(rapports) == 1
    rapport = json.loads(rapports[0].read_text("utf-8"))
    assert rapport["command"] == "export"
    assert rapport["totals"]["written"] == 1


def test_le_perimetre_est_toujours_explicite(lab, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--site-repo", str(lab), "export"])
    assert "--app" in capsys.readouterr().err


def test_le_manifeste_sort_sur_stdout(lab, capsys):
    pose_snapshot(lab)
    assert run(lab) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["dataset_version"] == export.DATASET_VERSION
    assert doc["apps"][0]["slug"] == "x"
