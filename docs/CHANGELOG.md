# Changelog

## Unreleased

### Changed

- **L3 historical warehouse: flat layout migration.** Per-(period, year, code) files replaced by a single Parquet per (period, code) with all years merged (`A_share/{daily|weekly|monthly}/{code}.parquet`). Sync jobs now pull `[20200101, today]` per code and overwrite the single file. `_metadata.json` moved from per-year to per-period. `kline_file_path()` and `warehouse.kline_dir()` no longer require a `year` argument; the parameter is accepted but ignored for backward compatibility. `sync_kline_daily/weekly/monthly` now accept `from_date`/`to_date` (the legacy `year=` keyword still works). `warehouse.stats()` drops `year_count`, adds `first_date`/`last_date`. New migration script: `python -m scripts.migrate_to_flat_layout [--dry-run] [--keep-old] [--backup-root PATH]`.
- Moved limit-up stock calculation from the market router into `LimitUpService`, using daily K-line data and theoretical limit-up prices.
- Updated Phase 3 development plan status for completed `TechnicalResponse`, `tables`, `limit-up`, and changelog tasks.
- Fixed AmazingData `BaseData` calls so `get_code_list` honors the requested `security_type` and `get_calendar` remains compatible across SDK versions.
- Moved technical analysis orchestration from the router into `TechnicalAnalysisService`.
- Removed generic request-result caching from the AmazingData adapter and limit-up service; Redis is now scoped to real-time/subscription market data only.
- Removed unused generic cache hit/miss Prometheus metrics.

### Added

- Added service contract tests for limit-up local metadata/K-line hits, AmazingData fallback without login checks, remote K-line persistence, board/ST filtering, limit-price rounding, partial K-line batch failures, and ladder generation.
- Added adapter contract tests for `get_code_list`, `get_code_info`, and `get_calendar`.
- Added service contract tests for technical analysis single-indicator, category, invalid input, empty data, and default date behavior.
- Added `scripts/migrate_to_flat_layout.py` for one-shot conversion of year-bucketed files into the new flat layout, with `--dry-run`, `--keep-old`, and `--backup-root` options.

### Removed

- Removed local Parquet request cache support from `CacheManager`; local Parquet files are now owned by the historical warehouse only.
