# Figma UI Automation Roadmap

## Version Status

- Current Release: **v1.0**
- Release Scope: focused 3-page workflow (`home` / `category` / `details`) with
  design JSON extraction, web capture, element-level comparison, functional checks,
  and visual HTML reporting.

## Goals

1. Element-level matching between design and implementation.
2. Output a list of different elements, not only global similarity.
3. Expand to multi-page crawling with automatic click navigation.

## Current Limits (v1.0)

- Comparison is page-level bitmap diff only.
- No stable element identity map between Figma node and DOM node.
- No navigation graph for auto-discovery and traversal.

## Proposed Architecture

### 1) Element-level positioning

- Build a "design element registry" from Figma:
  - node_id
  - element name
  - absolute bounding box (x, y, width, height)
  - optional component hierarchy path
- Build a "web element registry" from browser:
  - css/xpath locator
  - text/role/testid signatures
  - runtime bounding box
  - computed style snapshot
- Introduce matching strategy:
  - Priority: testid -> role+name -> text signature -> geometry fallback
  - Output confidence score

### 2) Difference element output

- Add element-level compare pipeline:
  - compare geometry delta
  - compare color and typography (font size/weight/line-height)
  - compare visibility and clipping
- Persist results as JSON:
  - element_id
  - match_status
  - diff_types
  - severity
  - preview image path
- Render mismatch overlays for each element and an aggregate mismatch table.

### 3) Multi-page auto-crawl

- Add navigation crawler:
  - start from seed URLs
  - auto-click candidate links/buttons with de-dup strategy
  - restrict by domain and max depth
  - support blacklist selectors
- For each discovered page:
  - wait for stable state
  - run page-level + element-level compare
  - append into one run report

## Implementation Phases

### v1.1.0 (Foundation)
- Add versioned config schema for pages and crawl options.
- Add structured report JSON output.
- Add page discovery skeleton and URL queue manager.

### v1.2.0 (Element Match MVP)
- Add Figma element extraction and web element extraction.
- Add matching engine and confidence score.
- Add per-element mismatch report.

### v1.3.0 (Crawl + Visual Workflow)
- Add automatic click traversal and de-dup.
- Add per-page snapshots and retry mechanism.
- Merge all pages into one summary report.

### v1.4.0 (Stability)
- Add baseline cache and flaky controls (masking, wait strategies).
- Add threshold profiles by page category.
- Add CI-friendly artifacts and summary export.
