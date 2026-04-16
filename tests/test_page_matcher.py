"""测试 page_fingerprint + page_matcher 的纯数据逻辑。"""

from src.page_fingerprint import (
    text_similarity,
    list_overlap,
    best_pairwise_similarity,
    name_similarity,
    page_type_similarity,
    compute_page_similarity,
)
from src.page_matcher import PageMatcher

FIGMA_PAGES = [
    {
        "figma_page_id": "figma::100:1",
        "figma_node_id": "100:1",
        "frame_name": "Homepage",
        "text_summary": ["Welcome to Example", "Build something amazing", "Get Started"],
        "structure_summary": {"text_count": 3, "button_hint_count": 1, "image_count": 0},
    },
    {
        "figma_page_id": "figma::200:1",
        "figma_node_id": "200:1",
        "frame_name": "Pricing",
        "text_summary": ["Choose your plan", "Start free trial"],
        "structure_summary": {"text_count": 2, "button_hint_count": 1, "image_count": 0},
    },
]

SITE_PAGES = [
    {
        "page_id": "site::aaa",
        "url": "https://example.com/",
        "path": "/",
        "title": "Example - Build something amazing",
        "text_summary": ["Example - Build something amazing", "Welcome to Example", "Get Started"],
        "dom_summary": {"heading_count": 3, "button_count": 2, "image_count": 5},
    },
    {
        "page_id": "site::bbb",
        "url": "https://example.com/pricing",
        "path": "/pricing",
        "title": "Pricing - Choose your plan",
        "text_summary": ["Pricing - Choose your plan", "Choose your plan", "Start free trial"],
        "dom_summary": {"heading_count": 5, "button_count": 3, "image_count": 2},
    },
]


class TestTextSimilarity:
    def test_identical(self):
        assert text_similarity("hello", "hello") == 1.0

    def test_case_insensitive(self):
        assert text_similarity("Pricing", "pricing") > 0.95

    def test_different(self):
        assert text_similarity("Homepage", "About") < 0.5

    def test_empty(self):
        assert text_similarity("", "hello") == 0.0


class TestListOverlap:
    def test_identical(self):
        assert list_overlap(["a", "b"], ["a", "b"]) == 1.0

    def test_disjoint(self):
        assert list_overlap(["a"], ["b"]) == 0.0

    def test_partial(self):
        result = list_overlap(["a", "b", "c"], ["b", "c", "d"])
        assert 0.4 < result < 0.6


class TestNameSimilarity:
    def test_pricing_vs_pricing_path(self):
        score = name_similarity("Pricing", "Pricing - Choose your plan", "/pricing")
        assert score > 0.7

    def test_homepage_vs_root(self):
        score = name_similarity("Homepage", "Example", "/")
        assert score > 0.3

    def test_unrelated(self):
        score = name_similarity("Pricing", "About Us", "/about")
        assert score < 0.5


class TestPageTypeSimilarity:
    def test_home_matches_root(self):
        assert page_type_similarity("Home", "Example", "/") == 1.0

    def test_category_matches_list_path(self):
        assert page_type_similarity("Category", "Finance", "/list/Finance") == 1.0

    def test_unrelated_types(self):
        assert page_type_similarity("Detail", "Example", "/list/Finance") == 0.0


class TestComputePageSimilarity:
    def test_correct_pair_scores_higher(self):
        correct = compute_page_similarity(FIGMA_PAGES[0], SITE_PAGES[0])
        wrong = compute_page_similarity(FIGMA_PAGES[0], SITE_PAGES[1])
        assert correct["total_score"] > wrong["total_score"]

    def test_pricing_correct_pair(self):
        correct = compute_page_similarity(FIGMA_PAGES[1], SITE_PAGES[1])
        wrong = compute_page_similarity(FIGMA_PAGES[1], SITE_PAGES[0])
        assert correct["total_score"] > wrong["total_score"]

    def test_returns_page_type_score(self):
        correct = compute_page_similarity(FIGMA_PAGES[1], SITE_PAGES[1])
        assert "page_type_score" in correct


class TestPageMatcher:
    def test_basic_matching(self):
        matcher = PageMatcher(top_k=3, min_confidence=0.3)
        result = matcher.match(FIGMA_PAGES, SITE_PAGES)

        pair_map = {p["figma_name"]: p["site_path"] for p in result["pairs"]}
        assert pair_map.get("Homepage") == "/"
        assert pair_map.get("Pricing") == "/pricing"

    def test_summary_counts(self):
        matcher = PageMatcher(top_k=3, min_confidence=0.3)
        result = matcher.match(FIGMA_PAGES, SITE_PAGES)

        assert result["summary"]["total_figma"] == 2
        assert result["summary"]["total_site"] == 2
        assert result["summary"]["matched"] == 2

    def test_high_confidence_filter(self):
        matcher = PageMatcher(top_k=3, min_confidence=0.99)
        result = matcher.match(FIGMA_PAGES, SITE_PAGES)
        assert result["summary"]["matched"] == 0

    def test_pairs_have_required_fields(self):
        matcher = PageMatcher(top_k=3, min_confidence=0.3)
        result = matcher.match(FIGMA_PAGES, SITE_PAGES)

        for pair in result["pairs"]:
            assert "figma_page_id" in pair
            assert "figma_node_id" in pair
            assert "site_url" in pair
            assert "confidence" in pair
            assert "scores" in pair
            assert "reason" in pair
            assert pair["status"] == "matched"
