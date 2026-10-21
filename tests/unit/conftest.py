"""Fixtures shared by the ``signal_obs`` unit tests (fictional tenant ``realshape``)."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE_URL = "https://members.example.test/list"

_SOURCE = """
[[source]]
id = "north-directory"
title = "North members list"
url = "{url}"
kind = "member_directory"
premise = ""
claim_gap = "Membership shows the firm runs agents across clouds."
precision = "{precision}"
member_role = "{role}"
timing = "{timing}"
cadence_days = 30
extractor = "{extractor}"
extractor_args = {args}
max_members = {max_members}
expires_on = "2026-12-01"
owner = "ops"
"""


def page(*names: str, domains: dict[str, str] | None = None) -> str:
    """A member-directory page in markdown: one link per name, outbound to its own domain."""
    domains = domains or {}
    rows = []
    for n in names:
        host = domains.get(n, n.lower().replace(" ", "") + ".example.test")
        rows.append(f"- [{n}](https://{host}/)")
    return "# Our members\n\n" + "\n".join(rows) + "\n"


@dataclass
class World:
    profiles_root: Path
    content_root: Path
    profile: str = "realshape"
    product: str = "alpha"

    @property
    def sources_dir(self) -> Path:
        return self.content_root / self.profile / "sources"

    @property
    def obs_dir(self) -> Path:
        return self.content_root / self.profile / "prospects" / "observations"

    def registry(
        self,
        *,
        precision="8/10",
        role="buyer",
        extractor="links",
        args="{}",
        max_members=50,
        timing="listing_date",
        product_file: str | None = None,
    ) -> None:
        """Write the fixture registry: the profile file, or ``products/<slug>/`` when named."""
        base = self.profiles_root / self.profile
        p = (
            base / "products" / product_file / "signal-sources.toml"
            if product_file
            else (base / "knowledge" / "signal-sources.toml")
        )
        p.write_text(
            _SOURCE.format(
                url=SOURCE_URL,
                precision=precision,
                role=role,
                extractor=extractor,
                args=args,
                max_members=max_members,
                timing=timing,
            ),
            encoding="utf-8",
        )

    def ledger(self, *pairs: tuple[str, str]) -> None:
        d = self.content_root / self.profile / "prospects"
        d.mkdir(parents=True, exist_ok=True)
        items = [{"company": c, "domain": dom} for c, dom in pairs]
        (d / "latest.json").write_text(
            json.dumps({"kind": "prospects", "profile": self.profile, "items": items}),
            encoding="utf-8",
        )

    def competitors(self, *pairs: tuple[str, str]) -> None:
        """Write the profile's ``competitors.toml``: each pair is (name, domain)."""
        body = "schema = 1\n" + "".join(
            f'\n[[competitor]]\nname = "{n}"\ntier = "direct"\ndomains = ["{d}"]\n'
            for n, d in pairs
        )
        (self.profiles_root / self.profile / "knowledge" / "competitors.toml").write_text(
            body, encoding="utf-8"
        )

    def settings(self, observer_id: str = "amy") -> None:
        d = self.content_root / self.profile
        d.mkdir(parents=True, exist_ok=True)
        (d / "settings.json").write_text(json.dumps({"observer_id": observer_id}), encoding="utf-8")

    def capture(self, text: str, fetched_at: str) -> str:
        from gtm_core.signal_sources import store_capture

        return store_capture(SOURCE_URL, text, sources_dir=self.sources_dir, fetched_at=fetched_at)

    def run(self, day: str = "2026-10-01", run_id: str = "run1", **kw):
        from gtm_core.signal_obs import extract

        d = datetime.date.fromisoformat(day)
        return extract.run_extract(
            self.profile,
            "north-directory",
            product=kw.pop("product", self.product),
            profiles_root=self.profiles_root,
            content_root=self.content_root,
            today=d,
            now=datetime.datetime(d.year, d.month, d.day, 9, tzinfo=datetime.UTC),
            run_id=run_id,
            **kw,
        )

    def observations(self, product: str | None = None):
        from gtm_core.signal_obs import observations as obs

        return obs.read_all(self.obs_dir, product=product or self.product).observations


@pytest.fixture
def signal_world(tmp_path, one_product_profiles, monkeypatch) -> World:
    monkeypatch.setenv("GTM_SIGNAL_SOURCES_ENABLED", "1")
    w = World(profiles_root=one_product_profiles, content_root=tmp_path / "content")
    w.registry()
    w.settings()
    w.ledger()
    return w


@pytest.fixture
def switch_open(monkeypatch):
    """The source-list switch open, for a test that calls the write primitives directly."""
    monkeypatch.setenv("GTM_SIGNAL_SOURCES_ENABLED", "1")
