"""Le rapport de run : aucune panne silencieuse, y compris la sienne."""

import json

from apklab import runreport

TOTALS = {"apps": 1, "snapshots": 0, "written": 0, "skipped": 0, "errors": 0}


def test_dry_run_n_ecrit_pas_de_rapport(tmp_path):
    runs = tmp_path / "runs"
    assert runreport.finish(runreport.new("run", {}), TOTALS, runs, False) is None
    assert not runs.exists()


def test_deux_runs_dans_la_meme_seconde_ne_se_recouvrent_pas(tmp_path):
    """Le nom du fichier est horodaté à la seconde. Un `run` qui ne trouve rien à faire
    dure moins que ça, et un rapport écrasé effacerait le statut d'applications déjà
    traitées — la panne silencieuse que le projet s'interdit."""
    premier = runreport.new("run", {"app": "carrefour"})
    second = dict(premier, args={"app": "fnac"}, started_at=premier["started_at"])

    a = runreport.finish(premier, TOTALS, tmp_path, True)
    b = runreport.finish(second, TOTALS, tmp_path, True)

    assert a != b
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert json.loads(a.read_text("utf-8"))["args"]["app"] == "carrefour"
    assert json.loads(b.read_text("utf-8"))["args"]["app"] == "fnac"


def test_le_rapport_porte_un_statut_par_application(tmp_path):
    report = runreport.new("run", {})
    runreport.add_app(report, "carrefour", "com.carrefour.fid.android", "ok", version="1")
    runreport.add_app(report, "fnac", "com.fnac.android", "not_listed")

    path = runreport.finish(report, TOTALS, tmp_path, True)
    apps = json.loads(path.read_text("utf-8"))["apps"]

    assert [a["status"] for a in apps] == ["ok", "not_listed"]
    assert apps[0]["version"] == "1"
