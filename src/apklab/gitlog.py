"""Lecture de l'historique git du site. Seule frontière impure des itérations 0 à 2."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Séparateurs demandés à git via ses propres échappements %x00 et %x1f. Les passer
# comme octets bruts dans argv est impossible : execve refuse un octet nul.
_REC = "\x00"
_FLD = "\x1f"
_FORMAT = "--format=%x00%H%x1f%aI"


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Revision:
    sha: str
    authored_at: str
    path: str


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} : {proc.stderr.strip()}")
    return proc.stdout


def revisions(repo: Path, relpath: str) -> list[Revision]:
    """Révisions touchant un fichier, de la plus ancienne à la plus récente.

    **`--follow` a été retiré le 2026-09-09, après mesure.** Il était là pour suivre les
    renommages ; il faisait deux choses, et les deux étaient mauvaises ici.

    Il inventait des révisions : les fiches Exodus ont une structure quasi identique, sa
    détection de renommage par similarité rendait le blob de `fnac.json` comme première
    révision de `carrefour.json`, et 94 révisions du corpus étaient dans ce cas.

    Il en **perdait** aussi, ce qui est pire parce que ça ne se voit pas. Mesuré sur les
    131 fiches : 42 fichiers pour lesquels `--follow` rend moins de révisions que
    `git log` nu, dont dix où il rend **zéro** alors que le fichier a deux commits —
    `action.json`, `airbnb-inc.json`, `badoo.json`… Ces applications sortaient « aucun
    snapshot » et l'outil annonçait 0 erreur. C'est le mode de panne que ce projet
    s'interdit : une absence qui se lit comme un fait.

    Le remède ne coûte rien : `git log --diff-filter=R` sur `public/data/compare/*.json`
    ne rend **aucun renommage réel**. `--follow` ne rattrapait donc rien du tout.

    `--name-only` reste, et le chemin rendu est utilisé tel quel par `git show` : sans
    `--follow` il vaut toujours `relpath`, mais le lire plutôt que le supposer garde le
    code juste si une fiche est un jour déplacée pour de bon.

    **`--diff-merges=first-parent` est indispensable, et c'est la seconde moitié du même
    défaut.** Par défaut git n'affiche aucun diff pour un commit de fusion, donc aucune
    ligne `--name-only`, donc rien à lire. Or le site fusionne ses branches sans squash :
    pour dix applications du corpus — `action`, `airbnb-inc`, `badoo`, `bereal`,
    `blablacar`, `cerballiance`… — la fiche n'a **jamais** été touchée autrement que dans
    un merge. Elles sortaient « aucun snapshot », sans erreur, alors que leur historique
    existait.

    Le premier parent est l'état de la branche d'accueil avant la fusion : diffé contre
    lui, un merge dit ce que le site a réellement publié à ce moment-là. C'est la bonne
    lecture pour dater un point de mesure. Demande git ≥ 2.31.
    """
    out = _git(repo, "log", "--diff-merges=first-parent", "--name-only", _FORMAT,
               "--", relpath)
    revs: list[Revision] = []
    for record in out.split(_REC):
        record = record.strip("\n")
        if not record:
            continue
        head, *rest = record.split("\n")
        sha, authored_at = head.split(_FLD)
        names = [line for line in rest if line.strip()]
        if not names:
            continue
        revs.append(Revision(sha=sha, authored_at=authored_at, path=names[0]))
    revs.reverse()
    return revs


def blob(repo: Path, sha: str, path: str) -> bytes:
    """Contenu d'un fichier à une révision. Bytes : le décodage est l'affaire du lecteur."""
    proc = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{path}"],
                          capture_output=True)
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout


def head(repo: Path) -> str:
    return _git(repo, "rev-parse", "--short", "HEAD").strip()
