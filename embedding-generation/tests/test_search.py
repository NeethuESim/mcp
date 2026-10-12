# Copyright © 2026, Arm Limited and Contributors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Focused regressions for retrieval normalization and ranking behavior."""

import importlib
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

search = importlib.import_module("arm_kb_search.search")


def candidate(chunk_uuid, title, url, **metadata):
    return {
        "rrf_score": 0.0,
        "metadata": {
            "chunk_uuid": chunk_uuid,
            "title": title,
            "url": url,
            "resolved_url": url,
            "search_text": metadata.pop("search_text", title),
            **metadata,
        },
    }


def test_url_tokenization_splits_path_separators_but_not_hostname():
    tokens = set(
        search.tokenize_url_content_for_search(
            "https://docs.example.com/install-guides/learning-paths/"
            "google-chrome/7-zip"
        )
    )

    assert {
        "install",
        "guides",
        "learning",
        "paths",
        "google",
        "chrome",
        "7",
        "zip",
    } <= tokens
    assert not {"docs", "example", "com"} & tokens


def test_lexical_exactness_ignores_url_hostname():
    matching_host = {
        "url": "https://docs.example.com/install-guides/linux",
        "resolved_url": "https://docs.example.com/install-guides/linux",
    }
    different_host = {
        "url": "https://other.example.com/install-guides/linux",
        "resolved_url": "https://other.example.com/install-guides/linux",
    }

    assert search._lexical_exactness_score(
        "docs.example.com linux", matching_host
    ) == search._lexical_exactness_score(
        "docs.example.com linux", different_host
    )


def test_learning_path_root_bonus_requires_learning_path_intent():
    root = candidate(
        "root",
        "Docker",
        "https://learn.arm.com/learning-paths/servers-and-cloud-computing/docker/",
        doc_type="Learning Paths",
    )
    other = candidate(
        "other",
        "Docker",
        "https://example.com/docker",
        doc_type="Tutorial",
    )

    ordinary = search.rerank_candidates("docker", [root, other])
    explicit = search.rerank_candidates("docker learning path", [root, other])
    ordinary_scores = {
        item["metadata"]["chunk_uuid"]: item["rerank_score"] for item in ordinary
    }
    explicit_scores = {
        item["metadata"]["chunk_uuid"]: item["rerank_score"] for item in explicit
    }

    assert ordinary_scores["root"] - ordinary_scores["other"] < 0.5
    assert explicit_scores["root"] - explicit_scores["other"] > 0.5



def test_query_tokenization_does_not_grow_with_rerank_candidate_count(monkeypatch):
    query = "install docker on linux"
    original_tokenize = search.tokenize_for_search
    query_calls = 0

    def counted_tokenize(text):
        nonlocal query_calls
        if text == query:
            query_calls += 1
        return original_tokenize(text)

    monkeypatch.setattr(search, "tokenize_for_search", counted_tokenize)
    candidates = [
        candidate(str(i), "Docker instructions", f"https://example.com/docker/{i}",
                  doc_type="Install Guides")
        for i in range(25)
    ]
    search.rerank_candidates(query, candidates[:1])
    single_candidate_calls = query_calls
    query_calls = 0
    results = search.rerank_candidates(query, candidates)

    assert len(results) == 25
    assert query_calls == single_candidate_calls
