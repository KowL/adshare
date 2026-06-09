# Changelog

## Unreleased

### Changed

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

### Removed

- Removed local Parquet request cache support from `CacheManager`; local Parquet files are now owned by the historical warehouse only.
