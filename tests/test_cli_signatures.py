"""`signatures fetch` : figer le catalogue dans le dépôt, sans réseau dans les tests."""

import json

import pytest

from apklab import cli, exodusweb, signatures

CATALOGUE = {"trackers": {
    "1": {"id": 1, "name": "Teemo", "code_signature": "com.databerries."},
    "2": {"id": 2, "name": "Réseau seul", "code_signature": ""},
}}


class FakeClient:
    def __init__(self, cache_dir, delay=0.0):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return json.dumps(CATALOGUE)


class BrokenClient(FakeClient):
    def get(self, url):
        raise exodusweb.HttpError("HTTP 429 sur " + url)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "SIGNATURES_PATH", tmp_path / "signatures" / "trackers.json")
    monkeypatch.setattr(cli, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(exodusweb, "Client", FakeClient)
    return tmp_path


def test_dry_run_annonce_sans_ecrire(lab, capsys):
    assert cli.main(["signatures", "fetch"]) == 0
    assert "1 pisteur(s) avec signature de code" in capsys.readouterr().out
    assert not (lab / "signatures" / "trackers.json").exists()


def test_write_fige_la_liste_et_sa_version(lab, capsys):
    """Un nombre de pisteurs ne veut rien dire sans le catalogue qui l'a produit : les
    deux sont écrits dans le même fichier."""
    assert cli.main(["signatures", "fetch", "--write"]) == 0
    doc = json.loads((lab / "signatures" / "trackers.json").read_text("utf-8"))

    assert doc["trackers"]["1"]["name"] == "Teemo"
    assert doc["catalog_version"].endswith(f"/2/sha256:{doc['catalog_version'][-12:]}")

    trackers, version = signatures.load(lab / "signatures" / "trackers.json")
    assert [t.id for t in trackers] == [1]
    assert version == doc["catalog_version"]


def test_erreur_reseau_ne_laisse_pas_de_catalogue_partiel(lab, monkeypatch, capsys):
    monkeypatch.setattr(exodusweb, "Client", BrokenClient)
    assert cli.main(["signatures", "fetch", "--write"]) == 1
    assert not (lab / "signatures" / "trackers.json").exists()


def test_url_interrogee(lab, capsys):
    cli.main(["signatures", "fetch"])
    assert signatures.CATALOG_URL.endswith("/api/trackers")
