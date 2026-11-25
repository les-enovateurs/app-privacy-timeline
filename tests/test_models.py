"""L'empreinte décide seule si un snapshot est écrit : elle doit être stable."""

from apklab.models import Snapshot, fingerprint


def test_fingerprint_ignore_ordre_de_serialisation():
    """La source ne garantit pas l'ordre ; deux mêmes faits font une même empreinte."""
    assert (fingerprint("1.0", ["B", "A"], [{"id": 2}, {"id": 1}])
            == fingerprint("1.0", ["A", "B"], [{"id": 1}, {"id": 2}]))


def test_fingerprint_sensible_a_la_version():
    """Sans ça, une nouvelle version au même jeu de permissions serait perdue,
    et la série de poids aurait des trous."""
    assert (fingerprint("1.0", ["A"], []) != fingerprint("1.1", ["A"], []))


def test_fingerprint_sensible_aux_permissions_et_aux_trackers():
    assert fingerprint("1.0", ["A"], []) != fingerprint("1.0", ["A", "B"], [])
    assert fingerprint("1.0", ["A"], []) != fingerprint("1.0", ["A"], [{"id": 1}])


def test_fingerprint_pas_de_collision_par_concatenation():
    """Les champs sont séparés par un octet non imprimable : un découpage différent
    ne peut pas produire la même charge."""
    assert (fingerprint("1.0A", [], []) != fingerprint("1.0", ["A"], []))


def test_snapshot_round_trip():
    snap = Snapshot(handle="h", slug="s", app_name="S", version_name="1.0",
                    observed_at="2026-01-01T00:00:00+00:00")
    assert Snapshot.from_dict(snap.to_dict()) == snap


def test_source_inconnue_refusee():
    import pytest
    with pytest.raises(ValueError):
        Snapshot(handle="h", slug="s", app_name="S", version_name="1.0", source="maison")


def test_an_old_snapshot_loads_without_reference_fields():
    doc = Snapshot(handle="x", slug="x", app_name="X", version_name="1.0",
                   observed_at="2026-01-01T00:00:00+00:00").to_dict()
    for key in ("size_parts", "native_bytes_by_abi", "reference_bytes"):
        doc.pop(key)
    old = Snapshot.from_dict(doc)
    assert (old.size_parts, old.native_bytes_by_abi, old.reference_bytes) == (None, None, None)
