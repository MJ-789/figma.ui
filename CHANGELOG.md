# Changelog

All notable changes to this project will be documented in this file.

## [1.1.3] - 2026-04-02

### Fixed
- `image_compare.py`: return native Python `float` / `bool` in metrics and reports so `json.dump` does not hit `numpy.bool_` / NumPy scalars.
- `pytest.ini`: add `-p no:playwright` to avoid `pytest-playwright` starting an asyncio loop that breaks manual `sync_playwright()` usage.
- `tests/conftest.py`: skip `homepage_firefox` when Playwright Firefox is not installed (Windows `ms-playwright` path check).
- `tests/test_desktop.py`: remove emoji from `print` / assert messages for Windows GBK consoles (`UnicodeEncodeError`).

## [1.1.2] - 2026-04-02

### Fixed
- `report_writer.py`: fallback `UTC = timezone.utc` when `datetime.UTC` is missing (Python 3.10), so imports work on 3.10 and 3.11+.

## [1.1.1] - 2026-04-02

### Fixed
- `report_writer.py`: use `datetime.timezone.utc` instead of `datetime.UTC` for Python 3.10 compatibility.

## [1.1.0] - 2026-04-02

### Added
- Multi-page crawl foundation (`src/page_crawler.py`):
  - Seed-based discovery
  - Same-domain filter
  - Depth/page/click limits
  - Exclude keyword filter
- Structured JSON report writer (`src/report_writer.py`) with output at `reports/json/run_result.json`.
- Crawl smoke test flow (`TestCrawlDiscovery`) to validate page discovery and persist summary.

### Changed
- `config/config.py` now includes crawl-related settings and JSON report path.
- `Config.setup_directories()` now creates `reports/images` and `reports/json`.
- Desktop visual test now writes one structured JSON record per run.
- `pytest.ini` adds `crawl` marker.

## [1.0.0] - 2026-04-02

### Added
- Baseline version definition for the first stable project release.
- Initial visual comparison workflow:
  - Export Figma node image
  - Capture web screenshot with Playwright
  - Compare images and generate diff artifacts
  - Output HTML report via pytest-html

### Notes
- This is the project baseline version.
- From this version onward, every modification should be recorded with a version entry.
