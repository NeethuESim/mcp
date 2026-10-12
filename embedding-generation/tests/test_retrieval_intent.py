"""Regression coverage for architecture aliases and task intent in passage scoring."""

import importlib

import pytest

search = importlib.import_module("arm_kb_search.search")


def passage(key, title, text, **metadata):
    return {"metadata": {"chunk_uuid": key, "title": title, "heading": title,
                         "url": f"https://example.com/{key}/", "search_text": text,
                         "original_text": text, **metadata}}


@pytest.mark.parametrize("query,architecture", [("arm64", "aarch64"), ("aarch64", "arm64")])
def test_architecture_alias_matches_without_conflating_arm32(query, architecture):
    arm32 = passage("old", "Installation", "Download the arm32 package.")
    arm64 = passage("new", "Installation", f"Download the {architecture} package.")
    ranked = search.rerank_candidates(query, [arm32, arm64])
    assert ranked[0]["metadata"]["chunk_uuid"] == "new"


def test_compact_alias_does_not_join_generic_prose():
    plain = passage("plain", "Instructions", "Ordinary instructions.")
    prose = passage("prose", "Set up", "Ordinary instructions.")
    ranked = search.rerank_candidates("setup", [plain, prose])
    assert ranked[0]["rerank_score"] == ranked[1]["rerank_score"]


def test_chapter_navigation_does_not_displace_task_instructions():
    navigation = passage(
        'nav', 'Run a utility on Arm',
        'Document Title: Run a utility on Arm\nHeading Path: Introduction\n\n'
        'Introduction\nSet up your environment\nRun a utility on Arm\nNext Steps',
        url='https://learn.arm.com/learning-paths/servers-and-cloud-computing/utility/#learning-path-step-active',
    )
    instructions = passage('useful', 'Utility', 'Run utility --help to check the installation.')
    ranked = search.rerank_candidates('run utility on arm', [navigation, instructions])
    assert [r['metadata']['chunk_uuid'] for r in ranked] == ['useful']


def test_substantive_intro_at_navigation_anchor_is_retained():
    intro = passage(
        'intro', 'Utility',
        'Introduction\nInstall the utility, then run utility --help.\nNext Steps',
        url='https://learn.arm.com/learning-paths/servers-and-cloud-computing/utility/#learning-path-title',
    )
    assert search.rerank_candidates('utility', [intro])


def test_named_product_outweighs_generic_run_on_arm_title():
    generic = passage(
        'generic', 'Run applications on Arm', 'Run applications on Arm with this tutorial.',
        doc_type='Learning Paths',
        url='https://learn.arm.com/learning-paths/servers-and-cloud-computing/applications/#introduction',
    )
    product = passage('product', 'BuildCLI', 'Download BuildCLI for Linux.', doc_type='Install Guides')
    ranked = search.rerank_candidates('run buildcli arm', [generic, product])
    assert ranked[0]['metadata']['chunk_uuid'] == 'product'


def test_installation_retrieves_relevant_passage_beyond_initial_dense_pool():
    import numpy as np
    from arm_kb_search.resources import SearchResources
    from arm_kb_search.resources import search as retrieve

    class Model:
        def encode(self, _texts):
            return np.zeros((1, 2))

    class Index:
        def search(self, _vector, k):
            count = min(k, len(metadata))
            return np.arange(count), np.full(count, 0.2)

    metadata = [passage(str(i), 'Other tool', 'Unrelated instructions.')['metadata'] for i in range(140)]
    metadata[120] = passage('correct', 'GraphCLI', 'Install GraphCLI on Linux.', doc_type='Install Guides')['metadata']
    assets = SearchResources(metadata, Model(), Index(), None, include_disclaimers=False)
    result = retrieve('Set up graphcli on an Arm instance', assets, k=5)
    assert result[0]['url'] == 'https://example.com/correct/'


def test_installation_subject_is_not_overwhelmed_by_machine_context():
    machine = passage(
        'machine', 'Create an EC2 instance',
        'Set up an Arm based EC2 instance. Create an EC2 instance for your application.',
        heading='Set up your EC2 instance', doc_type='Learning Paths',
    )
    machine.update(bm25_score=15, rrf_score=0.04, lexical_exactness_score=1.2)
    product = passage('product', 'GraphCLI', 'Install GraphCLI on Linux.', doc_type='Install Guides')
    product.update(distance=0.3, rrf_score=0.007)
    ranked = search.rerank_candidates('set up graphcli on an arm based ec2 instance', [machine, product])
    assert ranked[0]['metadata']['chunk_uuid'] == 'product'


def test_installation_subject_preserves_explicit_operating_system():
    linux = passage('linux', 'GraphCLI', 'Install GraphCLI on Linux.', doc_type='Install Guides')
    windows = passage('windows', 'GraphCLI', 'Install GraphCLI on Windows.', doc_type='Install Guides')
    ranked = search.rerank_candidates('install graphcli on windows', [linux, windows])
    assert ranked[0]['metadata']['chunk_uuid'] == 'windows'
