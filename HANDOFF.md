# HANDOFF — Da Nang Real Estate Analytics

## Goal
Review the Phase 1 implementation against best practices and identify next steps. The
immediate concrete task that was executed: fix the four highest-priority bugs found during
the review. Longer-term goal is to complete Phase 1 (scrape → geocode → dbt marts →
Metabase dashboards) per `danang_realestate_spec_v2.md`.

## Status
- [x] Full codebase review (src/, dbt/, tests/) completed
- [x] Bug #1 — invalid f-string format spec in price_tracker (crashed on every price change)
- [x] Bug #2 — deprecated `datetime.utcnow()` replaced with `utils/timeutil.utcnow()`
- [x] Bug #3 — double `conn.close()` in `refresh_wards`
- [x] Bug #4 — wired `.env` scrape config into `SafeHTTPClient`
- [x] Verified: imports OK, tests pass, price-change path runtime-tested
- [x] Cleaned up `utcnow()` in test files
- [x] dbt `_sources.yml` + `source()` refs + `schema.yml` tests (dbt build PASS=32/32)
- [x] Made tests hermetic (mock Nominatim + HTTP) + added scraper tests (11 tests pass)
- [x] `git init` + initial commit (on `master`), dev deps (pytest/ruff), ruff+pytest config,
      `.gitignore`, `.env.example`, GitHub Actions CI (ruff + pytest + dbt parse)
- [x] Renamed branch `master` → `main`, added remote
      `git@github.com:ktran16/real-estate-service.git`, pushed `main` ← CURRENTLY HERE
- [ ] (Next) Confirm CI runs green on GitHub (gh not authed in this env — check Actions tab)
- [ ] (Next) Metabase dashboards to close out Phase 1 (Market Overview / Map / Trend)

## Key Context
- Stack: Python 3.11+, `uv`, Typer CLI, Pydantic v2, DuckDB, dbt-duckdb. Entry point
  `danang-realestate = danang_realestate.cli:app`.
- Pipeline: `scrape` (nhatot/chotot gateway API) → normalize (`models.NormalizedListing.from_nhatot`)
  → `load_listings` (upsert + price-history) → `geocode` (cache → Nominatim → district centroid)
  → `transform` (dbt staging→intermediate→marts).
- DB timestamp columns are tz-NAIVE `TIMESTAMP`. New `utils/timeutil.utcnow()` deliberately
  returns naive UTC (`datetime.now(timezone.utc).replace(tzinfo=None)`) to match — do not
  switch to tz-aware without also changing the schema.
- Pitfall fixed: `detect_price_changes` runs INSIDE `load_listings`' transaction, so any
  exception there (was the f-string bug) rolls back the whole load.
- `SafeHTTPClient` now reads `settings.scrape_delay_min/max` and appends
  `settings.scrape_user_agent` to its UA rotation pool. Previously `.env` config was dead.
- No git repo in this folder. dbt models reference raw tables by bare name (no `source()`),
  and there are no dbt schema tests yet — flagged as next steps, not done.

## Files Touched
- `src/danang_realestate/pipeline/price_tracker.py` — fixed crashing f-string (compute `pct_str`)
- `src/danang_realestate/utils/timeutil.py` — NEW, `utcnow()` helper
- `src/danang_realestate/utils/__init__.py` — export `utcnow`
- `src/danang_realestate/cli.py` — use `utcnow()` (2 sites), removed unused `datetime` import,
  removed duplicate `conn.close()`
- `src/danang_realestate/scrapers/nhatot.py` — use `utcnow()`, removed unused import
- `src/danang_realestate/pipeline/geocoder.py` — use `utcnow()`, removed unused import
- `src/danang_realestate/utils/http.py` — wire `settings` delays + UA into `SafeHTTPClient`

## Verification
```bash
source .venv/bin/activate   # or use: uv run
python -c "import danang_realestate.cli"          # imports OK
python -m unittest discover -s tests -p "test_*.py" -v   # 7/7 pass
```
Remaining deprecation warnings are from `tests/test_geocoder.py:56` and
`tests/test_normalizer.py:59` (test fixtures still call `datetime.utcnow()`), not src.

## Next Actions
1. Create a GitHub remote and `git push -u origin master`; confirm the CI workflow
   (`.github/workflows/ci.yml`) runs green there (ruff + pytest + dbt parse).
2. Stand up Metabase dashboards (Market Overview / Map / Trend) on the marts — Phase 1 finish.
3. (Future, per spec) Phase 2 `batdongsan.com.vn` scraper behind `BaseScraper`; Goong geocoding
   (the `goong_api_key` config exists but is unused).

### Verify current state (mirrors CI)
```bash
uv sync --dev
uv run ruff check .                                   # All checks passed
uv run pytest -q                                      # 11 passed
cd dbt && uv run dbt parse --profiles-dir .           # parses clean
cd dbt && uv run dbt build --profiles-dir .           # PASS=32 (needs data/danang.duckdb)
```

## Open Questions
- None blocking. All planned next-step items for this session are done. Remaining work
  (push to remote, Metabase) needs user input on the GitHub remote and Metabase host.

## Cross-Agent Notes
- "Skills" referenced here (handoff-skill, code-review, run, verify) are Claude Code slash
  commands; other agents can ignore. No MCP servers used. `rtk` is a token-saving CLI proxy
  wrapper around shell commands (transparent, see ~/.claude/RTK.md).
