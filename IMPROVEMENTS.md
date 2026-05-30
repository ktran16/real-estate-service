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

4. **dbt source freshness + tests in CI with seed data.** *(M)* — ✅ **done.** Added a
   `freshness:` block on `raw_listings` and `dbt/seeds/*` gated to a `ci` target; CI now builds
   marts/tests on realistic seeded rows (PASS=39). The freshness check now runs on its own
   **monitoring schedule** — a `source_freshness_check` Dagster job (daily 06:00) that raises on a
   stale source → Slack via the failure sensor. See `PROPOSALS.md` P1 #4.

5. **Incremental publish to Postgres.** *(M)* — ✅ **done (atomic swap).** `publish_to_postgres`
   builds `<mart>__staging` tables then swaps all marts in one native Postgres transaction
   (`postgres_execute`), so Metabase never reads an empty/half-published table. See
   `PROPOSALS.md` P1 #5. (A true upsert/merge remains a future option if marts grow large.)

6. **Geocoding robustness.** *(M)* — ✅ **done.** Tiered `confidence` persisted + a
   `regeocode_low_confidence()` pass that upgrades centroid rows (retry/backoff already via
   `SafeHTTPClient`). Wired into `weekly_maintenance`. See `PROPOSALS.md` P1 #6.

7. **Scheduled `rescrape`.** *(S)* — ✅ **done.** `pipeline/rescraper.rescrape_active_listings`
   (CLI + Dagster `weekly_maintenance` job, Sunday 05:00) marks vanished listings inactive and
   records price changes. See `PROPOSALS.md` P1 #7.

## P2 — Platform & observability

8. **dagster-dbt integration.** *(M)* — ✅ **done.** `daily_refresh` is an asset graph
   (`scraped_listings` → `geocoded_raw` → `@dbt_assets` per-model → `published_marts`); dbt tests
   are asset checks; no subprocess (op jobs use `DbtCliResource`). Verified end-to-end against the
   live stack. See `PROPOSALS.md` P2 #8.

9. **Durability.** *(S)* — ✅ **done.** Back up the irreplaceable **DuckDB** store
   (`backup_duckdb`, `EXPORT DATABASE` + rotation) via a `backup_database` op in
   weekly_maintenance; pinned Metabase + Postgres image digests. (PG is a regenerable serving
   copy, so it needs no dump.) See `PROPOSALS.md` P2 #9.

10. **Observability.** *(S)* — ✅ **done.** `check_mart_health` (fails + Slack-alerts if a
    critical mart is empty, before publish) plus **structured JSON logging** (`utils/logging`,
    opt-in via `LOG_JSON`) and a **drop-detection sensor** (`mart_drop_alert`): each healthy run
    snapshots mart row counts to `mart_row_history`, and a `run_status_sensor` Slack-alerts when a
    mart shrinks ≥30% vs the prior run (catches partial breaks the empty-guard misses). See
    `PROPOSALS.md` P2 #10.

## P3 — Engineering hygiene

11. **More tests.** *(M)* — ✅ **done.** Integration test for `publish_to_postgres`
    (testcontainers Postgres — atomic swap, no leftover staging; self-skips without Docker),
    loader + price-tracker unit tests (new-obs / price-change / rollback paths), and Typer CLI
    smoke tests. See `PROPOSALS.md` P3 #11.
12. **Typing + pre-commit.** *(S)* — ✅ **done.** mypy clean across 23 files (config + CI step)
    and a `.pre-commit-config.yaml` (ruff + mypy). See `PROPOSALS.md` P3 #12.
13. **Config consolidation.** *(S)* Move the `PG_*` and scrape settings into `config.py`
    `Settings` (single source of truth) instead of reading `os.getenv` in `orchestration.py`.

## P4 — Product / analytics

14. **Richer marts:** *(M)* — ✅ **done.** Four new dbt marts (published to Postgres for
    Metabase): `price_per_sqm_by_ward` (p25/median/p75/p90 per ward), `listing_days_on_market`
    (per-listing DOM + bucket), `listing_velocity` (weekly new-vs-removed per district), and
    `broker_concentration` (broker share + top-account share per district). +schema tests + 3
    singular tests (percentile ordering, non-negative DOM, shares in [0,1]) → `dbt build PASS=61`.
    See `PROPOSALS.md` P4 #14.
15. **Alerting on deals.** *(M)* — ✅ **done.** `deals` dbt mart flags active listings priced
    ≥20% below their comparable district benchmark price-per-sqm (`deal_discount_threshold` /
    `deal_min_comparables` dbt vars), published to Postgres. `pipeline/deals` + a Dagster
    `deals_alert` `run_status_sensor` Slack-alerts on *new* deals (diffed against a `deal_alerts`
    tracking table so each fires once). +7 unit tests + a singular dbt test. See `PROPOSALS.md`
    P4 #15.
16. **Dashboards as code.** *(M)* — ✅ **done.** Three dashboards (Market Overview / Trends &
    Activity / Brokers & Deals) defined as version-controlled YAML specs under
    `metabase/dashboards/` and applied via the Metabase REST API by `metabase/provision.py`
    (idempotent provision + `export` round-trip + `validate`). OSS Metabase has no EE
    serialization API, so this uses the REST API instead. Removes the manual UI build step;
    +13 unit tests (mocked httpx). See `PROPOSALS.md` P4 #16.
