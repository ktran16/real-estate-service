#!/usr/bin/env python3
"""Dashboards-as-code for the self-hosted Metabase.

Provisions (and re-exports) the Da Nang dashboards from version-controlled YAML specs via the
Metabase REST API, so the three dashboards never have to be hand-built in the UI and stay
reproducible. Idempotent: cards/dashboards are matched by name within the target collection and
updated in place, so re-running converges rather than duplicating.

Usage (run against your own Metabase — credentials are never committed):

    export MB_URL=http://localhost:3001
    export MB_USERNAME=you@example.com MB_PASSWORD=...   # or: export MB_API_KEY=...
    uv run --extra metabase python metabase/provision.py provision
    uv run --extra metabase python metabase/provision.py provision --create-database  # also add the PG conn
    uv run --extra metabase python metabase/provision.py export    # pull live dashboards back to YAML

The Postgres "serving" DB connection must already exist in Metabase (add it once in the UI), or
pass --create-database to create it from the PG_* env vars (the same ones the pipeline uses).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

logger = logging.getLogger("metabase.provision")

SPECS_DIR = Path(__file__).resolve().parent / "dashboards"


class MetabaseError(RuntimeError):
    """Raised on an unexpected Metabase API response."""


# --------------------------------------------------------------------------------------------
# Pure helpers (no I/O) — easy to unit-test.
# --------------------------------------------------------------------------------------------
def build_card_payload(card: dict, database_id: int, collection_id: Optional[int]) -> dict:
    """Build the POST/PUT /api/card body for a native-SQL card spec."""
    return {
        "name": card["name"],
        "display": card.get("display", "table"),
        "dataset_query": {
            "type": "native",
            "native": {"query": card["query"].strip()},
            "database": database_id,
        },
        "visualization_settings": card.get("visualization_settings", {}),
        "collection_id": collection_id,
    }


def build_dashcards(card_ids: list[int], cards: list[dict]) -> list[dict]:
    """Build the `dashcards` array for PUT /api/dashboard/:id from card specs + their ids.

    New dashcards use negative placeholder ids (Metabase assigns real ones on save).
    """
    dashcards = []
    for index, (card_id, card) in enumerate(zip(card_ids, cards)):
        layout = card.get("layout", {})
        dashcards.append(
            {
                "id": -(index + 1),
                "card_id": card_id,
                "row": layout.get("row", 0),
                "col": layout.get("col", 0),
                "size_x": layout.get("size_x", 6),
                "size_y": layout.get("size_y", 6),
                "series": [],
                "parameter_mappings": [],
                "visualization_settings": {},
            }
        )
    return dashcards


def load_specs(specs_dir: Path = SPECS_DIR) -> dict:
    """Load config.yml + each referenced dashboard spec into a single dict.

    Returns {database, collection, dashboards: [{name, description, cards: [...]}, ...]}.
    """
    config = yaml.safe_load((specs_dir / "config.yml").read_text())
    dashboards = []
    for filename in config["dashboards"]:
        spec = yaml.safe_load((specs_dir / filename).read_text())
        _validate_dashboard_spec(spec, filename)
        dashboards.append(spec)
    return {
        "database": config["database"],
        "collection": config["collection"],
        "dashboards": dashboards,
    }


def _validate_dashboard_spec(spec: dict, filename: str) -> None:
    if "name" not in spec or "cards" not in spec:
        raise ValueError(f"{filename}: dashboard spec needs 'name' and 'cards'.")
    names = [c.get("name") for c in spec["cards"]]
    if not all(names):
        raise ValueError(f"{filename}: every card needs a 'name'.")
    if len(names) != len(set(names)):
        raise ValueError(f"{filename}: duplicate card names {names}.")
    for card in spec["cards"]:
        if not card.get("query"):
            raise ValueError(f"{filename}: card '{card.get('name')}' needs a 'query'.")


# --------------------------------------------------------------------------------------------
# Metabase REST client.
# --------------------------------------------------------------------------------------------
class MetabaseClient:
    """Thin idempotent wrapper over the Metabase REST API."""

    def __init__(
        self,
        base_url: str,
        *,
        session_token: Optional[str] = None,
        api_key: Optional[str] = None,
        http: Optional[httpx.Client] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session_token = session_token
        self.api_key = api_key
        self._http = http or httpx.Client(base_url=self.base_url, timeout=30.0)

    # -- auth ---------------------------------------------------------------------------------
    @classmethod
    def from_env(cls, http: Optional[httpx.Client] = None) -> "MetabaseClient":
        base_url = os.getenv("MB_URL", "http://localhost:3001")
        api_key = os.getenv("MB_API_KEY")
        client = cls(base_url, api_key=api_key, http=http)
        if not api_key:
            username = os.getenv("MB_USERNAME")
            password = os.getenv("MB_PASSWORD")
            if not (username and password):
                raise MetabaseError(
                    "Set MB_API_KEY, or MB_USERNAME + MB_PASSWORD, to authenticate to Metabase."
                )
            client.login(username, password)
        return client

    def login(self, username: str, password: str) -> None:
        resp = self._http.post(
            f"{self.base_url}/api/session", json={"username": username, "password": password}
        )
        if resp.status_code >= 400:
            raise MetabaseError(f"Login failed ({resp.status_code}): {resp.text}")
        self.session_token = resp.json()["id"]

    def _headers(self) -> dict:
        if self.api_key:
            return {"x-api-key": self.api_key}
        if self.session_token:
            return {"X-Metabase-Session": self.session_token}
        raise MetabaseError("Not authenticated (no API key or session token).")

    # -- low-level ----------------------------------------------------------------------------
    def request(self, method: str, path: str, **kwargs) -> Any:
        resp = self._http.request(
            method, f"{self.base_url}{path}", headers=self._headers(), **kwargs
        )
        if resp.status_code >= 400:
            raise MetabaseError(f"{method} {path} -> {resp.status_code}: {resp.text}")
        if resp.content:
            return resp.json()
        return None

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, json: dict) -> Any:
        return self.request("POST", path, json=json)

    def put(self, path: str, json: dict) -> Any:
        return self.request("PUT", path, json=json)

    # -- databases ----------------------------------------------------------------------------
    def find_database_id(self, name: str) -> Optional[int]:
        data = self.get("/api/database")
        rows = data.get("data", data) if isinstance(data, dict) else data
        for db in rows:
            if db.get("name") == name:
                return db["id"]
        return None

    def create_database(self, name: str, details: dict) -> int:
        body = {"name": name, "engine": "postgres", "details": details}
        return self.post("/api/database", body)["id"]

    # -- collections --------------------------------------------------------------------------
    def ensure_collection(self, name: str) -> int:
        for col in self.get("/api/collection"):
            if col.get("name") == name and not col.get("archived"):
                return col["id"]
        return self.post("/api/collection", {"name": name})["id"]

    def _collection_items(self, collection_id: int, model: str) -> list[dict]:
        data = self.get(f"/api/collection/{collection_id}/items", params={"models": model})
        return data.get("data", []) if isinstance(data, dict) else data

    # -- cards --------------------------------------------------------------------------------
    def upsert_card(self, card: dict, database_id: int, collection_id: int) -> int:
        payload = build_card_payload(card, database_id, collection_id)
        existing = {c["name"]: c["id"] for c in self._collection_items(collection_id, "card")}
        if card["name"] in existing:
            card_id = existing[card["name"]]
            self.put(f"/api/card/{card_id}", payload)
            return card_id
        return self.post("/api/card", payload)["id"]

    # -- dashboards ---------------------------------------------------------------------------
    def upsert_dashboard(self, spec: dict, collection_id: int, card_ids: list[int]) -> int:
        existing = {
            d["name"]: d["id"] for d in self._collection_items(collection_id, "dashboard")
        }
        meta = {
            "name": spec["name"],
            "description": spec.get("description", "").strip() or None,
            "collection_id": collection_id,
        }
        if spec["name"] in existing:
            dashboard_id = existing[spec["name"]]
        else:
            dashboard_id = self.post("/api/dashboard", meta)["id"]
        body = {**meta, "dashcards": build_dashcards(card_ids, spec["cards"])}
        self.put(f"/api/dashboard/{dashboard_id}", body)
        return dashboard_id

    def get_dashboard(self, dashboard_id: int) -> dict:
        return self.get(f"/api/dashboard/{dashboard_id}")


# --------------------------------------------------------------------------------------------
# High-level provision / export.
# --------------------------------------------------------------------------------------------
def _pg_details_from_env() -> dict:
    """Postgres connection details for --create-database, from the pipeline's PG_* env."""
    password = os.getenv("PG_PASSWORD") or os.getenv("POSTGRES_PASSWORD")
    if not password:
        raise MetabaseError("PG_PASSWORD (or POSTGRES_PASSWORD) must be set to create the DB.")
    return {
        # Default to the compose service name: Metabase connects over the docker network.
        "host": os.getenv("MB_PG_HOST", "postgres"),
        "port": int(os.getenv("MB_PG_PORT", "5432")),
        "dbname": os.getenv("PG_DB", "danang"),
        "user": os.getenv("PG_USER", "danang"),
        "password": password,
        "ssl": False,
    }


def provision(client: MetabaseClient, specs: dict, *, create_database: bool = False) -> dict:
    """Create/update the collection, cards and dashboards described by `specs`."""
    database_id = client.find_database_id(specs["database"])
    if database_id is None:
        if create_database:
            database_id = client.create_database(specs["database"], _pg_details_from_env())
            logger.info("Created Postgres database connection '%s' (id=%s).",
                        specs["database"], database_id)
        else:
            raise MetabaseError(
                f"Metabase database '{specs['database']}' not found. Add it in the UI, or pass "
                "--create-database to create it from the PG_* env vars."
            )
    collection_id = client.ensure_collection(specs["collection"])
    logger.info("Collection '%s' id=%s, database id=%s", specs["collection"],
                collection_id, database_id)

    result = {"database_id": database_id, "collection_id": collection_id, "dashboards": {}}
    for dash in specs["dashboards"]:
        card_ids = [
            client.upsert_card(card, database_id, collection_id) for card in dash["cards"]
        ]
        dashboard_id = client.upsert_dashboard(dash, collection_id, card_ids)
        result["dashboards"][dash["name"]] = dashboard_id
        logger.info("Provisioned dashboard '%s' (id=%s, %d cards).",
                    dash["name"], dashboard_id, len(card_ids))
    return result


def dashboard_to_spec(dashboard: dict) -> dict:
    """Convert a live /api/dashboard payload back into our YAML spec shape."""
    cards = []
    for dc in dashboard.get("dashcards", []) or dashboard.get("ordered_cards", []):
        card = dc.get("card") or {}
        native = (card.get("dataset_query") or {}).get("native") or {}
        if not native.get("query"):
            continue  # skip text/non-native cards
        cards.append(
            {
                "name": card.get("name"),
                "display": card.get("display", "table"),
                "query": native["query"],
                "layout": {
                    "row": dc.get("row", 0), "col": dc.get("col", 0),
                    "size_x": dc.get("size_x", 6), "size_y": dc.get("size_y", 6),
                },
            }
        )
    return {
        "name": dashboard.get("name"),
        "description": (dashboard.get("description") or "").strip(),
        "cards": cards,
    }


def export(client: MetabaseClient, specs: dict, specs_dir: Path = SPECS_DIR) -> list[Path]:
    """Pull the live dashboards back into their YAML spec files (round-trips provisioning)."""
    collection_id = client.ensure_collection(specs["collection"])
    live = {d["name"]: d["id"] for d in client._collection_items(collection_id, "dashboard")}
    config = yaml.safe_load((specs_dir / "config.yml").read_text())
    written = []
    for filename, dash in zip(config["dashboards"], specs["dashboards"]):
        if dash["name"] not in live:
            logger.warning("Dashboard '%s' not found live; skipping export.", dash["name"])
            continue
        full = client.get_dashboard(live[dash["name"]])
        path = specs_dir / filename
        path.write_text(
            yaml.safe_dump(dashboard_to_spec(full), sort_keys=False, allow_unicode=True)
        )
        written.append(path)
        logger.info("Exported '%s' -> %s", dash["name"], path)
    return written


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Metabase dashboards-as-code.")
    parser.add_argument("command", choices=["provision", "export", "validate"])
    parser.add_argument("--create-database", action="store_true",
                        help="Create the Postgres DB connection from PG_* env if missing.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    specs = load_specs()

    if args.command == "validate":
        print(f"OK: {len(specs['dashboards'])} dashboard(s), "
              f"{sum(len(d['cards']) for d in specs['dashboards'])} card(s).")
        return 0

    client = MetabaseClient.from_env()
    if args.command == "provision":
        provision(client, specs, create_database=args.create_database)
    else:
        export(client, specs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
