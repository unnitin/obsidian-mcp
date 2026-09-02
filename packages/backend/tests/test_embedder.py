"""Unit tests for Embedder — covers uncovered lines using a mocked model."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import numpy as np
from obsidian_search.embedding.embedder import Embedder, prefixes_for


def _mock_model(output_dims: int = 384) -> MagicMock:
    """Return a SentenceTransformer mock whose encode() returns unit vectors."""
    model = MagicMock()
    model.encode.side_effect = lambda texts, **kwargs: np.random.rand(
        len(texts), output_dims
    ).astype(np.float32)
    model.get_sentence_embedding_dimension.return_value = output_dims
    return model


def _bare_embedder(dims: int = 384) -> Embedder:
    """Construct an Embedder bypassing __init__, with all required attributes set."""
    e = Embedder.__new__(Embedder)
    e.model_name = "test-model"
    e.dims = dims
    e.prefixes = prefixes_for("test-model")
    e._model = _mock_model(dims)
    e._sem = threading.Semaphore(1)
    return e


class TestEmbedderLoad:
    def test_load_calls_sentence_transformer(self) -> None:
        """Covers _load: initialises the model on first call with correct args.

        SentenceTransformer is a local import inside _load(), so we patch
        the module it comes from rather than the embedder module's namespace.
        _load also passes device= (mps/cuda/cpu), so we check only the
        positional arg and trust_remote_code rather than the full call signature.
        """
        e = Embedder(model_name="fake-model")
        mock_model = _mock_model()
        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=mock_model,
        ) as MockST:
            loaded = e._load()
        args, kwargs = MockST.call_args
        assert args[0] == "fake-model"
        # non-nomic models should NOT request trust_remote_code
        assert kwargs.get("trust_remote_code") is False
        assert "device" in kwargs
        assert loaded is mock_model

    def test_load_trust_remote_code_for_nomic(self) -> None:
        """nomic models require trust_remote_code=True for their custom pooling."""
        e = Embedder(model_name="nomic-ai/nomic-embed-text-v1.5")
        mock_model = _mock_model(768)
        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=mock_model,
        ) as MockST:
            e._load()
        _, kwargs = MockST.call_args
        assert kwargs.get("trust_remote_code") is True

    def test_load_cached_on_second_call(self) -> None:
        """_load must not reinstantiate the model once cached."""
        e = Embedder(model_name="fake-model")
        mock_model = _mock_model()
        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=mock_model,
        ) as MockST:
            e._load()
            e._load()
        assert MockST.call_count == 1


class TestEmbedderEncode:
    def test_encode_returns_float32_ndarray(self) -> None:
        """Covers encode(): calls model.encode and wraps result."""
        e = _bare_embedder()
        result = e.encode(["hello world"])
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert result.shape == (1, 384)

    def test_encode_batch_shape(self) -> None:
        e = _bare_embedder()
        result = e.encode(["text one", "text two", "text three"])
        assert result.shape == (3, 384)

    def test_encode_passes_normalize_and_batch_size(self) -> None:
        """Verify the model is called with the correct kwargs."""
        e = _bare_embedder()
        e.encode(["hello"])
        _, kwargs = e._model.encode.call_args
        assert kwargs.get("normalize_embeddings") is True
        assert kwargs.get("batch_size") == 32
        assert kwargs.get("show_progress_bar") is False


class TestEmbedderPrefixes:
    def test_encode_documents_applies_the_models_document_prefix(self) -> None:
        """Covers encode_documents: prepends whatever the model expects."""
        e = _bare_embedder()
        e.prefixes = prefixes_for("nomic-ai/nomic-embed-text-v1.5")
        e.encode_documents(["my note content"])
        call_args = e._model.encode.call_args[0][0]
        assert call_args[0].startswith("search_document: ")

    def test_encode_query_applies_the_models_query_prefix(self) -> None:
        """Covers encode_query: prepends whatever the model expects."""
        e = _bare_embedder()
        e.prefixes = prefixes_for("nomic-ai/nomic-embed-text-v1.5")
        result = e.encode_query("quantum computing")
        call_args = e._model.encode.call_args[0][0]
        assert call_args[0].startswith("search_query: ")
        assert result.shape == (384,)
        assert result.dtype == np.float32

    def test_encode_query_returns_1d_array(self) -> None:
        e = _bare_embedder()
        result = e.encode_query("test query")
        assert result.ndim == 1
        assert len(result) == 384


class TestEmbedderSearcher:
    def test_default_model_name(self) -> None:
        e = Embedder()
        assert e.model_name == "BAAI/bge-small-en-v1.5"

    def test_dims_zero_before_load(self) -> None:
        # dims is derived from the loaded model; it starts at 0 until _load() runs
        e = Embedder()
        assert e.dims == 0

    def test_dims_set_after_load(self) -> None:
        e = Embedder(model_name="test-model")
        mock_model = _mock_model(384)
        with patch("sentence_transformers.SentenceTransformer", return_value=mock_model):
            e._load()
        assert e.dims == 384

    def test_model_none_before_load(self) -> None:
        e = Embedder()
        assert e._model is None


class TestTaskPrefixes:
    """Prefix conventions are per-model and must not leak across models."""

    def test_bge_uses_query_instruction_and_no_document_prefix(self) -> None:
        from obsidian_search.embedding.embedder import prefixes_for

        p = prefixes_for("BAAI/bge-small-en-v1.5")
        assert p.document == ""
        assert p.query == "Represent this sentence for searching relevant passages: "

    def test_nomic_uses_search_task_prefixes(self) -> None:
        from obsidian_search.embedding.embedder import prefixes_for

        p = prefixes_for("nomic-ai/nomic-embed-text-v1.5")
        assert p.document == "search_document: "
        assert p.query == "search_query: "

    def test_unknown_model_gets_no_prefixes(self) -> None:
        from obsidian_search.embedding.embedder import prefixes_for

        p = prefixes_for("some-org/some-new-model")
        assert p.document == ""
        assert p.query == ""

    def test_default_model_does_not_get_nomic_prefixes(self) -> None:
        """Regression: the nomic prefixes were applied to every model."""
        e = Embedder()  # default is bge-small
        assert e.prefixes.document == ""
        assert "search_document" not in e.prefixes.query

    def test_encode_documents_passes_text_through_for_bge(self) -> None:
        e = _bare_embedder()
        e.prefixes = Embedder(model_name="BAAI/bge-small-en-v1.5").prefixes
        e.encode_documents(["chunk one", "chunk two"])
        sent = e._model.encode.call_args[0][0]
        assert sent == ["chunk one", "chunk two"]

    def test_encode_documents_prefixes_for_nomic(self) -> None:
        e = _bare_embedder()
        e.prefixes = Embedder(model_name="nomic-ai/nomic-embed-text-v1.5").prefixes
        e.encode_documents(["chunk one"])
        sent = e._model.encode.call_args[0][0]
        assert sent == ["search_document: chunk one"]

    def test_encode_query_applies_query_instruction(self) -> None:
        e = _bare_embedder()
        e.prefixes = Embedder(model_name="BAAI/bge-small-en-v1.5").prefixes
        e.encode_query("what is entanglement")
        sent = e._model.encode.call_args[0][0]
        assert sent == [
            "Represent this sentence for searching relevant passages: what is entanglement"
        ]

    def test_profile_distinguishes_model_and_prefixes(self) -> None:
        bge = Embedder(model_name="BAAI/bge-small-en-v1.5").profile
        nomic = Embedder(model_name="nomic-ai/nomic-embed-text-v1.5").profile
        assert bge != nomic
        assert "bge-small" in bge
