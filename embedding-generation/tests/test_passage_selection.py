"""Preserve answer-bearing sections through deduplication and semantic fusion."""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
search = importlib.import_module("arm_kb_search.search")


def passage(identifier, url, distance=0.5, resolved_url=None):
    return {
        "metadata": {
            "chunk_uuid": identifier,
            "url": url,
            "resolved_url": resolved_url or url,
        },
        "distance": distance,
        "rerank_score": 1.0,
    }


def test_different_sections_keep_their_answer_passages():
    page = "https://example.com/ci/"
    artifacts = passage("artifacts", page + "#artifacts", resolved_url=page)
    troubleshooting = passage("troubleshooting", page + "#troubleshooting", resolved_url=page)
    duplicate = passage("duplicate", page + "?utm_source=mcp#troubleshooting", resolved_url=page)

    result = search.deduplicate_urls([artifacts, troubleshooting, duplicate])

    assert [r["metadata"]["chunk_uuid"] for r in result] == ["artifacts", "troubleshooting"]


def test_distinct_resource_parameters_and_intrinsics_survive():
    items = [
        passage("a", "https://example.com/intrinsics/#q=svcntw"),
        passage("b", "https://example.com/intrinsics/#q=svcnth"),
        passage("v1", "https://example.com/reference?version=1#syntax"),
        passage("v2", "https://example.com/reference?version=2#syntax"),
    ]
    assert len(search.deduplicate_urls(items)) == 4


def test_semantic_fusion_keeps_hybrid_order_without_dense_evidence():
    items = [passage("first", "https://example.com/first"), passage("second", "https://example.com/second")]
    for item in items:
        del item["distance"]
    assert search.fuse_semantic_resource_ranks(items) == items


def test_semantic_fusion_deduplicates_tracking_aliases_without_losing_sections():
    items = [
        passage("first", "https://Example.com/doc/?b=2&utm_source=mcp&a=1#intro", 0.5),
        passage("alias", "https://example.com/doc?a=1&b=2&utm_medium=tool#intro", 0.1),
        passage("answer", "https://example.com/doc?a=1&b=2#troubleshoot", 0.2),
    ]

    result = search.fuse_semantic_resource_ranks(items)

    assert [r["metadata"]["chunk_uuid"] for r in result] == ["first", "answer"]
    assert result[0]["metadata"]["url"] == items[0]["metadata"]["url"]


def test_hybrid_fuses_before_cutting_off_candidate_pool(monkeypatch):
    items = [passage(str(i), f"https://example.com/page{i}", distance=0.5 + i / 100) for i in range(10)]
    items[8]["distance"] = 0.1
    items[8]["metadata"].update(
        url="https://example.com/page0#troubleshooting", resolved_url="https://example.com/page0",
    )
    monkeypatch.setattr(search, "_rerank_candidates", lambda query, candidates: items)

    results = search.hybrid_search("target", None, [], object(), None, k=5)

    assert results[0]["metadata"]["chunk_uuid"] == "0"
    assert "8" in [r["metadata"]["chunk_uuid"] for r in results]
    assert next(r for r in results if r["metadata"]["chunk_uuid"] == "8")["metadata"]["url"].endswith("#troubleshooting")


def test_fusion_keeps_dashboard_editions_and_collapses_source_file_aliases():
    url = "https://developer.arm.com/ecosystem-dashboard/linux?package=calico"
    items = [
        passage("commercial", url, 0.5, "https://raw.githubusercontent.com/repo/a.md"),
        passage("alias", url, 0.1, "https://raw.githubusercontent.com/repo/b.md"),
        passage("open", url, 0.2, "https://raw.githubusercontent.com/repo/c.md"),
    ]
    for item in items:
        item["metadata"].update(
            doc_type="Ecosystem Dashboard", content_type="markdown",
            edition="open-source" if item["metadata"]["chunk_uuid"] == "open" else "commercial",
        )

    result = search.fuse_semantic_resource_ranks(items)

    assert [item["metadata"]["chunk_uuid"] for item in result] == ["commercial", "open"]
    assert [item["metadata"]["edition"] for item in result] == ["commercial", "open-source"]
