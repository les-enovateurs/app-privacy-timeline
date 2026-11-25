"""La commande analyze de bout en bout, sur le vrai APK de fixture.

Le point de contrôle du projet est ici : --compare confronte l'analyse au snapshot
Exodus de la même version. Ce que ces tests vérifient, c'est que la divergence est dite,
pas qu'elle est absente.
"""

import json
from pathlib import Path

import pytest

from apklab import cli, corpus, store
from apklab.corpus import CorpusEntry
from apklab.models import Snapshot

APK = str(Path(__file__).parent / "fixtures" / "apk"
          / "org.fdroid.fdroid.privileged_2130.apk")
HANDLE = "org.fdroid.fdroid.privileged"
PERMISSIONS = ["android.permission.DELETE_PACKAGES", "android.permission.INSTALL_PACKAGES"]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    corpus.write([CorpusEntry(slug="fdroid-privileged", handle=HANDLE,
                              app_name="F-Droid Privileged")], tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "CORPUS_PATH", tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    # Les preuves suivent le snapshot : sans cette ligne, un test qui écrit dépose un
    # fichier dans le vrai `evidence/` du dépôt et fausse la prochaine passe de
    # `candidates`. C'est arrivé le 2026-09-10.
    monkeypatch.setattr(cli, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(cli, "RUNS_ROOT", tmp_path / "runs")
    monkeypatch.setattr(cli, "SIGNATURES_PATH", tmp_path / "signatures.json")
    return tmp_path


def write_signatures(lab, code_signature="android.content.pm.IPackage"):
    (lab / "signatures.json").write_text(json.dumps({
        "catalog_version": "2026-09-09/1/sha256:abc123abc123",
        "trackers": {"1": {"id": 1, "name": "T1", "code_signature": code_signature}},
    }), "utf-8")


def run(site, *args) -> int:
    return cli.main(["--site-repo", str(site), "analyze", APK,
                     "--slug", "fdroid-privileged", *args])


def seed_exodus(lab, version="0.2.13", permissions=PERMISSIONS):
    store.ingest(lab / "snapshots", [Snapshot(
        handle=HANDLE, slug="fdroid-privileged", app_name="F-Droid Privileged",
        version_name=version, source="exodus-web", analyzer="exodus",
        observed_at="2026-01-01T00:00:00+00:00", report_date="2026-01-01T00:00:00+00:00",
        permissions=permissions, trackers=[], provenance_ref="123456")], write=True)


def test_sans_catalogue_la_commande_refuse(lab, site, capsys):
    """Une liste de pisteurs vide voudrait dire « pas cherché » et serait lue comme
    « pas trouvé »."""
    assert run(site) == 1
    assert "catalogue de signatures absent" in capsys.readouterr().err


def test_sans_catalogue_mais_explicitement_le_snapshot_est_marque(lab, site, capsys):
    assert run(site, "--without-signatures") == 0
    snap = json.loads(capsys.readouterr().out.strip())
    assert "trackers_not_analyzed" in snap["quality_flags"]
    assert snap["catalog_version"] is None


def test_analyse_complete(lab, site, capsys):
    write_signatures(lab)
    assert run(site) == 0
    snap = json.loads(capsys.readouterr().out.strip())

    assert snap["source"] == "apk-static"
    assert snap["version_name"] == "0.2.13"
    assert snap["permissions"] == PERMISSIONS
    assert snap["trackers"] == [{"id": 1, "name": "T1"}]
    assert snap["catalog_version"] == "2026-09-09/1/sha256:abc123abc123"
    assert snap["total_bytes"] == 45943
    assert snap["dex_method_count"] == 185


def test_dry_run_n_ecrit_pas(lab, site, capsys):
    write_signatures(lab)
    run(site)
    assert not (lab / "snapshots").exists()


def test_write_ingere_le_snapshot(lab, site, capsys):
    write_signatures(lab)
    run(site, "--write")
    rows = store.read(lab / "snapshots", HANDLE)
    assert len(rows) == 1
    assert rows[0]["source"] == "apk-static"


def test_comparaison_sans_reference_le_dit(lab, site, capsys):
    write_signatures(lab)
    run(site, "--compare")
    assert "comparaison impossible" in capsys.readouterr().err


def test_comparaison_permissions_identiques(lab, site, capsys):
    """Le critère du point de contrôle : les permissions sont lues du manifeste des deux
    côtés, elles doivent coïncider."""
    write_signatures(lab)
    seed_exodus(lab)
    run(site, "--compare")
    out = capsys.readouterr().out
    assert "comparaison avec exodus-web (123456)" in out
    assert "permissions identiques" in out


def test_comparaison_permissions_divergentes(lab, site, capsys):
    """Une divergence signifie que l'analyseur est faux. Elle doit sauter aux yeux, pas
    se lire dans un JSON de 5 Ko."""
    write_signatures(lab)
    seed_exodus(lab, permissions=[*PERMISSIONS, "android.permission.RECORD_AUDIO"])
    run(site, "--compare")
    out = capsys.readouterr().out
    assert "DIVERGENTES" in out
    assert "RECORD_AUDIO" in out


def test_xapk_depose_a_la_main_est_fusionne(lab, site, capsys, xapk_deux_parts,
                                             monkeypatch):
    """APKPure ne sert que du XAPK, même pour une calculatrice de 7 Mo : un binaire
    déposé à la main en est un aussi. `analyze` le fusionne au lieu de refuser."""
    monkeypatch.setattr(cli, "CACHE_ROOT", lab / "cache")
    write_signatures(lab)
    assert cli.main(["--site-repo", str(site), "analyze", str(xapk_deux_parts),
                     "--slug", "fdroid-privileged"]) == 0
    snap = json.loads(capsys.readouterr().out.strip())

    assert snap["size_scope"] == "merged-xapk"
    assert snap["native_abis"] == ["arm64-v8a"]
    assert "merged_xapk" in snap["quality_flags"]
    assert list((lab / "cache").rglob("*.apk")) == []


def test_fichier_absent(lab, site, capsys):
    write_signatures(lab)
    assert cli.main(["--site-repo", str(site), "analyze", "/nulle/part.apk",
                     "--slug", "fdroid-privileged"]) == 1
    assert "introuvable" in capsys.readouterr().err


def test_slug_hors_corpus(lab, site, capsys):
    write_signatures(lab)
    with pytest.raises(SystemExit):
        cli.main(["--site-repo", str(site), "analyze", APK, "--slug", "inconnu"])
