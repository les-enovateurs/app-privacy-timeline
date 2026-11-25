"""La commande project de bout en bout, sur un faux site.

Ce qui est vérifié ici n'est pas la décision — test_project s'en charge — mais ce que la
commande fait au disque de quelqu'un d'autre : ne rien écrire sans --write, ne pas
toucher une fiche identique, ne pas faire reculer le site, et écrire les deux fiches d'un
handle porté par deux slugs.
"""

import json

import pytest

from apklab import cli, corpus, project, store
from apklab.corpus import CorpusEntry
from apklab.models import Snapshot

SLUG = "carrefour"
HANDLE = "com.carrefour.fid.android"

FICHE = {"app_name": "Carrefour", "handle": HANDLE, "version": "23.0.0",
         "report_date": "2026-04-18T16:17:00.000Z",
         "permissions": ["android.permission.INTERNET"],
         "trackers": [49, 12], "slug_playstore": HANDLE}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    corpus.write([CorpusEntry(slug=SLUG, handle=HANDLE, app_name="Carrefour")],
                 tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "CORPUS_PATH", tmp_path / "corpus.json")
    monkeypatch.setattr(cli, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(cli, "RUNS_ROOT", tmp_path / "runs")
    (tmp_path / "public" / "data" / "compare").mkdir(parents=True)
    return tmp_path


def compare(lab):
    return lab / "public" / "data" / "compare"


def pose_fiche(lab, slug: str, doc: dict) -> "object":
    """Une fiche écrite au format exact du site : indentation 2, sans newline final."""
    path = compare(lab) / f"{slug}.json"
    path.write_text(project.dumps(doc), "utf-8")
    return path


def pose_snapshot(lab, **kw):
    defaults = dict(handle=HANDLE, slug=SLUG, app_name="Carrefour",
                    version_name="23.0.0", source="exodus-git",
                    observed_at="2026-04-18T18:17:00+02:00",
                    report_date="2026-04-18T16:17:00.000Z",
                    permissions=["android.permission.INTERNET"],
                    trackers=[{"id": 12, "name": "A"}, {"id": 49, "name": "B"}])
    store.ingest(lab / "snapshots", [Snapshot(**{**defaults, **kw})], write=True)


def projette(lab, *args) -> int:
    return cli.main(["--site-repo", str(lab), "project", *args])


def rapport(lab) -> dict:
    return json.loads(sorted((lab / "runs").glob("*.json"))[-1].read_text("utf-8"))


# ------------------------------------------------------------- l'aller-retour

def test_une_fiche_deja_a_jour_n_est_pas_touchee(lab, capsys):
    """La propriété qui valide l'étage : le store vient du git du site, donc projeter
    doit rendre ses fiches inchangées, octet pour octet."""
    path = pose_fiche(lab, SLUG, FICHE)
    avant = path.read_bytes()
    pose_snapshot(lab)

    assert projette(lab, "--all", "--write") == 0

    assert path.read_bytes() == avant
    assert "1 inchangée(s)" in capsys.readouterr().err


def test_un_reordonnancement_ne_reecrit_pas_la_fiche(lab, capsys):
    """La fiche porte ses pisteurs dans l'ordre d'Exodus — 80 des 131 sont dans ce cas.
    Trier pour trier produirait un diff où aucun fait n'a bougé."""
    path = pose_fiche(lab, SLUG, FICHE)
    avant = path.read_bytes()
    pose_snapshot(lab, trackers=[{"id": 49, "name": "B"}, {"id": 12, "name": "A"}])

    projette(lab, "--all", "--write")

    assert path.read_bytes() == avant


def test_un_fait_qui_bouge_est_ecrit(lab, capsys):
    path = pose_fiche(lab, SLUG, FICHE)
    pose_snapshot(lab, version_name="24.0.0",
                  permissions=["android.permission.CAMERA"],
                  report_date="2026-06-01T10:00:00.000Z")

    projette(lab, "--all", "--write")
    doc = json.loads(path.read_text("utf-8"))

    assert doc["version"] == "24.0.0"
    assert doc["permissions"] == ["android.permission.CAMERA"]
    assert doc["report_date"] == "2026-06-01T10:00:00.000Z"
    assert doc["slug_playstore"] == HANDLE


def test_la_fiche_reste_lisible_ligne_a_ligne(lab, capsys):
    """Le format n'est pas du confort : sans lui le diff de revue serait une ligne de
    2 Ko remplacée par une autre, et la validation humaine n'aurait rien à valider."""
    path = pose_fiche(lab, SLUG, FICHE)
    pose_snapshot(lab, version_name="24.0.0")

    projette(lab, "--all", "--write")
    texte = path.read_text("utf-8")

    assert texte.startswith("{\n  ")
    assert not texte.endswith("\n")


# ------------------------------------------------------------------ les refus

def test_le_site_en_avance_n_est_pas_ecrase(lab, capsys):
    """Sans cette garde, la première projection réelle aurait fait reculer 13
    applications du site, dont Facebook de 548.1.0.51.64 à 529.0.0.44.73."""
    path = pose_fiche(lab, SLUG, FICHE)
    avant = path.read_bytes()
    pose_snapshot(lab, version_name="22.0.0", report_date="2025-09-13T10:00:00.000Z")

    assert projette(lab, "--all", "--write") == 0

    assert path.read_bytes() == avant
    assert "1 site en avance" in capsys.readouterr().err
    assert rapport(lab)["apps"][0]["files"][0]["status"] == "site_ahead"


def test_force_passe_outre_le_site_en_avance(lab, capsys):
    path = pose_fiche(lab, SLUG, FICHE)
    pose_snapshot(lab, version_name="22.0.0", report_date="2025-09-13T10:00:00.000Z")

    projette(lab, "--all", "--force", "--write")

    assert json.loads(path.read_text("utf-8"))["version"] == "22.0.0"


def test_dernier_point_sans_analyse_de_pisteurs_fait_sauter_l_application(lab, capsys):
    """Publier sa liste vide écrirait « aucun pisteur » sur la fiche."""
    path = pose_fiche(lab, SLUG, FICHE)
    avant = path.read_bytes()
    pose_snapshot(lab)
    # Écrit à la main et non par `store.ingest`, qui refuse désormais ce drapeau. La
    # garde reste nécessaire pour les points déjà dans les séries : le store est
    # append-only, ce qui y est écrit y reste.
    ligne = json.dumps({**Snapshot(
        handle=HANDLE, slug=SLUG, app_name="Carrefour", version_name="23.6.0",
        source="apk-static", acquisition="apkpure",
        observed_at="2026-09-09T14:38:52+00:00", permissions=["android.permission.INTERNET"],
        trackers=[], quality_flags=["trackers_not_analyzed"]).to_dict(),
        "previous_observed_at": "2026-04-18T18:17:00+02:00"}, ensure_ascii=False,
        sort_keys=True)
    série = lab / "snapshots" / f"{HANDLE}.jsonl"
    série.write_text(série.read_text("utf-8") + ligne + "\n", "utf-8")

    projette(lab, "--all", "--write")

    assert path.read_bytes() == avant
    assert "1 sans analyse de pisteurs" in capsys.readouterr().err
    assert rapport(lab)["apps"][0]["status"] == "trackers_not_analyzed"


def test_application_sans_snapshot_est_signalee(lab, capsys):
    pose_fiche(lab, SLUG, FICHE)
    assert projette(lab, "--all") == 0
    assert "1 sans snapshot" in capsys.readouterr().err


def test_fiche_illisible_n_emporte_pas_les_autres(lab, capsys):
    (compare(lab) / f"{SLUG}.json").write_text("{pas du json", "utf-8")
    pose_snapshot(lab)

    assert projette(lab, "--all", "--write") == 0

    assert "1 erreur(s)" in capsys.readouterr().err
    assert rapport(lab)["apps"][0]["files"][0]["status"] == "error"


# ------------------------------------------------------------------- l'écriture

def test_dry_run_n_ecrit_rien(lab, capsys):
    path = pose_fiche(lab, SLUG, FICHE)
    avant = path.read_bytes()
    pose_snapshot(lab, version_name="24.0.0")

    assert projette(lab, "--all") == 0

    assert path.read_bytes() == avant
    assert not (lab / "runs").exists()
    err = capsys.readouterr().err
    assert "1 à changer" in err
    assert "dry-run" in err


def test_une_fiche_absente_est_creee(lab, capsys):
    pose_snapshot(lab)
    projette(lab, "--all", "--write")

    doc = json.loads((compare(lab) / f"{SLUG}.json").read_text("utf-8"))
    assert doc["app_name"] == "Carrefour"
    assert doc["handle"] == HANDLE
    assert doc["version"] == "23.0.0"


def test_les_deux_fiches_d_un_handle_partage_sont_ecrites(lab, capsys):
    """alibaba.json et aliexpress.json portent le même paquet. Le store étant clé par
    handle, une seule série alimente les deux ; n'en écrire qu'une laisserait l'autre
    dériver sans que rien ne le signale."""
    corpus.write([CorpusEntry(slug=SLUG, handle=HANDLE, app_name="Carrefour",
                              aliases=["carrefour-drive"])], lab / "corpus.json")
    pose_snapshot(lab)

    projette(lab, "--all", "--write")

    assert (compare(lab) / f"{SLUG}.json").exists()
    assert (compare(lab) / "carrefour-drive.json").exists()
    assert "2 fiche(s)" in capsys.readouterr().err


def test_out_remplace_le_repertoire_par_defaut(lab, capsys):
    ailleurs = lab / "ailleurs"
    ailleurs.mkdir()
    pose_snapshot(lab)

    projette(lab, "--all", "--out", str(ailleurs), "--write")

    assert (ailleurs / f"{SLUG}.json").exists()
    assert not (compare(lab) / f"{SLUG}.json").exists()


def test_repertoire_de_sortie_introuvable(lab, capsys):
    assert projette(lab, "--all", "--out", str(lab / "nulle-part")) == 1
    assert "répertoire de sortie introuvable" in capsys.readouterr().err


def test_la_commande_rappelle_la_revue_humaine(lab, capsys):
    """La projection ne commit jamais : la validation promise par le design est un
    git diff lu par un humain."""
    pose_fiche(lab, SLUG, FICHE)
    pose_snapshot(lab)
    projette(lab, "--all")

    assert "git -C" in capsys.readouterr().err


def test_le_rapport_porte_le_detail_du_changement(lab, capsys):
    pose_fiche(lab, SLUG, FICHE)
    pose_snapshot(lab, version_name="24.0.0", report_date="2026-06-01T10:00:00.000Z")

    projette(lab, "--all", "--write")
    app = rapport(lab)["apps"][0]

    assert app["status"] == "changed"
    assert app["files"][0]["changes"]["version"] == ["23.0.0", "24.0.0"]
