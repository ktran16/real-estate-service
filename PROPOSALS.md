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

## P2 — Platform & observability (proposed)

**#8 dagster-dbt.** Replace the `subprocess uv run dbt` op with `dagster-dbt` so each model is an
asset (lineage, per-model retries, test results in the UI). Medium effort; biggest observability
win. Do after #4 so seeds/tests come along for free.

**#9 Postgres durability.** Scheduled `pg_dump` (a Dagster op or sidecar + cron) to a mounted
volume, documented restore, and pin Metabase + Postgres image **digests** (not just tags).

**#10 Observability.** Structured (JSON) logging, Dagster run-metrics, and a row-count freshness
sensor that alerts when a mart's row count drops unexpectedly (reuse `alerting.post_slack`).

## P3 — Hygiene (proposed)

**#11 More tests:** `publish_to_postgres` integration test via testcontainers Postgres;
loader/price-tracker unit tests (the price-change transaction path); a CLI smoke test.
**#12 Typing + pre-commit:** add `mypy` to CI and a `pre-commit` config (ruff + mypy).
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
5. **P2 #8** (dagster-dbt) — observability/lineage; pairs well with the new seeds/tests. *(M)*
6. **P2 #9/#10** (PG backups + pin digests; row-count freshness sensor → `post_slack`). *(S)*
7. **P0 #1 live run** — once you have a proxy + a captured fixture to validate selectors. *(L)*
