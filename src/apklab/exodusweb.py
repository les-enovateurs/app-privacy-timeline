"""Accès réseau aux rapports Exodus : délai de politesse, reprise, cache par id.

Seul module du backfill Exodus à sortir de la machine. Le parsing est dans
backfill_exodus, qui n'a jamais besoin du réseau pour être testé.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

BASE = "https://reports.exodus-privacy.eu.org"
LIST_URL = BASE + "/fr/reports/search/{handle}/?page={page}"
REPORT_URL = BASE + "/fr/reports/{report_id}/"

# Le contact est dans l'agent : la collecte est identifiable et joignable, condition
# minimale pour scraper un service associatif qu'on ne veut pas gêner.
USER_AGENT = ("umd-apk-lab/0.1 (recherche Unlock My Data ; "
              "https://github.com/les-enovateurs/unlock-my-data)")

# Deux secondes entre deux requêtes. Mesuré : une dizaine de requêtes rapprochées
# suffisent à déclencher le 429 de leur nginx. Le backfill se lance une fois puis se
# complète à la marge, sa durée n'est pas le critère.
DEFAULT_DELAY = 2.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4


class HttpError(Exception):
    pass


class NotFound(HttpError):
    pass


def _urlopen(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "fr",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


@dataclass
class Client:
    """Un client par run. Le cache lui survit, le délai non.

    Le cache est la clé de la reprise : un id de rapport est immuable chez Exodus, son
    détail est donc mis en cache définitivement et jamais re-téléchargé. Une interruption
    au milieu de 131 applications ne coûte que les pages de liste au relancement.

    Les pages de liste, elles, ne sont pas mises en cache : elles bougent à chaque
    nouveau scan, et les servir depuis le disque ferait manquer les versions récentes.
    """
    cache_dir: Path
    delay: float = DEFAULT_DELAY
    timeout: float = 30.0
    fetch: Callable[[str, float], str] = _urlopen
    sleep: Callable[[float], None] = time.sleep
    stats: dict = field(default_factory=lambda: {"requests": 0, "cached": 0, "retries": 0})
    _last_at: float = 0.0

    def get(self, url: str) -> str:
        """Une requête hors rapport — l'API des signatures — au même rythme que le reste."""
        return self._get(url)

    def list_page(self, handle: str, page: int = 1) -> str:
        return self._get(LIST_URL.format(handle=handle, page=page))

    def report(self, report_id: int) -> str:
        path = self.cache_dir / "reports" / f"{report_id}.html"
        if path.exists():
            self.stats["cached"] += 1
            return path.read_text("utf-8")
        html = self._get(REPORT_URL.format(report_id=report_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        # Écriture par fichier temporaire puis renommage : un Ctrl-C au mauvais moment
        # laisserait sinon un HTML tronqué en cache, que rien ne re-téléchargerait jamais.
        tmp = path.with_suffix(".part")
        tmp.write_text(html, "utf-8")
        tmp.replace(path)
        return html

    def _get(self, url: str) -> str:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait()
            self.stats["requests"] += 1
            try:
                return self.fetch(url, self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise NotFound(f"404 sur {url}") from exc
                if exc.code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    raise HttpError(f"HTTP {exc.code} sur {url}") from exc
            except urllib.error.URLError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise HttpError(f"{exc.reason} sur {url}") from exc
            # Recul exponentiel : un 429 signifie qu'on est déjà trop rapide, réessayer
            # au même rythme ne ferait qu'aggraver le blocage.
            self.stats["retries"] += 1
            self.sleep(self.delay * 2 ** attempt)
        raise HttpError(f"échec après {MAX_ATTEMPTS} tentatives sur {url}")

    def _wait(self) -> None:
        remaining = self._last_at + self.delay - time.monotonic()
        if remaining > 0:
            self.sleep(remaining)
        self._last_at = time.monotonic()
