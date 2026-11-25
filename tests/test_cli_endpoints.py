"""La commande endpoints de bout en bout.

Deux propriétés tiennent ici : la commande ne lit que les preuves déjà sur le disque —
donc ni réseau ni binaire, et une correction de la table de classement se relit en une
seconde — et le document de sortie garde les hôtes rejetés avec leur classe, pour que
le tri reste contestable.
"""

import json
from pathlib import Path

import pytest

from apklab import cli, evidence


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(cli, "HOST_CLASSES_PATH", tmp_path / "host-classes.txt")
    monkeypatch.setattr(cli, "ENDPOINTS_PATH", tmp_path / "endpoints.json")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    return tmp_path


def pose_preuve(lab, handle, domains, packages=("com.tiers.sdk",), version="1.0"):
    evidence.write(lab / "evidence", evidence.build(
        handle=handle, version_name=version, apk_sha256="ab",
        observed_at="2026-09-10T00:00:00+00:00", analyzer_version="androguard 4.1.4",
        class_names=[f"{p}.Classe" for p in packages], domains=list(domains)))


def run(lab, *args) -> int:
    return cli.main(["--site-repo", str(lab), "endpoints", *args])


def test_sans_preuve_la_commande_le_dit(lab, capsys):
    assert run(lab) == 1
    assert "aucune preuve" in capsys.readouterr().err


def test_dry_run_n_ecrit_pas(lab, capsys):
    pose_preuve(lab, "com.app.x", ["api.tiers.com"])
    assert run(lab) == 0
    assert not (lab / "endpoints.json").exists()
    assert "dry-run" in capsys.readouterr().err


def test_write_ecrit_le_document_complet(lab, capsys):
    pose_preuve(lab, "com.app.x", ["api.tiers.com", "user.id", "www.example.com"])
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "endpoints.json").read_text("utf-8"))
    assert doc["app_count"] == 1
    assert doc["counts"] == {"endpoint": 1, "doc": 0, "placeholder": 1, "code": 1,
                             "invalid": 0, "infra": 0}
    classes = {h["host"]: h["class"] for h in doc["hosts"]}
    assert classes == {"api.tiers.com": "endpoint", "user.id": "code",
                       "www.example.com": "placeholder"}


def test_le_classement_manuel_est_pris_en_compte(lab, capsys):
    """Sans la table, `adj.st` sortirait en `code` et Adjust quitterait le dataset."""
    (lab / "host-classes.txt").write_text("endpoint adj.st\ndoc schemas.tiers.com\n",
                                          "utf-8")
    pose_preuve(lab, "com.app.x", ["adj.st", "schemas.tiers.com"])
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "endpoints.json").read_text("utf-8"))
    assert {h["host"]: h["class"] for h in doc["hosts"]} == {
        "adj.st": "endpoint", "schemas.tiers.com": "doc"}
    assert "1 endpoint(s) tiers publiables" in capsys.readouterr().err


def test_only_filtre_le_document_sans_fausser_le_bilan(lab, capsys):
    """Le bilan porte sur tous les hôtes : filtrer la sortie ne doit pas cacher
    combien d'hôtes ont été écartés, sinon le tri se juge sur un dénominateur faux."""
    pose_preuve(lab, "com.app.x", ["api.tiers.com", "user.id"])
    assert run(lab, "--only", "code", "--write") == 0
    doc = json.loads((lab / "endpoints.json").read_text("utf-8"))
    assert [h["host"] for h in doc["hosts"]] == ["user.id"]
    assert doc["counts"]["endpoint"] == 1


def test_la_prevalence_compte_les_applications(lab, capsys):
    pose_preuve(lab, "com.app.x", ["api.tiers.com"])
    pose_preuve(lab, "com.app.y", ["api.tiers.com"])
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "endpoints.json").read_text("utf-8"))
    host = doc["hosts"][0]
    assert host["app_count"] == 2
    assert host["apps"] == ["com.app.x", "com.app.y"]
    assert host["owner_packages"] == ["com.tiers.sdk"]
