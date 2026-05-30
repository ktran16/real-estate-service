# Improvement Plan — Da Nang Real Estate Analytics

Current state (Phase 1 complete): nhatot scrape → DuckDB → geocode (Nominatim/Goong/centroid)
→ dbt marts → publish to Postgres → Metabase, orchestrated by an always-on Dagster stack
(daily 03:00 schedule). CI runs ruff + pytest (17) + Dagster validate + dbt build (39 tests).

This plan is ordered by leverage. Each item notes the rough effort.

> **Progress (2026-05-30):** P0 #2 (secrets) and #3 (API drift alerting) are **done**, and
> P0 #1's parsing/normalization half **shipped** (only the Cloudflare/browser live run remains,
> which can't be validated without a proxy). Researched solutions + the live-run plan for #1 and
> concrete designs for the P1–P4 items are in **`PROPOSALS.md`**.

## P0 — Highest leverage

1. **Real `batdongsan.com.vn` scraper (Playwright).** *(L)* — 🟡 **parsing shipped; live run
   blocked.** Vietnamese price/area parsing, card extraction, `from_batdongsan`, and the
   Playwright fetch scaffold are implemented + unit-tested (`scrapers/batdongsan.py`,
   `tests/test_batdongsan.py`). The Cloudflare-cleared live crawl needs a real browser +
   residential proxy — see `PROPOSALS.md` "P0 #1" for the bypass/selector-validation plan.
   The placeholder exists and the registry/`--source` plumbing is ready. batdongsan is
   Cloudflare-protected (verified HTTP 403 + challenge), so it needs a real browser:
   - Add a `playwright`-based scraper: launch chromium (stealth UA/viewport), load the
     Da Nang listing pages, clear the JS challenge, paginate, parse listing cards.
   - Add `NormalizedListing.from_batdongsan(...)` mirroring `from_nhatot`.
   - Run it from a separate Dagster op/job (browser containers are heavier) and likely add
     residential-proxy support for sustained runs. Reuses loader/geocoder/dbt unchanged.
   - Then add `mogi` / `alonhadat` (the schema already anticipates these sources).

2. **Secrets management.** *(S)* — ✅ **done.** Password removed from compose (required from
   untracked `.env`, `${POSTGRES_PASSWORD:?…}`), PG port bound to `127.0.0.1`, PG config
   consolidated into `config.Settings` (also closes P3 #13), fail-fast if the secret is unset.
   *Remaining ops task:* rotate the old `danang` password. (Original notes below.)
   Postgres credentials are the literal `danang/danang/danang` in `metabase/docker-compose.yml`
   and flow into `orchestration` via env; the DuckDB→PG `ATTACH` DSN embeds the password in
   SQL. Move to a `.env`/Docker secrets (`POSTGRES_PASSWORD` from env, not committed), use a
   strong password, and don't publish 5433 to all interfaces (bind 127.0.0.1). Add `goong_api_key`
   handling docs. Rotate before anything leaves localhost.

3. **API drift alerting.** *(S)* — ✅ **done.** Added a `schema_drift_check` Dagster job +
   weekly schedule and a `run_failure_sensor` (`pipeline_failure_alert`) that posts to Slack
   (`alerting.post_slack`, `SLACK_WEBHOOK_URL`) for both it and `daily_refresh`. The drift op
   now raises on drift so failures actually fire. (Original notes below.)
   There's a `validate_schema` command but nothing runs it. Add a Dagster job + schedule (e.g.
   weekly) and wire failure notifications (Dagster `run_failure_sensor` → Slack/email) for both
   it and `daily_refresh`, so a silent scrape break or nhatot schema change is noticed.

## P1 — Data quality & correctness

4. **dbt source freshness + tests in CI with seed data.** *(M)*
   Add `dbt source freshness` (raw_listings.scraped_at) to catch a stalled pipeline. Ship a
   small seed dataset so CI runs the marts/tests against realistic rows (today CI builds on an
   empty schema — it proves SQL executes but not the aggregation logic).

5. **Incremental publish to Postgres.** *(M)*
   `publish_to_postgres` drops+recreates whole tables each run — fine now, but causes brief
   empty windows and won't scale. Switch to upsert/merge (or write to a staging schema then
   atomically swap) so Metabase never reads a half-published table.

6. **Geocoding robustness.** *(M)*
   Add Goong rate limiting + retry/backoff, persist `confidence`, and re-geocode listings that
   only got a district-centroid once a better tier is available. Consider batch geocoding.

7. **Scheduled `rescrape`.** *(S)*
   `daily_refresh` re-scrapes search results (catches price changes for still-listed ads + new
   ones) but doesn't mark vanished listings inactive. Add a `rescrape` op/job (offline + price
   detection) on its own cadence so `is_active` and `price_changes` stay accurate.

## P2 — Platform & observability

8. **dagster-dbt integration.** *(M)*
   Replace the `subprocess uv run dbt` op with `dagster-dbt` so each model is an asset with
   lineage, per-model retries, and test results surfaced in the Dagster UI.

9. **Postgres durability.** *(S)*
   Add scheduled `pg_dump` backups (a Dagster op or sidecar) and document restore. Pin the
   Metabase + Postgres image digests.

10. **Observability.** *(S)* Structured logging, Dagster run metrics, and a freshness/row-count
    sensor that alerts when a mart's row count drops unexpectedly.

## P3 — Engineering hygiene

11. **More tests.** *(M)* Integration test for `publish_to_postgres` (testcontainers Postgres),
    loader/price-tracker unit tests (the price-change path), CLI smoke tests.
12. **Typing + pre-commit.** *(S)* Add mypy to CI and a pre-commit config (ruff + mypy) so
    issues are caught before push.
13. **Config consolidation.** *(S)* Move the `PG_*` and scrape settings into `config.py`
    `Settings` (single source of truth) instead of reading `os.getenv` in `orchestration.py`.

## P4 — Product / analytics

14. **Richer marts:** price-per-sqm percentiles by ward, days-on-market, new-vs-removed
    listing velocity, broker concentration trends.
15. **Alerting on deals:** flag listings priced materially below their ward's median (a "deals"
    mart + a notification sensor).
16. **Dashboards as code:** export Metabase dashboards via the serialization API so they're
    version-controlled and reproducible (removes the only manual setup step).
