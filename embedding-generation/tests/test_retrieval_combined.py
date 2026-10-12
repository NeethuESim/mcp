"""Regressions for behavior shared by the two retrieval proposals."""

import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
search = importlib.import_module("arm_kb_search.search")
resources = importlib.import_module("arm_kb_search.resources")


def candidate(key, title, **metadata):
    return {
        "rrf_score": 0.0,
        "metadata": {
            "chunk_uuid": key,
            "title": title,
            "url": f"https://example.com/{key}/",
            "search_text": title,
            **metadata,
        },
    }


def test_embedding_keeps_original_cli_query():
    class Model:
        def encode(self, texts):
            assert texts == ["Install AWS-CLI!"]
            return np.zeros((1, 2))

    class Index:
        def search(self, _vector, _k):
            return (np.array([0]), np.array([0.1]))

    metadata = [candidate("aws-cli", "AWS CLI")["metadata"]]
    results = search.hybrid_search("Install AWS-CLI!", Index(), metadata, Model(), None, k=1)
    assert results[0]["metadata"]["chunk_uuid"] == "aws-cli"


@pytest.mark.parametrize("intent", ["install", "installing", "installation", "setup", "set up"])
def test_install_preference_requires_product_evidence(intent):
    def delta(title):
        # Generic acquisition keywords must not count as product evidence.
        keywords = "install; build; download"
        plain = candidate("tool", title, doc_type="Tutorial", keywords=keywords)
        guide = candidate("tool", title, doc_type="Install Guides", keywords=keywords)
        a = search.rerank_candidates(f"{intent} docker", [plain])[0]["rerank_score"]
        b = search.rerank_candidates(f"{intent} docker", [guide])[0]["rerank_score"]
        return b - a

    assert delta("Kubernetes") < 0.10
    assert delta("Install Guide") < 0.10
    assert delta("Docker") > 0.20


def test_navigation_filter_preserves_substantive_next_steps():
    footer = candidate("footer", "Docker", original_text="Document Title: Docker\nHeading Path: Next Steps\n\nLog an issue\nFork and edit\nDiscuss on Discord")
    next_steps = candidate("useful", "Docker", original_text="Next Steps\nRun docker hello-world to verify installation.\nLog an issue")
    results = search.rerank_candidates("docker", [footer, next_steps])
    assert [item["metadata"]["chunk_uuid"] for item in results] == ["useful"]


def test_filtered_dense_pool_refills_with_useful_pages():
    class Model:
        def encode(self, _texts):
            return np.zeros((1, 2))

    class Index:
        def search(self, _vector, k):
            count = min(k, 200)
            return (np.arange(count), np.full(count, 0.2))

    metadata = [
        candidate(str(i), "Target", original_text="Log an issue" if i < 100 else "Useful target instructions")["metadata"]
        for i in range(200)
    ]
    assets = resources.SearchResources(metadata, Model(), Index(), None, include_disclaimers=False)
    results = resources.search("target", assets, k=3)
    assert len(results) == 3
    assert all(item["snippet"] == "Useful target instructions" for item in results)


@pytest.mark.parametrize("query", ["", "  ", "?!"])
def test_empty_query_has_no_results(query):
    assets = resources.SearchResources([], object(), None, None, include_disclaimers=False)
    assert resources.search(query, assets) == []
