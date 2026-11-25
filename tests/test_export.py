"""La jointure du dataset, hors CLI.

Deux propriétés portent tout le module : une tranche par application ne garde que les
lignes qui citent son handle, et elle les garde **entières** — la prévalence sur le
corpus est le fait opposable, la réduire à l'application courante la fausserait.
"""

import json

from apklab import export
from apklab.corpus import CorpusEntry


def entry(slug="x", handle="com.app.x", app_name="X", aliases=()):
    return CorpusEntry(slug=slug, handle=handle, app_name=app_name,
                       aliases=list(aliases))


def snap(version="1.0", source="apk-static", observed_at="2026-09-10T00:00:00+00:00"):
    return {"handle": "com.app.x", "slug": "x", "version_name": version,
            "source": source, "observed_at": observed_at, "permissions": [],
            "trackers": [], "quality_flags": []}


def test_handles_nus_et_handles_objets_sont_lus_pareil():
    """`endpoints.json` liste des handles, `candidates.json` des objets. Les deux
    producteurs ont raison ; c'est à la jointure de s'adapter."""
    assert export.handles_of({"apps": ["com.a", "com.b"]}) == {"com.a", "com.b"}
    assert export.handles_of({"apps": [{"handle": "com.a", "class_count": 3}]}) == {"com.a"}
    assert export.handles_of({}) == set()


def test_slice_for_ne_garde_que_les_lignes_de_l_application():
    rows = [{"host": "api.tiers.com", "apps": ["com.app.x", "com.app.y"]},
            {"host": "api.autre.com", "apps": ["com.app.y"]}]
    assert [r["host"] for r in export.slice_for("com.app.x", rows)] == ["api.tiers.com"]


def test_la_tranche_garde_la_prevalence_du_corpus():
    """Le fait est « deux applications parlent à cet hôte », pas « une »."""
    rows = [{"host": "api.tiers.com", "apps": ["com.app.x", "com.app.y"], "app_count": 2}]
    tranche = export.slice_for("com.app.x", rows)
    assert tranche[0]["app_count"] == 2
    assert tranche[0]["apps"] == ["com.app.x", "com.app.y"]


def test_le_document_compte_ce_qu_il_porte():
    série = [snap("1.0", source="exodus-git"), snap("2.0")]
    doc = export.app_document(entry(), série, [{"has_change": True}],
                              hosts=[{"host": "api.tiers.com"}],
                              accounts=[{"id": "firebase-project"}],
                              candidates=[{"package": "com.tiers.sdk"}])
    assert doc["counts"] == {"snapshots": 2, "static_analyses": 1, "deltas": 1,
                             "endpoints": 1, "accounts": 1, "candidates": 1}
    assert doc["dataset_version"] == export.DATASET_VERSION
    assert doc["latest"]["version_name"] == "2.0"


def test_latest_garde_les_drapeaux_de_qualite():
    """`project` refuse de publier un point `trackers_not_analyzed` ; le dataset privé
    doit au contraire le montrer, sinon son lecteur ne peut pas le refuser lui-même."""
    marqué = {**snap("2.0"), "quality_flags": ["trackers_not_analyzed"]}
    doc = export.app_document(entry(), [marqué], [], hosts=[], accounts=[],
                              candidates=[])
    assert doc["latest"]["quality_flags"] == ["trackers_not_analyzed"]


def test_une_application_sans_snapshot_a_un_latest_nul_et_pas_une_invention():
    doc = export.app_document(entry(), [], [], hosts=[], accounts=[], candidates=[])
    assert doc["latest"] is None
    assert doc["counts"]["snapshots"] == 0


def test_le_manifeste_totalise_les_documents():
    docs = [export.app_document(entry(slug="x", handle="com.app.x"), [snap()], [],
                                hosts=[{"host": "a"}], accounts=[], candidates=[]),
            export.app_document(entry(slug="y", handle="com.app.y"), [snap()], [],
                                hosts=[{"host": "b"}], accounts=[{"id": "k"}],
                                candidates=[])]
    index = [export.index_entry(d, f"apps/{d['slug']}.json") for d in docs]
    man = export.manifest(generated_at="2026-09-10T00:00:00+00:00", sources=[],
                          apps=index, skipped=[{"slug": "z", "reason": "no_snapshot"}])
    assert man["app_count"] == 2
    assert man["skipped_count"] == 1
    assert man["totals"]["endpoints"] == 2
    assert man["totals"]["accounts"] == 1
    assert man["visibility"] == "private"


def test_le_manifeste_nomme_le_fichier_de_chaque_application():
    doc = export.app_document(entry(), [snap("3.1")], [], hosts=[], accounts=[],
                              candidates=[])
    ligne = export.index_entry(doc, "apps/x.json")
    assert ligne == {"slug": "x", "handle": "com.app.x", "file": "apps/x.json",
                     "version": "3.1", "counts": doc["counts"]}


def test_l_empreinte_declare_un_document_absent(tmp_path):
    assert export.digest(tmp_path / "keys.json") == {"file": "keys.json",
                                                     "present": False}


def test_l_empreinte_d_un_document_present_porte_sa_taille(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text('{"accounts": []}', "utf-8")
    found = export.digest(path)
    assert found["present"] and found["bytes"] == 16
    assert len(found["sha256"]) == 64


def test_deux_exports_du_meme_etat_donnent_les_memes_octets():
    """Ce qui rend la comparaison octet à octet possible, donc le diff lisible."""
    doc = export.app_document(entry(), [snap()], [], hosts=[], accounts=[],
                              candidates=[])
    autre = export.app_document(entry(), [snap()], [], hosts=[], accounts=[],
                                candidates=[])
    assert export.dumps(doc) == export.dumps(autre)
    assert json.loads(export.dumps(doc))["slug"] == "x"
