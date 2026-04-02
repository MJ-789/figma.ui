# Changelog

All notable changes to this project will be documented in this file.

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
