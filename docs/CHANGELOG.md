# Changelog

## Unreleased

### Fixed (2026-07-23)

- **API authentication now covers business and administrative routes.** When `AUTH_ENABLED=true`, market, financial, technical, fundamental, factor, realtime, stock-data, historical, and historical-admin endpoints require a valid `X-API-Key` header or `api_key` query parameter. Health, root, and metrics endpoints remain public for monitoring.
- **K-line adjustment factors now come from AmazingData.** Batch synchronization pulls backward-adjustment factors, aligns sparse factor dates to daily/weekly/monthly bars, and repairs existing Parquet files. Missing factors are no longer silently fabricated as `1.0`; `scripts/backfill_kline.py --adj-factor` can be used for an explicit backfill.
- **Daily/weekly/monthly price fields now follow Tushare semantics.** `pre_close`, `change`, and `pct_chg` use each security's prior trading bar; weekly and monthly bars are aggregated from daily data and use the last actual trading date of the period.
- **Technical-analysis responses now return the source bar date.** The response date is read from `date`/`kline_time` and formatted as `YYYYMMDD`, instead of leaking a DataFrame row index such as `131`.

### Added (2026-07-17)

- **Realtime code list now pulled directly from the SDK.** `amazingdata/realtime.py` fetches the full A-share list via `get_code_list("EXTRA_STOCK_A_SH_SZ")` (~5,200 codes, daily-fresh) at startup; the cached `meta/codes.parquet` file is only a fallback. The realtime worker no longer depends on batch's `sync_meta_codes` having run first.

### Changed (2026-07-17)

- **Worker env split per service: `amazingdata/.env` → `realtime.env` + `batch.env`.** Each service now has its own TGW account, so `amazingdata-realtime` and `amazingdata-batch` can run simultaneously (the single-connection constraint is per-account). Templates: `realtime.env.example` / `batch.env.example`; both env files are gitignored. `amazingdata/.env(.example)` removed. The dead `REALTIME_ENABLED` toggle is no longer referenced anywhere.
- **Compose projects separated.** `docker-compose.realtime.yml` / `docker-compose.batch.yml` now declare their own project names (`amazingdata-realtime` / `amazingdata-batch`) so lifecycle commands no longer report each other's containers as orphans.
- **Production API deployment is systemd-based (no Docker).** `scripts/adshare-api.service` runs `/opt/adshare/venv/bin/python -m adshare.main` with `PYTHONPATH=/opt/adshare`; the app reads config from `<repo>/adshare/.env` via pydantic-settings (the unit's `EnvironmentFile` line was removed). Production serves port **8888** with `AUTH_ENABLED=true` + X-API-Key.

### Fixed (2026-07-17)

- `amazingdata/base.Dockerfile` + `bin/build-base.sh`: reference the `AmazingData-1.1.8` wheel (1.0.30 no longer ships in `wheels/`).
- `realtime.Dockerfile` / `batch.Dockerfile`: drop `pip install --no-deps .` — the workspace-root `pyproject.toml` is a hatch workspace and intentionally not installable; packages import from `/app` via `PYTHONPATH`. Also quote pip version specs containing `<` in `batch.Dockerfile` (shell redirection bug: `duckdb>=1.0.0,<2.0` failed the build).
- `amazingdata/realtime.py`: fix `get_settings` NameError → `get_worker_settings()` (crashed on startup).
- `adshare/Dockerfile`: copy the README stub under its real filename (`README.adshare-stub.md`) so hatch's readme validation passes; switch pip index to the Tsinghua mirror.

### Removed (2026-07-17)

- **11 stale one-off scripts deleted from `scripts/`**: `cron_sync_kline_daily.py`, `cron_verify_max_date.py`, `run_sync_kline_daily.sh`, `run_verify_max_date.sh` (superseded by the APScheduler inside `amazingdata/batch.py`), `verify_kline_dates.py`, `verify_kline_quality.py` (one-off debug), `migrate_to_flat_layout.py` (migration done), `ref_sync_runner.py` (included the disabled financial sync), `simulate_backfill_5y.py` (mock-SDK demo), `deploy.sh` (pre-monorepo single-compose deploy), `sync_to_remote.sh` (stale paths; workers no longer need rsync to the server). Kept: `backfill_kline.py`, `backfill_financial.py`, `repair_kline_quality.py`, `sync_reference_data.py`, `adshare-api.service`.

## 2026-06 monorepo split

### Changed

- **Monorepo split: `adshare/` and `amazingdata/` are now independent Python packages.** Each has its own `pyproject.toml` declaring its own runtime dependencies; the repository root `pyproject.toml` is a [hatch workspace](https://hatch.pypa.io/latest/config/workspace/) entry that lists both members and shared dev tools (`pytest`, `ruff`, `mypy`). `pip install -e .` at the workspace root installs both; `pip install -e ./adshare` or `pip install -e ./amazingdata` installs one. Each member owns its Dockerfile + docker-compose.yml.
- **`amazingdata_worker/` package removed.** The single worker process (which mixed realtime subscription and APScheduler batch sync behind `REALTIME_ENABLED` / `SYNC_SCHEDULE_ENABLED` toggles) is replaced by two independent entry points: `amazingdata.realtime` (intraday subscription → Redis + Pub/Sub) and `amazingdata.batch` (after-hours APScheduler → L3 warehouse). Both share `amazingdata.adapters`. Two separate Dockerfiles + compose files, deployed independently.
- **Worker self-contained base image.** `adshare_base/Dockerfile` → `amazingdata/base.Dockerfile`. SDK wheels (AmazingData + tgw) live under `amazingdata/wheels/`; `bin/build-base.sh` updated to point at the new path. Workers depend only on `adshare-base` for SDK + C-extension setup, not on the adshare API image.
- **`Settings` split into two Pydantic classes.** API-only fields (`redis_*`, `historical_*`, `duckdb_*`, `auth_*`, `rate_limit_*`, etc.) live in `adshare.core.config.Settings`; worker-only fields (`ad_*`, `sync_*`, `realtime_enabled`, `maintenance_*`, `amazingdata_local_path`, `index_codes`) live in `amazingdata.config.WorkerSettings`. `WorkerSettings.__getattr__` proxies shared fields transparently, so call sites can use `settings.<field>` regardless of which class they hold.
- **`.env` split into `adshare/.env` and `amazingdata/.env`.** Each package loads its own `.env` via `pydantic-settings` (`env_file` resolved relative to the module path so it works regardless of CWD). `.env.example` templates mirror the split. Shared fields (`REDIS_*`, `HISTORICAL_*`) stay in `adshare/.env` so the API image never needs the SDK login.
- **API docker-compose moved into `adshare/docker-compose.yml`.** Root-level `docker-compose.yml`, `docker-compose.override.yml`, root `Dockerfile`, and root `.env` / `.env.example` removed.
- **`tests/test_historical*.py` fixtures** now return `WorkerSettings` (which exposes both worker fields and shared L3 fields via the `__getattr__` proxy), so batch sync tests work unchanged. Test count unchanged: **326 passed, 2 pre-existing failures** (documented in `docs/refactor-backlog.md`).

- **L3 historical warehouse: scope narrowed to SH/SZ A-share.** The warehouse no longer serves Beijing Stock Exchange codes. `.BJ` rows are filtered at sync time (`_filter_sh_sz_codes`) and on-disk legacy `.BJ.parquet` files are removed by `repair_kline_directory`. `sync_index_component` default index list drops `899050.BJ` (北证50).
- **L3 historical warehouse: adjustment-factor support.** Missing `adj_factor` values are preserved until the AmazingData backward-factor feed is synchronized; scheduled K-line jobs and `scripts/backfill_kline.py --adj-factor` can align real factors to stored daily/weekly/monthly bars.
- **L3 historical warehouse: OHLCV-zero rows auto-marked suspended.** `validate_kline_df` flips `is_suspended=True` and nulls prices for rows where `open=high=low=close=0 && volume=0` to defend against upstream sync failures (e.g. 2026-06-12 weekly batch returned 0 for every stock).
- **L3 financial tables: composite natural key dedup.** `sync_financial` and `repair_financial_table` deduplicate on `(market_code|ts_code, reporting_period, report_type, statement_type, comp_type_code)` to preserve the legitimate multi-version reports (合并/母公司) while removing exact duplicates from re-pulls. `report_type` enum is normalised to `{1, 2, 3, 4}` (SDK has occasionally returned a date string).
- **L3 historical warehouse: flat layout migration.** Per-(period, year, code) files replaced by a single Parquet per (period, code) with all years merged (`A_share/{daily|weekly|monthly}/{code}.parquet`). Sync jobs now pull `[20200101, today]` per code and overwrite the single file. `_metadata.json` moved from per-year to per-period. `kline_file_path()` and `warehouse.kline_dir()` no longer require a `year` argument; the parameter is accepted but ignored for backward compatibility. `sync_kline_daily/weekly/monthly` now accept `from_date`/`to_date` (the legacy `year=` keyword still works). `warehouse.stats()` drops `year_count`, adds `first_date`/`last_date`. New migration script: `python -m scripts.migrate_to_flat_layout [--dry-run] [--keep-old] [--backup-root PATH]`.
- Moved limit-up stock calculation from the market router into `LimitUpService`, using daily K-line data and theoretical limit-up prices.
- Updated Phase 3 development plan status for completed `TechnicalResponse`, `tables`, `limit-up`, and changelog tasks.
- Fixed AmazingData `BaseData` calls so `get_code_list` honors the requested `security_type` and `get_calendar` remains compatible across SDK versions.
- Moved technical analysis orchestration from the router into `TechnicalAnalysisService`.
- Removed generic request-result caching from the AmazingData adapter and limit-up service; Redis is now scoped to real-time/subscription market data only.
- Removed unused generic cache hit/miss Prometheus metrics.

### Added

- `adshare.historical.maintenance` module with idempotent L3 warehouse repair routines (`repair_kline_directory`, `repair_codes_table`, `repair_financial_table`, `repair_all`). Use as `python -m adshare.historical.maintenance {kline|codes|financial|all} [--dry-run]` or via the new `POST /historical/admin/repair` admin endpoint. Each routine is safe to call repeatedly and skips rewrite when nothing changed.
- `MAINTENANCE_SCHEDULE_ENABLED` (default off) wires the repair routines into APScheduler as weekly defensive crons after the regular sync jobs. Configurable via `MAINTENANCE_KLINE_*` and `MAINTENANCE_FINANCIAL_*` env vars (see `.env.example`) (`repair_kline_directory`, `repair_codes_table`, `repair_financial_table`, `repair_all`). Use as `python -m adshare.historical.maintenance {kline|codes|financial|all} [--dry-run]` or via the new `POST /historical/admin/repair` admin endpoint. Each routine is safe to call repeatedly and skips rewrite when nothing changed.
- Added service contract tests for limit-up local metadata/K-line hits, AmazingData fallback without login checks, remote K-line persistence, board/ST filtering, limit-price rounding, partial K-line batch failures, and ladder generation.
- Added adapter contract tests for `get_code_list`, `get_code_info`, and `get_calendar`.
- Added service contract tests for technical analysis single-indicator, category, invalid input, empty data, and default date behavior.
- Added `scripts/migrate_to_flat_layout.py` for one-shot conversion of year-bucketed files into the new flat layout, with `--dry-run`, `--keep-old`, and `--backup-root` options.

### Removed

- Removed local Parquet request cache support from `CacheManager`; local Parquet files are now owned by the historical warehouse only.
