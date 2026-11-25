"""Modèle de snapshot — une ligne JSONL dans snapshots/<handle>.jsonl."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

SOURCES = ("apk-static", "exodus-web", "exodus-git")
ACQUISITIONS = ("apkpure", "google-play", "manual")
SIZE_SCOPES = ("single-apk", "merged-xapk", "base-only")

# Séparateur de champ dans la charge d'empreinte. Un octet non imprimable, absent des
# noms de permission comme des versions : deux découpages différents ne peuvent pas
# produire la même charge.
_SEP = "\x1f"


def fingerprint(version_name: str | None,
                permissions: list[str],
                trackers: list[dict[str, Any]]) -> str:
    """sha256(version_name + permissions + trackers), ordre-indépendant.

    version_name entre dans l'empreinte : une nouvelle version au même jeu de
    permissions produit quand même un snapshot, sans quoi la série de poids serait
    trouée. Le tri rend l'empreinte insensible à l'ordre de sérialisation de la source.
    """
    perms = "\n".join(sorted(permissions))
    trks = ",".join(str(t["id"]) for t in sorted(trackers, key=lambda t: t["id"]))
    payload = _SEP.join([version_name or "", perms, trks])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class Snapshot:
    # identité
    handle: str
    slug: str
    app_name: str
    version_name: str | None
    version_code: int | None = None
    # provenance
    source: str = "exodus-git"
    acquisition: str | None = None
    apk_sha256: str | None = None
    analyzer: str | None = None
    analyzer_version: str | None = None
    catalog_version: str | None = None
    # temps
    observed_at: str = ""
    previous_observed_at: str | None = None
    report_date: str | None = None
    report_updated_at: str | None = None
    # privacy
    permissions: list[str] = field(default_factory=list)
    trackers: list[dict[str, Any]] = field(default_factory=list)
    # éco
    size_scope: str | None = None
    total_bytes: int | None = None
    dex_bytes: int | None = None
    res_bytes: int | None = None
    assets_bytes: int | None = None
    native_bytes: int | None = None
    native_abis: list[str] | None = None
    native_bytes_by_abi: dict[str, int] | None = None
    size_parts: list[dict[str, Any]] | None = None
    reference_bytes: int | None = None
    dex_method_count: int | None = None
    min_sdk: int | None = None
    target_sdk: int | None = None
    # empreinte et qualité
    fingerprint: str = ""
    quality_flags: list[str] = field(default_factory=list)
    provenance_ref: str | None = None

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"source inconnue : {self.source!r}")
        if not self.fingerprint:
            self.fingerprint = fingerprint(self.version_name, self.permissions, self.trackers)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, doc: dict[str, Any]) -> "Snapshot":
        return cls(**doc)
