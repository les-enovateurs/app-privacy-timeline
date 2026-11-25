"""Cache, délai de politesse et réessais. Aucun test ne sort sur le réseau."""

import urllib.error

import pytest

from apklab import exodusweb


class FakeNet:
    """Réponses programmées par URL. Chaque appel est enregistré."""

    def __init__(self, **responses):
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float) -> str:
        self.calls.append(url)
        value = self.responses.get(url, "<html></html>")
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, int):
            raise urllib.error.HTTPError(url, value, "erreur", {}, None)
        return value


@pytest.fixture
def client(tmp_path):
    def make(net, **kwargs):
        slept: list[float] = []
        kwargs.setdefault("delay", 0.0)
        client = exodusweb.Client(tmp_path / "exodus", fetch=net,
                                  sleep=slept.append, **kwargs)
        client.slept = slept
        return client
    return make


def report_url(report_id: int) -> str:
    return exodusweb.REPORT_URL.format(report_id=report_id)


def test_rapport_mis_en_cache_definitivement(client, tmp_path):
    """Un id de rapport est immuable chez Exodus : son détail n'est jamais redemandé.
    C'est ce qui rend le backfill reprenable après interruption."""
    net = FakeNet(**{report_url(42): "<html>42</html>"})
    c = client(net)

    assert c.report(42) == "<html>42</html>"
    assert c.report(42) == "<html>42</html>"
    assert len(net.calls) == 1
    assert c.stats["cached"] == 1
    assert (tmp_path / "exodus" / "reports" / "42.html").read_text("utf-8") == "<html>42</html>"


def test_cache_partage_entre_deux_clients(client, tmp_path):
    """Le cache survit au run, sinon la reprise ne coûterait pas moins cher que le run."""
    net = FakeNet(**{report_url(42): "<html>42</html>"})
    client(net).report(42)
    second = FakeNet()
    client(second).report(42)
    assert second.calls == []


def test_liste_jamais_mise_en_cache(client):
    """La liste bouge à chaque nouveau scan ; la servir depuis le disque ferait manquer
    les versions récentes."""
    net = FakeNet()
    c = client(net)
    c.list_page("com.example.app", 1)
    c.list_page("com.example.app", 1)
    assert len(net.calls) == 2


def test_404_est_distingue_des_autres_erreurs(client):
    """Un rapport supprimé se saute ; une panne de réseau arrête l'application."""
    net = FakeNet(**{report_url(7): 404})
    with pytest.raises(exodusweb.NotFound):
        client(net).report(7)


def test_429_est_reessaye_puis_reussit(client):
    net = FakeNet(**{report_url(7): [429, "<html>ok</html>"]})
    c = client(net)
    assert c.report(7) == "<html>ok</html>"
    assert c.stats["retries"] == 1


def test_erreur_persistante_finit_par_lever(client):
    net = FakeNet(**{report_url(7): [503] * exodusweb.MAX_ATTEMPTS})
    c = client(net)
    with pytest.raises(exodusweb.HttpError):
        c.report(7)
    assert len(net.calls) == exodusweb.MAX_ATTEMPTS


def test_erreur_persistante_ne_laisse_rien_en_cache(client, tmp_path):
    """Un HTML tronqué en cache ne serait jamais re-téléchargé."""
    net = FakeNet(**{report_url(7): [503] * exodusweb.MAX_ATTEMPTS})
    with pytest.raises(exodusweb.HttpError):
        client(net).report(7)
    assert not (tmp_path / "exodus" / "reports" / "7.html").exists()


def test_erreur_non_reessayable_leve_tout_de_suite(client):
    net = FakeNet(**{report_url(7): [403, "<html>ok</html>"]})
    c = client(net)
    with pytest.raises(exodusweb.HttpError):
        c.report(7)
    assert len(net.calls) == 1


def test_recul_exponentiel_entre_deux_essais(client):
    """Un 429 dit qu'on est déjà trop rapide ; réessayer au même rythme aggraverait le
    blocage."""
    net = FakeNet(**{report_url(7): [429, 429, "<html>ok</html>"]})
    c = client(net, delay=1.0)
    c.report(7)
    assert [d for d in c.slept if d >= 1.0] == [2.0, 4.0]


def test_delai_de_politesse_entre_deux_requetes(client):
    net = FakeNet()
    c = client(net, delay=1.0)
    c.list_page("com.example.app", 1)
    c.list_page("com.example.app", 2)
    assert any(0 < d <= 1.0 for d in c.slept)
