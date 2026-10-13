"""Resource sections and Dashboard identity survive deduplication and fusion."""

import importlib

import pytest

from arm_kb_search import resources
from arm_kb_search.search import deduplicate_urls

search_module = importlib.import_module("arm_kb_search.search")


def result(
    url,
    resolved_url,
    doc_type="Ecosystem Dashboard",
    content_type="markdown",
    edition="",
):
    return {
        "metadata": {
            "url": url,
            "resolved_url": resolved_url,
            "doc_type": doc_type,
            "content_type": content_type,
            "edition": edition,
        }
    }


def test_dashboard_editions_survive_but_multiple_chunks_per_source_do_not():
    url = "https://developer.arm.com/ecosystem-dashboard/linux?package=calico"
    commercial = result(
        url,
        "https://raw.githubusercontent.com/repo/commit/content/linux/commercial_packages/calico.md",
        edition="commercial",
    )
    opensource = result(
        url,
        "https://raw.githubusercontent.com/repo/commit/content/linux/opensource_packages/calico.md",
        edition="open-source",
    )
    windows = result(
        url.replace("linux", "windows"),
        "https://raw.githubusercontent.com/repo/commit/content/windows/all_packages/calico.md",
    )
    assert deduplicate_urls([commercial, opensource, commercial, windows]) == [
        commercial,
        opensource,
        windows,
    ]


@pytest.mark.parametrize(
    "platform,package,edition,filenames",
    [
        (
            "linux",
            "kubermatic-kubernetes-platform",
            "commercial",
            ("KKP.md", "kubermatic-kubernetes-platform.md"),
        ),
        ("windows", "github-cli", "", ("gh.md", "github_cli.md")),
    ],
)
def test_duplicate_files_in_same_edition_keep_highest_ranked_hit(
    platform, package, edition, filenames
):
    url = f"https://developer.arm.com/ecosystem-dashboard/{platform}?package={package}"
    first, second = [
        result(
            url,
            f"https://raw.githubusercontent.com/repo/commit/{name}",
            edition=edition,
        )
        for name in filenames
    ]
    assert deduplicate_urls([first, second]) == [first]
    assert deduplicate_urls([second, first]) == [second]
    assert deduplicate_urls([first, second], max_chunks_per_url=2) == [first, second]


def test_other_documents_and_legacy_dashboard_keep_url_deduplication():
    for doc_type, content_type in [
        ("Documentation", "markdown"),
        ("Ecosystem Dashboard", "html"),
    ]:
        first = result("https://example.com", "first", doc_type, content_type)
        second = result("https://example.com", "second", doc_type, content_type)
        assert deduplicate_urls([first, second]) == [first]
        assert deduplicate_urls([first, second], max_chunks_per_url=2) == [
            first,
            second,
        ]


def test_search_response_retains_dashboard_scope_and_defaults_for_old_metadata(
    monkeypatch,
):
    dashboard = result("https://example.com/dashboard", "source")
    dashboard["metadata"].update(platform="linux", edition="commercial")
    legacy = result("https://example.com/guide", "guide", doc_type="Documentation")
    monkeypatch.setattr(
        resources, "hybrid_search", lambda *args, **kwargs: [dashboard, legacy]
    )
    state = resources.SearchResources([], None, None, None, include_disclaimers=False)
    hits = resources.search("query", state)
    assert hits[0]["platform"] == "linux" and hits[0]["edition"] == "commercial"
    assert hits[1]["platform"] == hits[1]["edition"] == ""


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

    result = search_module.deduplicate_urls([artifacts, troubleshooting, duplicate])

    assert [r["metadata"]["chunk_uuid"] for r in result] == ["artifacts", "troubleshooting"]


def test_distinct_resource_parameters_and_intrinsics_survive():
    items = [
        passage("a", "https://example.com/intrinsics/#q=svcntw"),
        passage("b", "https://example.com/intrinsics/#q=svcnth"),
        passage("v1", "https://example.com/reference?version=1#syntax"),
        passage("v2", "https://example.com/reference?version=2#syntax"),
    ]
    assert len(search_module.deduplicate_urls(items)) == 4


def test_semantic_fusion_keeps_hybrid_order_without_dense_evidence():
    items = [passage("first", "https://example.com/first"), passage("second", "https://example.com/second")]
    for item in items:
        del item["distance"]
    assert search_module.fuse_semantic_resource_ranks(items) == items


def test_semantic_fusion_deduplicates_tracking_aliases_without_losing_sections():
    items = [
        passage("first", "https://Example.com/doc/?b=2&utm_source=mcp&a=1#intro", 0.5),
        passage("alias", "https://example.com/doc?a=1&b=2&utm_medium=tool#intro", 0.1),
        passage("answer", "https://example.com/doc?a=1&b=2#troubleshoot", 0.2),
    ]

    result = search_module.fuse_semantic_resource_ranks(items)

    assert [r["metadata"]["chunk_uuid"] for r in result] == ["first", "answer"]
    assert result[0]["metadata"]["url"] == items[0]["metadata"]["url"]


def test_hybrid_fuses_before_cutting_off_candidate_pool(monkeypatch):
    items = [passage(str(i), f"https://example.com/page{i}", distance=0.5 + i / 100) for i in range(10)]
    items[8]["distance"] = 0.1
    items[8]["metadata"].update(
        url="https://example.com/page0#troubleshooting", resolved_url="https://example.com/page0",
    )
    monkeypatch.setattr(search_module, "_rerank_candidates", lambda query, candidates: items)

    results = search_module.hybrid_search("target", None, [], object(), None, k=5)

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

    result = search_module.fuse_semantic_resource_ranks(items)

    assert [item["metadata"]["chunk_uuid"] for item in result] == ["commercial", "open"]
    assert [item["metadata"]["edition"] for item in result] == ["commercial", "open-source"]
