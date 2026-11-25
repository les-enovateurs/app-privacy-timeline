"""La commande candidates de bout en bout, et l'écriture des preuves par analyze.

Deux propriétés se vérifient ici et nulle part ailleurs : `analyze --write` dépose bien
une preuve à côté du snapshot — sans elle, `candidates` n'a rien à lire — et
`candidates` refuse de tourner sans catalogue, où sa sortie ne serait que les 400
pisteurs connus.
"""

import json
from pathlib import Path

import pytest

from apklab import cli, corpus, evidence
from apklab.corpus import CorpusEntry

APK = str(Path(__file__).parent / "fixtures" / "apk"
          / "org.fdroid.fdroid.privileged_2130.apk")
HANDLE = "org.fdroid.fdroid.privileged"


@pytest.fixture
def lab(tmp_path, monkeypatch):
    corpus.write([CorpusEntry(slug="fdroid-privileged", handle=HANDLE,
                              app_name="F-Droid Privileged")], tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "CORPUS_PATH", tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(cli, "SIGNATURES_PATH", tmp_path / "signatures.json")
    monkeypatch.setattr(cli, "ALLOWLIST_PATH", tmp_path / "allowlist.txt")
    monkeypatch.setattr(cli, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(cli, "CANDIDATES_PATH", tmp_path / "candidates.json")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    (tmp_path / "signatures.json").write_text(json.dumps({
        "catalog_version": "2026-09-09/1/sha256:abc123abc123",
        "trackers": {"1": {"id": 1, "name": "Connu",
                           "code_signature": r"com\.connu\.sdk"}}}), "utf-8")
    return tmp_path


def pose_preuve(lab, handle, packages, domains=(), version="1.0"):
    evidence.write(lab / "evidence", evidence.build(
        handle=handle, version_name=version, apk_sha256="ab",
        observed_at="2026-09-10T00:00:00+00:00", analyzer_version="androguard 4.1.4",
        class_names=[f"{p}.Classe" for p in packages], domains=list(domains)))


def run(lab, *args) -> int:
    """Plancher de bruit et prévalence abaissés : ces preuves comptent en unités."""
    return cli.main(["--site-repo", str(lab), "candidates",
                     "--min-classes", "1", "--min-apps", "1", *args])


def test_sans_preuve_la_commande_le_dit(lab, capsys):
    assert run(lab) == 1
    assert "aucune preuve" in capsys.readouterr().err


def test_sans_catalogue_la_commande_refuse(lab, capsys):
    """Sans catalogue, les 400 pisteurs connus ressortiraient en candidats."""
    (lab / "signatures.json").unlink()
    pose_preuve(lab, "com.app.x", ["com.inconnu.sdk"])
    assert run(lab) == 1
    assert "catalogue de signatures absent" in capsys.readouterr().err


def test_dry_run_n_ecrit_pas(lab, capsys):
    pose_preuve(lab, "com.app.x", ["com.inconnu.sdk"])
    assert run(lab) == 0
    assert not (lab / "candidates.json").exists()
    assert "dry-run" in capsys.readouterr().err


def test_write_produit_un_document_trie_et_pending(lab, capsys):
    pose_preuve(lab, "com.app.x", ["com.inconnu.sdk", "com.connu.sdk", "androidx.core"],
                domains=["api.inconnu.com"])
    pose_preuve(lab, "com.app.y", ["com.inconnu.sdk"])
    (lab / "allowlist.txt").write_text("androidx\n", "utf-8")

    assert run(lab, "--write") == 0
    doc = json.loads((lab / "candidates.json").read_text("utf-8"))
    assert [c["package"] for c in doc["candidates"]] == ["com.inconnu.sdk"]
    candidate = doc["candidates"][0]
    assert candidate["status"] == "pending" and candidate["app_count"] == 2
    assert candidate["endpoints"] == ["api.inconnu.com"]
    assert doc["catalog_version"] == "2026-09-09/1/sha256:abc123abc123"


def test_une_seconde_passe_conserve_le_jugement(lab):
    pose_preuve(lab, "com.app.x", ["com.inconnu.sdk"])
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "candidates.json").read_text("utf-8"))
    doc["candidates"][0]["status"] = "rejected"
    doc["candidates"][0]["note"] = "vu à la main : lib de rendu"
    (lab / "candidates.json").write_text(json.dumps(doc), "utf-8")

    assert run(lab, "--write") == 0
    after = json.loads((lab / "candidates.json").read_text("utf-8"))["candidates"][0]
    assert after["status"] == "rejected" and after["note"] == "vu à la main : lib de rendu"


def test_la_prevalence_minimale_est_de_deux_par_defaut(lab, capsys):
    """2 036 des 2 448 candidats du corpus de onze applications étaient des singletons."""
    classes = [f"com.inconnu.sdk.C{n}" for n in range(9)]
    pose_preuve(lab, "com.app.x", classes)
    assert cli.main(["--site-repo", str(lab), "candidates"]) == 0
    assert json.loads(capsys.readouterr().out)["candidates"] == []

    pose_preuve(lab, "com.app.y", classes)
    assert cli.main(["--site-repo", str(lab), "candidates"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [c["package"] for c in doc["candidates"]] == ["com.inconnu.sdk"]


def test_le_plancher_de_bruit_est_actif_par_defaut(lab, capsys):
    """13 498 des 13 710 candidats bruts de Grindr tenaient en une classe."""
    pose_preuve(lab, "com.app.x", ["com.inconnu.sdk"])
    assert cli.main(["--site-repo", str(lab), "candidates"]) == 0
    assert json.loads(capsys.readouterr().out)["candidates"] == []


def test_min_apps_filtre(lab, capsys):
    pose_preuve(lab, "com.app.x", ["com.inconnu.sdk"])
    assert run(lab, "--min-apps", "2") == 0
    assert json.loads(capsys.readouterr().out)["candidates"] == []


def test_analyze_write_depose_une_preuve(lab, site, capsys):
    """Sans cette écriture, `candidates` n'aurait jamais rien à lire."""
    assert cli.main(["--site-repo", str(site), "analyze", APK,
                     "--slug", "fdroid-privileged", "--write"]) == 0
    path = lab / "evidence" / HANDLE / "0.2.13.json"
    doc = json.loads(path.read_text("utf-8"))
    assert doc["handle"] == HANDLE and doc["version_name"] == "0.2.13"
    assert any(p["package"].startswith("org.fdroid") for p in doc["packages"])
    assert "preuves :" in capsys.readouterr().err


def test_analyze_sans_write_ne_depose_rien(lab, site):
    """Une preuve sans point de série décrirait une version qu'on n'a pas retenue."""
    assert cli.main(["--site-repo", str(site), "analyze", APK,
                     "--slug", "fdroid-privileged"]) == 0
    assert not (lab / "evidence").exists()


def test_le_classement_masque_les_lots_signes(lab, capsys):
    """Le tri des 501 a signé 271 `rejected` ; les relire à chaque passe est du bruit."""
    pose_preuve(lab, "com.app.x", ["com.inconnu.sdk", "com.plomberie.sdk"])
    pose_preuve(lab, "com.app.y", ["com.inconnu.sdk", "com.plomberie.sdk"])
    assert run(lab, "--write") == 0
    capsys.readouterr()

    doc = json.loads((lab / "candidates.json").read_text("utf-8"))
    for candidat in doc["candidates"]:
        if candidat["package"] == "com.plomberie.sdk":
            candidat["status"] = "rejected"
    (lab / "candidates.json").write_text(json.dumps(doc, ensure_ascii=False), "utf-8")

    assert run(lab, "--par", "package") == 0
    err = capsys.readouterr().err
    assert "1 en pending, 1 masqué(s)" in err
    assert "com.inconnu.sdk" in err
    assert "com.plomberie.sdk" not in err


def test_status_all_montre_tout_le_classement(lab, capsys):
    """Le document reste complet : un rejected sorti du JSON reviendrait en pending."""
    pose_preuve(lab, "com.app.x", ["com.inconnu.sdk"])
    pose_preuve(lab, "com.app.y", ["com.inconnu.sdk"])
    assert run(lab, "--write") == 0
    doc = json.loads((lab / "candidates.json").read_text("utf-8"))
    doc["candidates"][0]["status"] = "rejected"
    (lab / "candidates.json").write_text(json.dumps(doc, ensure_ascii=False), "utf-8")
    capsys.readouterr()

    assert run(lab, "--status", "all", "--par", "package") == 0
    err = capsys.readouterr().err
    assert "com.inconnu.sdk" in err
    assert "masqué(s)" not in err


def test_le_classement_se_replie_sur_l_editeur(lab, capsys):
    """Un éditeur, une ligne — Stripe posait 202 sous-packages pour une seule question."""
    trois = ["com.editeur.sdk", "com.editeur.core", "com.editeur.internal"]
    pose_preuve(lab, "com.app.x", trois)
    pose_preuve(lab, "com.app.y", trois)
    assert run(lab) == 0
    err = capsys.readouterr().err
    assert "1 éditeur(s)" in err
    assert "com.editeur " in err and "3 pkg" in err
    assert "com.editeur.sdk" not in err
