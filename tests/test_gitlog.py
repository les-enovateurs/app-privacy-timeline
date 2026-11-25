"""Lecture de l'historique git du site.

`--follow` a été retiré le 2026-09-09 : il inventait des révisions par similarité et en
perdait de vraies. Ces tests verrouillent les deux côtés du remède.
"""

import pytest

from apklab.gitlog import GitError, blob, head, revisions

from conftest import git, init_repo

FNAC = "public/data/compare/fnac.json"
CARREFOUR = "public/data/compare/carrefour.json"


def fiche(handle: str, version: str) -> str:
    """Une fiche du site. Deux fiches ne diffèrent que par deux valeurs : c'est cette
    quasi-identité qui trompait la détection de renommage de `--follow`."""
    return ('{"app_name": "X", "handle": "%s", "version": "%s", '
            '"report_date": "2026-01-01T00:00:00.000Z", '
            '"permissions": ["android.permission.INTERNET"], "trackers": [12, 49]}'
            % (handle, version))


@pytest.fixture
def repo(tmp_path):
    """Trois commits sur une même fiche, sans renommage — la forme réelle du dépôt du
    site, où `git log --diff-filter=R` sur `compare/*.json` ne rend aucun renommage."""
    init_repo(tmp_path)
    compare = tmp_path / "public" / "data" / "compare"
    compare.mkdir(parents=True)
    for version in ("1.0", "1.1", "1.2"):
        (compare / "carrefour.json").write_text(fiche("com.carrefour", version), "utf-8")
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", f"maj {version}")
    return tmp_path


def test_revisions_ordre_chronologique(repo):
    revs = revisions(repo, CARREFOUR)
    assert len(revs) == 3
    assert [r.authored_at for r in revs] == sorted(r.authored_at for r in revs)


def test_revisions_rend_un_chemin_utilisable_par_git_show(repo):
    """Le chemin est lu de `--name-only` et non supposé : sans `--follow` il vaut
    toujours celui d'aujourd'hui, mais le lire garde le code juste si une fiche est un
    jour réellement déplacée."""
    revs = revisions(repo, CARREFOUR)
    assert [r.path for r in revs] == [CARREFOUR] * 3


def test_blob_lisible_a_chaque_revision(repo):
    """Régression : un séparateur \\x00 passé en argv fait échouer execve, et aucune
    révision n'est lisible."""
    for rev in revisions(repo, CARREFOUR):
        assert b'"handle": "com.carrefour"' in blob(repo, rev.sha, rev.path)


def test_fichier_inconnu_rend_une_liste_vide(repo):
    assert revisions(repo, "public/data/compare/jamais-committe.json") == []


def test_blob_absent_leve_git_error(repo):
    rev = revisions(repo, CARREFOUR)[0]
    with pytest.raises(GitError):
        blob(repo, rev.sha, "public/data/compare/jamais-committe.json")


def test_head_court(repo):
    assert len(head(repo)) >= 7


# ------------------------------------------------- ce que le retrait de --follow répare

@pytest.fixture
def repo_deux_fiches(tmp_path):
    """Une fiche créée, puis une seconde quasi identique créée plus tard.

    C'est la forme qui trompait `--follow` : les fiches Exodus ne diffèrent que par
    quelques valeurs, sa détection de similarité concluait au renommage, et la première
    révision de `carrefour.json` rendait le blob de `fnac.json`. 94 révisions du corpus
    étaient dans ce cas.
    """
    init_repo(tmp_path)
    compare = tmp_path / "public" / "data" / "compare"
    compare.mkdir(parents=True)

    (compare / "fnac.json").write_text(fiche("fr.fnac.com", "5.5.2"), "utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "ajout de comparatif")

    (compare / "fnac.json").unlink()
    (compare / "carrefour.json").write_text(fiche("com.carrefour", "23.0.0"), "utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "remplacement")
    return tmp_path


def test_aucune_revision_inventee_par_similarite(repo_deux_fiches):
    """Sans `--follow`, l'histoire de carrefour.json commence à carrefour.json. La série
    ne démarre plus sur les permissions d'une autre application."""
    revs = revisions(repo_deux_fiches, CARREFOUR)

    assert len(revs) == 1
    assert b'"handle": "com.carrefour"' in blob(repo_deux_fiches, revs[0].sha,
                                                revs[0].path)


def test_aucune_revision_perdue(repo_deux_fiches):
    """L'autre moitié du défaut, la plus grave parce qu'elle ne se voit pas : `--follow`
    rendait moins de révisions que `git log` nu sur 42 fiches réelles, et **zéro** sur
    dix d'entre elles — leurs applications sortaient « aucun snapshot » sans erreur.

    Le compte doit être celui de `git log` nu, ni plus ni moins — pour chaque fiche, y
    compris celle dont l'histoire comporte une suppression.
    """
    for relpath in (FNAC, CARREFOUR):
        attendu = [l for l in git(repo_deux_fiches, "log", "--format=%H", "--",
                                  relpath).splitlines() if l.strip()]
        assert len(revisions(repo_deux_fiches, relpath)) == len(attendu), relpath
