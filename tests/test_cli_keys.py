"""La commande keys de bout en bout.

La propriété qui compte : la commande ne voit jamais une clé entière. Les preuves la
portent déjà tronquée, et l'agrégat ne fait que compter les applications — ce qui est
justement ce qui permet de dire qu'un identifiant partagé n'est pas un compte d'éditeur.
"""

import json

import pytest

from apklab import cli, evidence, keys


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(cli, "KEYS_PATH", tmp_path / "keys.json")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    return tmp_path


def pose_preuve(lab, handle, pairs, version="1.0"):
    evidence.write(lab / "evidence", evidence.build(
        handle=handle, version_name=version, apk_sha256="ab",
        observed_at="2026-09-10T00:00:00+00:00", analyzer_version="androguard 4.1.4",
        class_names=["com.tiers.sdk.Classe"], domains=[],
        credentials=keys.scan(pairs)))


def run(lab, *args) -> int:
    return cli.main(["--site-repo", str(lab), "keys", *args])


def test_sans_preuve_la_commande_le_dit(lab, capsys):
    assert run(lab) == 1
    assert "aucune preuve" in capsys.readouterr().err


def test_dry_run_n_ecrit_pas(lab, capsys):
    pose_preuve(lab, "com.app.x", [("resource-string", "project_id", "projet-x")])
    assert run(lab) == 0
    assert not (lab / "keys.json").exists()
    assert "dry-run" in capsys.readouterr().err


def test_un_secret_ne_sort_jamais_entier(lab, capsys):
    pose_preuve(lab, "com.app.x", [("manifest-meta-data",
                                    "com.google.android.geo.API_KEY",
                                    "AIzaSy" + "z" * 33)])
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "keys.json").read_text("utf-8"))
    compte = doc["accounts"][0]
    assert "value" not in compte
    assert compte["value_prefix"] == "AIzaSy"
    assert "AIzaSy" + "z" * 33 not in (lab / "keys.json").read_text("utf-8")


def test_un_identifiant_partage_est_marque(lab, capsys):
    """Deux éditeurs sans rapport ne partagent pas un compte : c'est la valeur par
    défaut d'une bibliothèque, et elle ne dit rien de l'éditeur."""
    pairs = [("resource-string", "gcm_defaultSenderId", "123456789012")]
    pose_preuve(lab, "com.app.x", pairs)
    pose_preuve(lab, "com.app.y", pairs)
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "keys.json").read_text("utf-8"))
    compte = doc["accounts"][0]
    assert compte["app_count"] == 2
    assert compte["apps"] == ["com.app.x", "com.app.y"]
    assert compte["shared"] is True
    assert "1 partagé(s)" in capsys.readouterr().err


def test_un_identifiant_d_une_seule_application_n_est_pas_partage(lab, capsys):
    pose_preuve(lab, "com.app.x", [("resource-string", "project_id", "projet-x")])
    pose_preuve(lab, "com.app.y", [("resource-string", "project_id", "projet-y")])
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "keys.json").read_text("utf-8"))
    assert [c["shared"] for c in doc["accounts"]] == [False, False]
    assert {c["value"] for c in doc["accounts"]} == {"projet-x", "projet-y"}


def test_only_shared_filtre_la_sortie(lab, capsys):
    partage = [("resource-string", "gcm_defaultSenderId", "123456789012")]
    pose_preuve(lab, "com.app.x", partage + [("resource-string", "project_id", "px")])
    pose_preuve(lab, "com.app.y", partage)
    assert run(lab, "--only-shared", "--write") == 0
    doc = json.loads((lab / "keys.json").read_text("utf-8"))
    assert [c["id"] for c in doc["accounts"]] == ["firebase-sender"]


def test_une_reference_de_ressource_n_est_pas_un_compte(lab, capsys):
    """Deliveroo rangeait sa clé Maps derrière `@7F140385` : la valeur du manifeste est
    une référence non résolue, et l'annoncer comme clé serait affirmer un compte
    qu'on n'a jamais lu."""
    pose_preuve(lab, "com.app.x", [("manifest-meta-data",
                                    "com.google.android.geo.API_KEY", "@7F140385")])
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "keys.json").read_text("utf-8"))
    assert doc["accounts"] == []
