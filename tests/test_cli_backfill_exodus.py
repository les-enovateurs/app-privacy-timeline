"""La commande backfill-exodus de bout en bout, sans réseau.

Le client HTTP est remplacé par un faux qui sert des pages réelles retouchées. Ce qui est
vérifié ici n'est pas le parsing — test_backfill_exodus s'en charge — mais l'orchestration :
pagination, dédup, cache, écriture, et surtout le fait qu'un second run ne redemande rien.
"""

import json
from pathlib import Path

import pytest

from apklab import cli, corpus, exodusweb
from apklab.corpus import CorpusEntry

FIXTURES = Path(__file__).parent / "fixtures" / "exodus"
HANDLE = "com.facebook.katana"
DETAIL = (FIXTURES / "report_775051.html").read_text("utf-8")


def detail_for(report_id: int, version: str, created: str) -> str:
    """Le rapport réel, redaté et reversionné. Une version par point de mesure."""
    return (DETAIL
            .replace("576.0.0.42.73", version)
            .replace("31 août 2026 07:31", created))


def list_page(refs: list[tuple[int, str]], next_page: int | None) -> str:
    blocks = "".join(
        f'<a href="/fr/reports/{report_id}/" class="stretched-link report-link"></a>'
        f'<div class="small"><b>Version {version} - google</b></div>'
        for report_id, version in refs)
    suivant = f'<a class="page-link" href="?page={next_page}">Suivant</a>' if next_page else ""
    return blocks + suivant


# Deux pages, du plus récent au plus ancien comme chez Exodus, avec un re-scan de la
# version 2.0 (rapports 30 et 20) que la dédup doit ramener à un seul détail.
PAGES = {
    1: list_page([(30, "2.0"), (20, "2.0"), (10, "1.0")], next_page=2),
    2: list_page([(5, "0.9")], next_page=None),
}
DETAILS = {
    20: detail_for(20, "2.0", "1 mars 2026 10:00"),
    10: detail_for(10, "1.0", "1 février 2026 10:00"),
    5: detail_for(5, "0.9", "1 janvier 2026 10:00"),
}


class FakeClient:
    """Compte ses appels. Un id absent de DETAILS est une erreur du test, pas du code."""

    instances: list["FakeClient"] = []

    def __init__(self, cache_dir, delay=0.0):
        self.cache_dir = cache_dir
        self.stats = {"requests": 0, "cached": 0, "retries": 0}
        self.fetched: list[int] = []
        self.pages: list[int] = []
        FakeClient.instances.append(self)

    def list_page(self, handle, page=1):
        assert handle == HANDLE
        self.pages.append(page)
        self.stats["requests"] += 1
        return PAGES[page]

    def report(self, report_id):
        self.fetched.append(report_id)
        self.stats["requests"] += 1
        return DETAILS[report_id]


@pytest.fixture
def lab(tmp_path, site, monkeypatch):
    """Un dépôt de travail jetable : corpus d'une application, store et runs vides."""
    corpus.write([CorpusEntry(slug="facebook", handle=HANDLE, app_name="Facebook")],
                 tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "CORPUS_PATH", tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(cli, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(cli, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(exodusweb, "Client", FakeClient)
    FakeClient.instances.clear()
    return tmp_path


def run(site, *args) -> int:
    return cli.main(["--site-repo", str(site), "backfill-exodus", "--app", "facebook", *args])


def series(lab) -> list[dict]:
    path = lab / "snapshots" / f"{HANDLE}.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_run_complet_suit_la_pagination_et_dedoublonne(lab, site, capsys):
    assert run(site, "--write") == 0
    client = FakeClient.instances[0]

    assert client.pages == [1, 2]
    # 30 et 20 portent la même version : un seul détail demandé, le plus ancien des deux.
    assert client.fetched == [5, 10, 20]


def test_snapshots_ecrits_dans_l_ordre_du_temps(lab, site, capsys):
    run(site, "--write")
    rows = series(lab)
    assert [r["version_name"] for r in rows] == ["0.9", "1.0", "2.0"]
    assert [r["provenance_ref"] for r in rows] == ["5", "10", "20"]
    assert all(r["source"] == "exodus-web" for r in rows)
    assert rows[0]["previous_observed_at"] is None
    assert rows[2]["previous_observed_at"] == rows[1]["observed_at"]


def test_second_run_ne_redemande_aucun_rapport(lab, site, capsys):
    """Un rapport déjà dans la série n'est pas redemandé, même cache vidé : le backfill
    se relance sur une autre machine sans repayer 300 requêtes."""
    run(site, "--write")
    run(site, "--write")

    assert FakeClient.instances[1].fetched == []
    assert len(series(lab)) == 3


def test_dry_run_n_ecrit_ni_snapshot_ni_rapport_de_run(lab, site, capsys):
    assert run(site) == 0
    assert not (lab / "snapshots").exists()
    assert not (lab / "runs").exists()
    assert len(capsys.readouterr().out.strip().splitlines()) == 3


def test_rapport_de_run_liste_les_doublons_ecartes(lab, site, capsys):
    run(site, "--write")
    report = json.loads(next((lab / "runs").glob("*.json")).read_text("utf-8"))
    app = report["apps"][0]

    assert app["status"] == "ok"
    assert app["snapshots"] == 3
    assert [r["reason"] for r in app["rejected"]] == ["duplicate_version"]
    assert report["totals"]["http"]["requests"] == 5


def test_max_versions_prend_les_plus_recentes(lab, site, capsys):
    """Un run volontairement partiel doit ramener le présent ; le passé se complète au
    run suivant."""
    run(site, "--write", "--max-versions", "1")
    assert FakeClient.instances[0].fetched == [20]
    assert [r["version_name"] for r in series(lab)] == ["2.0"]
