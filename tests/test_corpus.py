"""Le corpus est clé par handle : c'est ce qui garde les séries propres."""

from apklab.corpus import CorpusEntry, app_list, seed

from conftest import fiche, write_fiche


def test_fichier_sans_handle_ignore(site):
    """breaches.json, permissions.json, trackers.json... sont des catalogues,
    pas des applications. Ils sortent du corpus sans lever d'anomalie."""
    write_fiche(site, "app", fiche("com.app", "1.0", "2026-01-01T00:00:00Z", ["A"], [1]))
    write_fiche(site, "breaches", [{"nom": "pas une app"}])
    write_fiche(site, "dpo-directory", {"pas_de": "handle"})

    entries, issues = seed(site)

    assert [e.slug for e in entries] == ["app"]
    assert issues == []


def test_handle_duplique_devient_alias(site):
    """Cas réel : alibaba.json et aliexpress.json portent le même paquet. Deux entrées
    fusionneraient deux historiques dans une même série."""
    write_fiche(site, "alibaba", fiche("com.ali", "8.1", "2026-01-01T00:00:00Z", [], []))
    write_fiche(site, "aliexpress", fiche("com.ali", "8.0", "2026-01-01T00:00:00Z", [], []))

    entries, issues = seed(site)

    assert len(entries) == 1
    assert entries[0].slug == "alibaba"
    assert entries[0].aliases == ["aliexpress"]
    assert [i["reason"] for i in issues] == ["duplicate_handle"]


def test_alias_present_dans_les_chemins_a_lire(site):
    """Les deux fichiers décrivent le même paquet : leurs deux historiques
    appartiennent à la même série, n'en lire qu'un perdrait l'autre."""
    write_fiche(site, "alibaba", fiche("com.ali", "8.1", "2026-01-01T00:00:00Z", [], []))
    write_fiche(site, "aliexpress", fiche("com.ali", "8.0", "2026-01-01T00:00:00Z", [], []))

    entry = seed(site)[0][0]

    assert entry.compare_relpaths == ["public/data/compare/alibaba.json",
                                      "public/data/compare/aliexpress.json"]


def test_app_name_par_defaut_le_slug(site):
    write_fiche(site, "app", {"handle": "com.app", "version": "1.0"})
    assert seed(site)[0][0].app_name == "app"


def test_json_illisible_signale_sans_planter(site):
    (site / "public/data/compare/casse.json").write_text("{pas du json", "utf-8")
    entries, issues = seed(site)
    assert entries == []
    assert [i["reason"] for i in issues] == ["invalid_json"]


# ------------------------------------------------- la liste d'un run à échelle réduite

CORPUS = [CorpusEntry(slug=s, handle=f"com.{s}", app_name=s.title())
          for s in ("carrefour", "fnac", "lidl")]


def test_app_list_garde_l_ordre_du_fichier(tmp_path):
    """Pas de tri : un run qui télécharge dure des dizaines de minutes, et s'il est
    interrompu il doit avoir couvert ce que l'humain a mis en tête de liste."""
    (tmp_path / "liste.txt").write_text("lidl\ncarrefour\nfnac\n", "utf-8")
    chosen, unknown = app_list(tmp_path / "liste.txt", CORPUS)

    assert [e.slug for e in chosen] == ["lidl", "carrefour", "fnac"]
    assert unknown == []


def test_app_list_commentaires_lignes_vides_et_doublons(tmp_path):
    (tmp_path / "liste.txt").write_text(
        "# passe du 2026-09-09\n\ncarrefour   # celle qui pèse 155 Mo\n"
        "carrefour\n   \nfnac\n", "utf-8")
    chosen, unknown = app_list(tmp_path / "liste.txt", CORPUS)

    assert [e.slug for e in chosen] == ["carrefour", "fnac"]
    assert unknown == []


def test_app_list_rend_les_inconnus_au_lieu_de_lever(tmp_path):
    """L'appelant refuse la liste entière avant la première requête ; s'arrêter à la
    première ligne fautée obligerait à relancer autant de fois qu'il y a de fautes."""
    (tmp_path / "liste.txt").write_text("carrefour\ncarrefourr\nfnak\n", "utf-8")
    chosen, unknown = app_list(tmp_path / "liste.txt", CORPUS)

    assert [e.slug for e in chosen] == ["carrefour"]
    assert unknown == ["carrefourr", "fnak"]


def test_reseeding_keeps_hand_added_apps_and_their_source():
    from apklab.corpus import CorpusEntry, merge
    existing = [CorpusEntry("tonies", "com.tonies.app", "Tonies", source="toys-afp-2026-09"),
                CorpusEntry("loona", "com.keyitech.loona", "Loona", source="toys-afp-2026-09")]
    seeded = [CorpusEntry("loona", "com.keyitech.loona", "Loona"),
              CorpusEntry("jowfr", "com.wishop.dev.jow", "Jow")]
    merged = {e.slug: e.source for e in merge(seeded, existing)}
    assert merged == {"loona": "toys-afp-2026-09", "jowfr": "umd-compare",
                      "tonies": "toys-afp-2026-09"}


# ------------------------------------------------- fiches without a compare file yet

def _manual(site, slug, doc):
    import json
    folder = site / "public/data/manual"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.json").write_text(json.dumps(doc), "utf-8")


def test_a_draft_fiche_with_a_play_store_id_enters_the_corpus(site):
    _manual(site, "n26", {"name": "N26", "status": "draft",
                          "playstore": {"id": "de.number26.android", "name": "N26"}})
    _manual(site, "no-app", {"name": "Sans appli"})
    _manual(site, "slugs", ["pas", "une", "fiche"])

    entries, issues = seed(site)

    assert [(e.slug, e.handle, e.source) for e in entries] == [
        ("n26", "de.number26.android", "umd-fiche")]
    assert issues == []


def test_a_compare_file_wins_over_the_fiche_for_the_same_handle(site):
    write_fiche(site, "app", fiche("com.app", "1.0", "2026-01-01T00:00:00Z", [], []))
    _manual(site, "app", {"playstore": {"id": "com.app"}})

    assert [(e.slug, e.source) for e in seed(site)[0]] == [("app", "umd-compare")]
