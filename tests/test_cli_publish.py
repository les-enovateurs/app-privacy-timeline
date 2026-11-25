"""La commande publish de bout en bout.

La propriété qui compte : **la projection n'entre pas dans un dépôt versionné.** C'est
la garde symétrique de celle d'`export`, et non la même. `export` refuse de sortir du
dépôt privé ; `publish` a vocation à en sortir — ce qu'il faut empêcher est son entrée
dans `../reboot`, où elle serait committée puis diffusée à chaque passe alors que le site
la reconstruit à chaque build depuis le dépôt de fichiers du serveur.

Le reste tient en deux points : une application dont il ne reste rien après réduction
n'est pas publiée, et un document inchangé n'est pas réécrit — sinon la porte
d'idempotence côté CI relancerait un build du site à chaque passe.
"""

import json

import pytest

from apklab import cli, corpus, publish, store
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
    monkeypatch.setattr(cli, "PUBLIC_ROOT", tmp_path / "dist-public")

    corpus.write([corpus.CorpusEntry(slug="x", handle="com.app.x", app_name="X")],
                 tmp_path / "corpus.json")
    (tmp_path / "endpoints.json").write_text(json.dumps({"hosts": [
        {"host": "api.tiers.com", "apps": ["com.app.x", "com.app.y"], "app_count": 2,
         "class": "endpoint", "first_party": False, "owner_packages": ["com.tiers.sdk"],
         "owner_package_count": 1},
        {"host": "www.w3.org", "apps": ["com.app.x"], "app_count": 1,
         "class": "doc", "first_party": False, "owner_packages": [],
         "owner_package_count": 0}]}), "utf-8")
    (tmp_path / "keys.json").write_text(json.dumps({"accounts": [
        {"id": "firebase", "apps": ["com.app.x"], "app_count": 1,
         "value_prefix": "mon-es", "value_sha256": "59dccc63a2d4",
         "secret": False, "shared": False}]}), "utf-8")
    (tmp_path / "candidates.json").write_text(json.dumps({"candidates": [
        {"package": "com.editeur.sdk", "status": "pending",
         "apps": [{"handle": "com.app.x", "class_count": 12}], "app_count": 1}]}),
        "utf-8")
    return tmp_path


def pose_snapshot(lab, version="1.0", observed_at="2026-09-10T00:00:00+00:00",
                  source="apk-static", permissions=("android.permission.INTERNET",)):
    store.ingest(lab / "snapshots", [Snapshot(
        handle="com.app.x", slug="x", app_name="X", version_name=version,
        source=source, acquisition="apkpure", observed_at=observed_at,
        permissions=list(permissions), trackers=[{"id": 1, "name": "Un pisteur"}],
        catalog_version="cat:1", size_scope="single-apk", total_bytes=1024)],
        write=True)


def run(lab, *args) -> int:
    return cli.main(["--site-repo", str(lab), "publish", "--app", "x", *args])


def lu(lab, nom="apps/x.json") -> dict:
    return json.loads((lab / "dist-public" / nom).read_text("utf-8"))


# ------------------------------------------------------------------- la garde git

def test_une_sortie_sous_un_depot_git_tiers_est_refusee(lab, tmp_path, capsys):
    """Écrire dans le dépôt du site mettrait la projection sous git, où elle serait
    committée puis diffusée à chaque passe."""
    site = tmp_path / "faux-site"
    (site / ".git").mkdir(parents=True)
    cible = site / "public" / "data" / "apk-lab"
    pose_snapshot(lab)
    assert run(lab, "--write", "--out", str(cible)) == 1
    assert "dépôt git tiers" in capsys.readouterr().err
    assert not cible.exists()


def test_un_repertoire_hors_git_est_accepte(lab, tmp_path):
    """La cible réelle : un dépôt de fichiers sur le serveur de déploiement."""
    pose_snapshot(lab)
    depot = tmp_path / "srv" / "apk-lab"
    assert run(lab, "--write", "--out", str(depot)) == 0
    assert (depot / "manifest.json").exists()


# ------------------------------------------------------------------ ce qui est écrit

def test_dry_run_n_ecrit_pas(lab, capsys):
    pose_snapshot(lab)
    assert run(lab) == 0
    assert not (lab / "dist-public").exists()
    assert "dry-run" in capsys.readouterr().err


def test_le_document_publie_porte_la_reduction_et_rien_d_autre(lab):
    pose_snapshot(lab)
    assert run(lab, "--write") == 0
    doc = lu(lab)
    assert doc["endpoints"]["third_party_count"] == 1
    assert [h["host"] for h in doc["endpoints"]["hosts"]] == ["api.tiers.com"]
    assert doc["endpoints"]["excluded_count"] == 1
    # Les comptes SDK par famille, jamais leur valeur ni son empreinte.
    assert doc["sdk_accounts"] == {"by_sdk": {"firebase": 1}}
    # Les candidats en compteurs, aucun nom de package.
    assert doc["tracker_candidates"] == {"examined": 1, "by_status": {"pending": 1}}
    texte = (lab / "dist-public" / "apps" / "x.json").read_text("utf-8")
    for interdit in ("com.editeur.sdk", "mon-es", "59dccc63a2d4", "www.w3.org"):
        assert interdit not in texte


def test_le_manifeste_indexe_et_empreinte_chaque_document(lab):
    pose_snapshot(lab)
    run(lab, "--write")
    m = json.loads((lab / "dist-public" / "manifest.json").read_text("utf-8"))
    assert m["app_count"] == 1
    assert m["visibility"] == "public"
    assert m["catalog_version"] == "cat:1"
    import hashlib
    attendu = hashlib.sha256(
        (lab / "dist-public" / "apps" / "x.json").read_bytes()).hexdigest()
    assert m["apps"][0]["sha256"] == attendu


def test_une_application_sans_rien_a_dire_n_est_pas_publiee(lab, capsys):
    """113 des 130 applications du corpus n'ont qu'un rapport Exodus derrière elles,
    dont le site porte déjà tout le contenu dans sa fiche. Publier un document vide
    ferait croire à une analyse qui n'existe pas."""
    monkey = lab / "endpoints.json"
    monkey.write_text(json.dumps({"hosts": []}), "utf-8")
    pose_snapshot(lab, source="exodus-web")
    assert run(lab, "--write") == 0
    assert not (lab / "dist-public" / "apps" / "x.json").exists()
    m = json.loads((lab / "dist-public" / "manifest.json").read_text("utf-8"))
    assert m["app_count"] == 0
    assert "sans rien à dire" in capsys.readouterr().err


def test_un_document_inchange_n_est_pas_reecrit(lab, capsys):
    """La porte d'idempotence côté CI compare le manifeste distant au manifeste local :
    si une passe sans changement réécrivait les fichiers, elle relancerait un build du
    site à chaque tour."""
    pose_snapshot(lab)
    run(lab, "--write")
    capsys.readouterr()
    run(lab, "--write")
    assert "1 inchangé(s)" in capsys.readouterr().err


def test_un_document_de_corpus_absent_arrete_la_commande(lab, capsys):
    """Une tranche vide se lirait « cette application ne parle à personne »."""
    (lab / "endpoints.json").unlink()
    pose_snapshot(lab)
    assert run(lab, "--write") == 1
    assert "endpoints.json" in capsys.readouterr().err
