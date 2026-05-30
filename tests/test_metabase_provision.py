"""Tests for the Metabase dashboards-as-code tool (metabase/provision.py).

Uses an in-memory fake Metabase (httpx.MockTransport) so the provision/export logic is fully
exercised without a live server.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import pytest

# provision.py lives under metabase/, not in the installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "metabase"))
import provision  # noqa: E402


class FakeMetabase:
    """A tiny stateful Metabase API stand-in."""

    def __init__(self, databases=None):
        self.databases = databases if databases is not None else [
            {"id": 1, "name": "Da Nang Marts"}
        ]
        self.collections: list[dict] = []
        self.cards: dict[int, dict] = {}
        self.dashboards: dict[int, dict] = {}
        self._next = {"collection": 1, "card": 1, "dashboard": 1}
        self.calls: list[tuple[str, str]] = []

    def _id(self, kind: str) -> int:
        v = self._next[kind]
        self._next[kind] += 1
        return v

    def client(self, **kwargs) -> provision.MetabaseClient:
        http = httpx.Client(transport=httpx.MockTransport(self.handler), base_url="http://mb")
        kwargs.setdefault("session_token", "tok")
        return provision.MetabaseClient("http://mb", http=http, **kwargs)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        body = json.loads(request.content) if request.content else {}

        if path == "/api/session" and method == "POST":
            return httpx.Response(200, json={"id": "tok"})
        if path == "/api/database" and method == "GET":
            return httpx.Response(200, json={"data": self.databases})
        if path == "/api/database" and method == "POST":
            nid = max((d["id"] for d in self.databases), default=0) + 1
            self.databases.append({"id": nid, "name": body["name"]})
            return httpx.Response(200, json={"id": nid})
        if path == "/api/collection" and method == "GET":
            return httpx.Response(200, json=self.collections)
        if path == "/api/collection" and method == "POST":
            cid = self._id("collection")
            self.collections.append({"id": cid, "name": body["name"], "archived": False})
            return httpx.Response(200, json={"id": cid})

        m = re.match(r"/api/collection/(\d+)/items$", path)
        if m and method == "GET":
            coll = int(m.group(1))
            model = request.url.params.get("models")
            if model == "card":
                items = [
                    {"id": c["id"], "name": c["name"], "model": "card"}
                    for c in self.cards.values() if c["collection_id"] == coll
                ]
            else:
                items = [
                    {"id": d["id"], "name": d["name"], "model": "dashboard"}
                    for d in self.dashboards.values() if d["collection_id"] == coll
                ]
            return httpx.Response(200, json={"data": items})

        if path == "/api/card" and method == "POST":
            cid = self._id("card")
            self.cards[cid] = {"id": cid, **body}
            return httpx.Response(200, json={"id": cid})
        m = re.match(r"/api/card/(\d+)$", path)
        if m and method == "PUT":
            self.cards[int(m.group(1))].update(body)
            return httpx.Response(200, json={"id": int(m.group(1))})

        if path == "/api/dashboard" and method == "POST":
            did = self._id("dashboard")
            self.dashboards[did] = {"id": did, "dashcards": [], **body}
            return httpx.Response(200, json={"id": did})
        m = re.match(r"/api/dashboard/(\d+)$", path)
        if m and method == "PUT":
            did = int(m.group(1))
            self.dashboards[did].update(body)
            return httpx.Response(200, json=self.dashboards[did])
        if m and method == "GET":
            return httpx.Response(200, json=self.dashboards[int(m.group(1))])

        return httpx.Response(404, json={"error": f"unhandled {method} {path}"})


# --- pure helpers ---------------------------------------------------------------------------
def test_build_card_payload():
    payload = provision.build_card_payload(
        {"name": "X", "display": "bar", "query": "  SELECT 1  "}, database_id=7, collection_id=3
    )
    assert payload["name"] == "X"
    assert payload["display"] == "bar"
    assert payload["collection_id"] == 3
    assert payload["dataset_query"] == {
        "type": "native", "native": {"query": "SELECT 1"}, "database": 7
    }


def test_build_dashcards_layout_and_negative_ids():
    cards = [{"layout": {"row": 1, "col": 2, "size_x": 4, "size_y": 5}}, {}]
    dcs = provision.build_dashcards([10, 11], cards)
    assert dcs[0]["id"] == -1 and dcs[1]["id"] == -2
    assert dcs[0]["card_id"] == 10
    assert (dcs[0]["row"], dcs[0]["col"], dcs[0]["size_x"], dcs[0]["size_y"]) == (1, 2, 4, 5)
    assert dcs[1]["size_x"] == 6  # default


def test_load_specs_reads_three_dashboards():
    specs = provision.load_specs()
    assert specs["database"] and specs["collection"]
    assert len(specs["dashboards"]) == 3
    assert sum(len(d["cards"]) for d in specs["dashboards"]) == 12


def test_validate_rejects_missing_query():
    with pytest.raises(ValueError, match="needs a 'query'"):
        provision._validate_dashboard_spec(
            {"name": "D", "cards": [{"name": "c"}]}, "f.yml"
        )


def test_validate_rejects_duplicate_card_names():
    with pytest.raises(ValueError, match="duplicate"):
        provision._validate_dashboard_spec(
            {"name": "D", "cards": [{"name": "c", "query": "x"}, {"name": "c", "query": "y"}]},
            "f.yml",
        )


def test_dashboard_to_spec_roundtrips_native_cards():
    live = {
        "name": "D", "description": " desc ",
        "dashcards": [
            {"row": 0, "col": 0, "size_x": 6, "size_y": 4,
             "card": {"name": "C", "display": "table",
                      "dataset_query": {"native": {"query": "SELECT 1"}}}},
            {"row": 0, "col": 6, "card": {"name": "text", "dataset_query": {}}},  # skipped
        ],
    }
    spec = provision.dashboard_to_spec(live)
    assert spec["name"] == "D"
    assert spec["description"] == "desc"
    assert len(spec["cards"]) == 1
    assert spec["cards"][0]["query"] == "SELECT 1"


# --- client / provision ---------------------------------------------------------------------
def test_provision_creates_collection_cards_dashboards():
    fake = FakeMetabase()
    result = provision.provision(fake.client(), provision.load_specs())
    assert len(fake.cards) == 12
    assert len(fake.dashboards) == 3
    assert set(result["dashboards"]) == {"Market Overview", "Trends & Activity", "Brokers & Deals"}
    # Each dashboard got its dashcards laid out.
    for dash in fake.dashboards.values():
        assert dash["dashcards"], f"{dash['name']} has no dashcards"


def test_provision_is_idempotent():
    fake = FakeMetabase()
    specs = provision.load_specs()
    client = fake.client()
    r1 = provision.provision(client, specs)
    r2 = provision.provision(client, specs)
    # No duplicates created on the second run; same ids returned.
    assert len(fake.cards) == 12
    assert len(fake.dashboards) == 3
    assert len(fake.collections) == 1
    assert r1["dashboards"] == r2["dashboards"]


def test_provision_requires_existing_database():
    fake = FakeMetabase(databases=[])
    with pytest.raises(provision.MetabaseError, match="not found"):
        provision.provision(fake.client(), provision.load_specs())


def test_provision_can_create_database(monkeypatch):
    monkeypatch.setenv("PG_PASSWORD", "secret")
    fake = FakeMetabase(databases=[])
    provision.provision(fake.client(), provision.load_specs(), create_database=True)
    assert any(d["name"] == "Da Nang Marts" for d in fake.databases)


def test_export_writes_specs(tmp_path, monkeypatch):
    # Copy real specs into a temp dir so export doesn't clobber the repo files.
    src = Path(__file__).resolve().parents[1] / "metabase" / "dashboards"
    dst = tmp_path / "dashboards"
    dst.mkdir()
    for f in ["config.yml", "market_overview.yml", "trends_activity.yml", "brokers_deals.yml"]:
        (dst / f).write_text((src / f).read_text())

    fake = FakeMetabase()
    client = fake.client()
    specs = provision.load_specs(dst)
    provision.provision(client, specs)
    written = provision.export(client, specs, specs_dir=dst)
    assert len(written) == 3
    reloaded = provision.load_specs(dst)
    assert {d["name"] for d in reloaded["dashboards"]} == {
        "Market Overview", "Trends & Activity", "Brokers & Deals"
    }


def test_from_env_requires_credentials(monkeypatch):
    monkeypatch.delenv("MB_API_KEY", raising=False)
    monkeypatch.delenv("MB_USERNAME", raising=False)
    monkeypatch.delenv("MB_PASSWORD", raising=False)
    with pytest.raises(provision.MetabaseError, match="authenticate"):
        provision.MetabaseClient.from_env(http=httpx.Client(base_url="http://mb"))


def test_from_env_with_api_key_skips_login(monkeypatch):
    monkeypatch.setenv("MB_API_KEY", "key123")
    monkeypatch.setenv("MB_URL", "http://mb")
    fake = FakeMetabase()
    http = httpx.Client(transport=httpx.MockTransport(fake.handler), base_url="http://mb")
    client = provision.MetabaseClient.from_env(http=http)
    assert client.api_key == "key123"
    assert client.find_database_id("Da Nang Marts") == 1
    assert ("POST", "/api/session") not in fake.calls  # no login when using an API key
