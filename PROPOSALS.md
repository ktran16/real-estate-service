# Proposals — research & solutions for the IMPROVEMENTS items

Companion to `IMPROVEMENTS.md`. This document records the **researched solutions** and the
**decisions/trade-offs** behind the work done in this round, and lays out concrete plans for
the items that remain. Items already shipped are marked ✅ with where the code lives.

---

## P0 #1 — batdongsan.com.vn scraper ✅ (parsing) / 🔬 (live run still blocked)

**Shipped this round** (`src/danang_realestate/scrapers/batdongsan.py`,
`models.NormalizedListing.from_batdongsan`, `tests/test_batdongsan.py`):
- Real Vietnamese **price/area parsing** (`parse_price_vnd`, `parse_area_sqm`) — handles
  `tỷ`/`triệu`, the `2 tỷ 500 triệu` combined form, decimal commas, rent suffixes, and
  `Thỏa thuận` → None. Fully unit-tested (source-agnostic logic).
- **Card extraction** (`parse_listing_cards`) + normalization to `NormalizedListing`, with a
  tolerant multi-selector strategy. Exercised end-to-end via a mocked fetch
  (`tests/test_scraper.py::test_batdongsan_scrape_wiring`).
- **Browser fetch** (`_fetch_rendered`) isolated behind a lazy Playwright import so the
  module + parsers load without the extra; a clear actionable error if it's missing.

**Why the live run can't be validated here:** batdongsan is behind Cloudflare (verified HTTP
403 + JS challenge). Clearing it needs a real browser fingerprint and, for sustained crawls,
residential IPs — neither available in this sandbox.

**Plan to take it live (in priority order):**
1. **Selector re-validation first.** The CSS selectors match batdongsan's *documented* card
   markup but the site rotates classes. Capture one Cloudflare-cleared page, save it as a
   fixture, and diff against `tests/test_batdongsan.py`'s assumptions before any crawl. Treat
   a `parse_listing_cards` returning 0 cards as "selectors stale", not "end of results".
2. **Cloudflare bypass**, cheapest → strongest:
   - `playwright` + `playwright-stealth` (patches the obvious `navigator.webdriver` tells).
   - If still challenged: a maintained anti-detect runtime — `undetected-playwright` or the
     `camoufox`/`patchright` forks — or `curl_cffi` (already an optional extra) for the JSON
     XHR endpoints *if* one exists behind the page (worth probing; far cheaper than a browser).
   - **Residential/mobile proxy pool** for sustained runs (datacenter IPs get hard-blocked).
     Make the proxy a `settings.batdongsan_proxy_url` (treated as a secret like PG_PASSWORD).
3. **Politeness & resilience:** randomized 5–15 s delays, exponential backoff on challenge
   pages, a per-run page cap (already `MAX_PAGES`), and persist the last-seen page so a run can
   resume. Cache cleared Cloudflare cookies between requests within a session.
4. **Separate Dagster op/job.** Browser containers are heavy and failure-prone — don't couple
   them to `daily_refresh`. Add a `batdongsan_refresh` job (its own schedule, longer timeout,
   `SCRAPE_SOURCE=batdongsan`) that reuses the loader/geocoder/dbt path unchanged. The image
   needs `uv sync --extra playwright && playwright install --with-deps chromium`.
5. **Then `mogi` / `alonhadat`** via the same registry (`scrapers/__init__.py`); the schema
   already anticipates multiple `source` values.

**Risk/ethics note:** check `robots.txt` and ToS; prefer the lowest request rate that yields
fresh data. Geocoding fills coordinates batdongsan doesn't expose, so district accuracy depends
on the address string parsing + the existing Nominatim/Goong tiers.

---

## P0 #2 — Secrets management ✅ (done this round)

- Postgres password removed from all committed files. Both compose stacks now require
  `POSTGRES_PASSWORD` from an untracked `.env` (`${POSTGRES_PASSWORD:?…}` — the stack refuses
  to start without it). Verified: `docker compose config` errors without it, validates with it.
- Postgres host port bound to **loopback only** (`127.0.0.1:5433:5432`) — no longer reachable
  from other hosts.
- Config consolidated into `config.Settings` (`pg_host/port/db/user/password/schema`,
  `slack_webhook_url`) — this also closes **P3 #13**. `pg_password` accepts `PG_PASSWORD` *or*
  `POSTGRES_PASSWORD` (one secret to define). `orchestration.py` reads `settings` instead of
  `os.getenv`, and `publish_to_postgres` fails fast with a clear message if the password is unset.
- `.env.example` documents the required secret + the per-compose-dir `.env` placement.

**Remaining (ops, not code):** rotate the old `danang` password before anything leaves
localhost; if you later move off localhost, switch to Docker secrets / a secrets manager and
give Metabase its own least-privilege read-only PG role.

---

## P0 #3 — API drift alerting ✅ (done this round)

- New Dagster `schema_drift_check` job + **weekly schedule** (`schema_drift_schedule`, Mon
  04:00 Asia/Ho_Chi_Minh, default RUNNING). The `check_api_schema` op runs the existing
  `validate_schema` and now **raises on drift** so the run fails (previously it only logged).
- A `run_failure_sensor` (`pipeline_failure_alert`, default RUNNING) watches both
  `daily_refresh` and `schema_drift_check` and posts to Slack via `alerting.post_slack`.
- `alerting.post_slack` no-ops safely when `SLACK_WEBHOOK_URL` is unset and never raises
  (alerting can't take down the pipeline it reports on). Unit-tested.

**Optional next:** add email (SMTP) as a second channel; add a success heartbeat (a weekly
"all green" message) so silence is distinguishable from a broken sensor.

---

## P1 — Data quality

**#4 dbt source freshness + CI seed data. ✅ done.** Added a `freshness:` block + `loaded_at_field:
scraped_at` on the `raw_listings` source (warn >36 h, error >7 d). Shipped `dbt/seeds/*.csv`
(12 listings + price history + geocode cache) **gated to a `ci` target** (`+enabled: target.name
== 'ci'`) so they never clobber dev/prod (which use `dbt run`). CI now `dbt seed --target ci`
then `dbt build --target ci` → marts/tests run on realistic rows (PASS=39): broker detection
(account with 5 listings), price-change filtering (5% threshold), and the geocode-cache coalesce
are all genuinely exercised. *Remaining:* wire `dbt source freshness` into a monitoring schedule
(it can't run in CI — seed timestamps are fixed). Reuse `alerting.post_slack` for the alert.

**#5 Atomic publish to Postgres. ✅ done.** `publish_to_postgres` now builds each mart into a
`<mart>__staging` table, then swaps ALL marts into place in **one native Postgres transaction**
(`postgres_execute`: `BEGIN; DROP old; ALTER … RENAME staging→final; … COMMIT`). Metabase always
reads either the full previous marts or the full new ones — never an empty/half-published table.
Verified end-to-end against the live PG (104 listings, 0 leftover staging tables). *Future:* a
true upsert/merge if the marts ever grow large enough that a full rewrite is too heavy.

**#6 Geocoding robustness. ✅ done.** Retry/backoff + request delay already came free via
`SafeHTTPClient` (tenacity, 3 attempts) which Goong uses. Added **tiered confidence**
(`CONFIDENCE`: nominatim 0.9 / goong 0.8 / centroid 0.3 / none 0.0) persisted to
`geocode_cache.confidence`, and `Geocoder.regeocode_low_confidence()` which drops + re-resolves
low-confidence (centroid) entries and upgrades the matching `raw_listings` coords when a better
tier resolves. Unit-tested. Wired as a Dagster op in `weekly_maintenance`. *Future:* Goong batch
geocoding for throughput.

**#7 Scheduled `rescrape`. ✅ done.** Extracted the rescrape core into
`pipeline/rescraper.rescrape_active_listings` (offline → `is_active=false`; price change →
history row + price update), refactored the CLI onto it, and added a `weekly_maintenance` Dagster
job + Sunday-05:00 schedule (rescrape → regeocode → dbt → publish). Keeps `is_active`/
`price_changes` honest without slowing the daily path. Unit-tested (4 cases).

## P2 — Platform & observability

**#8 dagster-dbt. ✅ done.** `daily_refresh` is now an **asset graph**: `scraped_listings` →
`geocoded_raw` (produces the `raw/*` dbt source tables) → `@dbt_assets` (every dbt model is its
own asset) → `published_marts`. dbt tests surface as **asset checks** in the Dagster UI; lineage
is end-to-end. The op-based `weekly_maintenance`/`schema_drift_check` jobs share the same step
helpers and invoke dbt via `DbtCliResource` (no `subprocess` anywhere). The manifest is read at
import — generated by `prepare_if_dev()` under `dagster dev`, by an explicit CI `dbt parse` step,
and by an import-time self-heal (`dbt parse`) for fresh containers/tests. **Verified** by
materializing the full asset job against the live stack (all dbt models + checks passed, marts
published atomically, RUN_SUCCESS).

**#9 Durability. ✅ done.** Reframed to back up the **DuckDB working store** (raw listings +
price history + geocode cache) — that's the irreplaceable data; the Postgres marts are a
regenerable serving copy (`publish_to_postgres`), so they need no separate dump (restore = re-run
the pipeline). Added `pipeline/backup.backup_duckdb` (`EXPORT DATABASE` → timestamped, rotated
snapshots; restore via `IMPORT DATABASE`) wired as a `backup_database` op in `weekly_maintenance`
(`BACKUPS_DIR`/`BACKUP_KEEP` env). Pinned Metabase + Postgres images by **digest**. Unit-tested
(snapshot round-trips via IMPORT; rotation) + smoke-tested against the real DB.

**#10 Observability.** ✅ **done.** `check_mart_health` (between dbt and publish in both
pipelines) logs all mart row counts and **raises if a critical mart (`listings`,
`price_by_district`) is empty** — so a broken upstream can't overwrite Metabase with empty marts,
and the failure trips the Slack sensor. Added this round:
- **Structured JSON logging** (`utils/logging.JsonLogFormatter` + `configure_logging`): one JSON
  object per line, includes `extra=` fields and exception/stack info. Opt-in via `LOG_JSON=1`
  (CLI keeps Rich output by default); idempotent so repeated config calls don't double-log.
- **Drop-detection sensor** (`pipeline/observability` + `orchestration.mart_drop_alert`): every
  *healthy* mart-health check snapshots row counts into a `mart_row_history` DuckDB table;
  `detect_row_drops` compares the two most recent run snapshots and a `run_status_sensor` (on
  `daily_refresh`/`weekly_maintenance` SUCCESS) Slack-alerts when a mart shrinks ≥30% vs the
  prior run — catching a *partial* break that still emits some rows (which the empty-guard would
  miss). Threshold is configurable; growth and sub-threshold churn are ignored. Unit-tested.
*Remaining (optional):* Dagster run metrics/asset metadata; an email channel.

## P3 — Hygiene

**#11 More tests. ✅ done.** Shipped: a `publish_to_postgres` **integration test** via
**testcontainers Postgres** (`tests/test_publish_postgres_integration.py`) that publishes real
DuckDB marts, asserts the rows land in PG and that re-publishing atomically swaps with **no
leftover `*__staging`** tables (plus the password-required guard); it self-skips when
Docker/`testcontainers`/`dagster` aren't available so the default suite stays hermetic. Plus
**loader** tests (`tests/test_loader.py`: new-observation, price-change update, unchanged-noop,
and transaction **rollback** on a failing history insert), **price-tracker** tests
(`tests/test_price_tracker.py`: classification + `get_existing_prices` bulk/null), and **Typer CLI
smoke tests** (`tests/test_cli.py`: help, per-command help, `transform` exit code, scrape wiring).
`testcontainers[postgres]` + `psycopg2-binary` added to the dev group.
**#12 Typing + pre-commit: ✅ done.** Added `mypy` (config in pyproject: `files=["src"]`,
`ignore_missing_imports`, `no_implicit_optional`) — fixed the 18 type errors (implicit-Optional
params, BS4 attr coercion, dict.get on Optional keys, DuckDB `fetchone()[0]` via a `_scalar_int`
helper) so it's clean across 23 files, and wired a CI `mypy` step + `.pre-commit-config.yaml`
(ruff + mypy).
**#13 Config consolidation:** ✅ done as part of P0 #2 (PG_* now in `Settings`).

## P4 — Product/analytics (proposed)

**#14 Richer marts:** price-per-sqm percentiles by ward, days-on-market, new-vs-removed
velocity, broker-concentration trends. **#15 Deals alerting:** a `deals` mart flagging listings
materially below their ward median + a notification sensor. **#16 Dashboards as code:** export
Metabase dashboards via the serialization API so they're version-controlled (removes the only
manual setup step).

---

## Suggested next sequence
1. ✅ ~~**P1 #4** (seed data + freshness)~~ — done.
2. ✅ ~~**P1 #5** (atomic publish)~~ — done.
3. ✅ ~~**P1 #6** (geocoding confidence + re-geocode)~~ — done.
4. ✅ ~~**P1 #7** (scheduled rescrape)~~ — done.
5. ✅ ~~**P2 #8** (dagster-dbt)~~ — done.
6. ✅ ~~**P2 #9** (durability: DuckDB backups + pin digests)~~ — done.
7. ✅ ~~**P3 #12** (mypy + pre-commit)~~ — done.
8. ✅ ~~**P2 #10 remainder** (structured logging + drop-detection sensor → `post_slack`)~~ — done.
9. ✅ ~~**P3 #11** (integration tests via testcontainers; loader/price-tracker unit tests)~~ — done.
10. **P4** (richer marts, deals alerting, dashboards-as-code). *(M)*
11. **P0 #1 live run** — once you have a proxy + a captured fixture to validate selectors. *(L)*
