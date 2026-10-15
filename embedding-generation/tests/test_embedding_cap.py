"""Offline MiniLM contract checks; acquire the locked model before running."""

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch  # Initialize its OpenMP runtime before USearch on macOS.
from arm_kb_search import resources
from transformers import AutoTokenizer

import local_vectorstore_creation as vectorstore


@pytest.fixture(scope="module")
def tokenizer():
    path = os.getenv("MINILM_MODEL_PATH", str(Path(__file__).parents[1] / ".cache/embedding-model"))
    if not Path(path).is_dir():
        pytest.skip("Acquire the locked MiniLM model or set MINILM_MODEL_PATH for tokenizer checks")
    return AutoTokenizer.from_pretrained(path, local_files_only=True, use_fast=True)


def record(text, identifier="doc"):
    return dict(
        uuid=identifier, chunk_uuid=identifier, original_text=text,
        search_text="Metadata title keywords " + text,
        url="https://example.com/guide?version=2#section",
        resolved_url="https://example.com/source#original", title="Original title",
        heading_path=["Chapter", "Section"], keywords="Arm", custom={"keep": True},
    )


@pytest.mark.parametrize("text", ["", " \t\n", "word " * 498])
def test_fitting_input_is_unchanged(tokenizer, text):
    row = record(text)
    assert vectorstore.token_count(tokenizer, text) <= 500
    assert vectorstore.split_embedding_inputs([row], tokenizer) == [row]


@pytest.mark.parametrize("identifier", ["yaml_data_doc", "intrinsic_vadd"])
@pytest.mark.parametrize("text", [
    "word " * 499,
    "Document Title: Code\n\n" + "\tint café = foo(a, b);  // 雪\r\n" * 400 + "  \n",
    "a;" * 1500,
])
def test_cap_preserves_exact_text_metadata_and_piece_identity(tokenizer, identifier, text):
    row = record(text, identifier)
    pieces = vectorstore.split_embedding_inputs([row], tokenizer)
    assert len(pieces) > 1
    assert "".join(piece["original_text"] for piece in pieces) == text
    assert all(vectorstore.token_count(tokenizer, piece["original_text"]) <= 500 for piece in pieces)
    assert len({piece["uuid"] for piece in pieces}) == len(pieces)
    assert len({piece["chunk_uuid"] for piece in pieces}) == len(pieces)
    cursor = 0
    for number, piece in enumerate(pieces, 1):
        assert piece["source_start"] == cursor
        cursor = piece["source_end"]
        assert piece["original_text"] == text[piece["source_start"]:cursor]
        assert piece["parent_uuid"] == piece["parent_chunk_uuid"] == identifier
        assert piece["uuid"] != identifier and piece["chunk_uuid"] != identifier
        assert (piece["source_part"], piece["source_parts"]) == (number, len(pieces))
        assert piece["search_text"] == "Metadata title keywords " + piece["original_text"]
        for key in ("url", "resolved_url", "title", "heading_path", "keywords", "custom"):
            assert piece[key] == row[key]
    assert cursor == len(text)
    assert vectorstore.split_embedding_inputs([row], tokenizer) == pieces


def test_bad_search_text_is_rejected(tokenizer):
    row = record("word " * 499)
    row["search_text"] = "missing body"
    with pytest.raises(ValueError, match="search_text"):
        vectorstore.split_embedding_inputs([row], tokenizer)


def test_consecutive_calls_and_index_metadata_alignment(tokenizer, monkeypatch):
    rows = [record(f"Record {i} " + "word " * (499 if i == 2 else 1), str(i)) for i in range(64)]
    expected = vectorstore.split_embedding_inputs(rows, tokenizer)
    calls = []

    class Model:
        def __init__(self, path, **kwargs):
            assert kwargs == dict(device="cpu", local_files_only=True, trust_remote_code=False)
            self.tokenizer = tokenizer

        def float(self):
            return self

        def eval(self):
            return self

        def encode(self, texts, **kwargs):
            assert self.max_seq_length == 512
            assert kwargs == dict(batch_size=32, show_progress_bar=False, convert_to_numpy=True,
                                  precision="float32", normalize_embeddings=True)
            offset = sum(map(len, calls))
            calls.append(texts)
            # Each vector identifies its input position; exercise the real USearch store.
            return np.repeat(np.arange(offset, offset + len(texts))[:, None], 384, axis=1)

    monkeypatch.setattr(vectorstore, "SentenceTransformer", Model)
    monkeypatch.setattr(vectorstore.torch, "set_num_interop_threads", lambda _: None)
    vectors, metadata = vectorstore.create_embeddings(rows, "unused")
    assert [len(batch) for batch in calls] == [32, 32, 1]
    assert [text for batch in calls for text in batch] == [row["original_text"] for row in expected]
    assert metadata == expected
    index, metadata = vectorstore.create_usearch_index(vectors, metadata)
    for position, row in enumerate(metadata):
        assert row == expected[position]
        np.testing.assert_array_equal(index.get(position), vectors[position])
    with pytest.raises(ValueError, match="counts differ"):
        vectorstore.create_usearch_index(vectors[:-1], metadata)


@pytest.mark.parametrize("cache_miss", [False, True])
def test_query_loader_sets_512_on_local_and_download_paths(monkeypatch, cache_miss):
    calls = []
    model = SimpleNamespace(max_seq_length=256)

    def load(*args, **kwargs):
        calls.append(kwargs)
        if cache_miss and len(calls) == 1:
            raise OSError("cache miss")
        return model

    monkeypatch.setattr(resources, "SentenceTransformer", load)
    assert resources.load_embedding_model("all-MiniLM-L6-v2") is model
    assert model.max_seq_length == 512
    assert calls[-1]["local_files_only"] is not cache_miss


def test_real_query_window_and_builtin_normalization(tokenizer):
    torch.set_num_threads(2)
    model = resources.load_embedding_model(tokenizer.name_or_path, allow_download=False).to("cpu")
    assert model.max_seq_length == 512
    assert model.tokenize(["hello " * 510])["input_ids"].shape[1] == 512
    assert model.get_embedding_dimension() == 384
    assert type(model[-1]).__name__ == "Normalize"
    vectors = model.encode(["Arm intrinsics", "hello " * 510], convert_to_numpy=True)
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-6)
