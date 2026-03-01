"""Unit tests for Embedder — covers uncovered lines using a mocked model."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
from obsidian_search.embedding.embedder import Embedder


def _mock_model(output_dims: int = 768) -> MagicMock:
    """Return a SentenceTransformer mock whose encode() returns unit vectors."""
    model = MagicMock()
    model.encode.side_effect = lambda texts, **kwargs: np.random.rand(
        len(texts), output_dims
    ).astype(np.float32)
    return model


class TestEmbedderLoad:
    def test_load_calls_sentence_transformer(self) -> None:
        """Covers lines 20-26: _load initialises the model on first call.

        SentenceTransformer is a local import inside _load(), so we patch
        the module it comes from rather than the embedder module's namespace.
        """
        e = Embedder(model_name="fake-model")
        mock_model = _mock_model()
        with patch(
            "sentence_transformers.SentenceTransformer",
            return_value=mock_model,
        ) as MockST:
            loaded = e._load()
        MockST.assert_called_once_with("fake-model", trust_remote_code=True)
        assert loaded is mock_model

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
        """Covers lines 31-38: encode() calls model.encode and wraps result."""
        e = Embedder.__new__(Embedder)
        e.dims = 768
        e._model = _mock_model(768)
        result = e.encode(["hello world"])
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert result.shape == (1, 768)

    def test_encode_batch_shape(self) -> None:
        e = Embedder.__new__(Embedder)
        e.dims = 768
        e._model = _mock_model(768)
        result = e.encode(["text one", "text two", "text three"])
        assert result.shape == (3, 768)

    def test_encode_passes_normalize_and_batch_size(self) -> None:
        """Verify the model is called with the correct kwargs."""
        e = Embedder.__new__(Embedder)
        e.dims = 768
        mock_model = _mock_model(768)
        e._model = mock_model
        e.encode(["hello"])
        _, kwargs = mock_model.encode.call_args
        assert kwargs.get("normalize_embeddings") is True
        assert kwargs.get("batch_size") == 32
        assert kwargs.get("show_progress_bar") is False


class TestEmbedderPrefixes:
    def test_encode_documents_adds_index_prefix(self) -> None:
        """Covers lines 41-42: encode_documents prepends search_document prefix."""
        e = Embedder.__new__(Embedder)
        e.dims = 768
        mock_model = _mock_model(768)
        e._model = mock_model
        e.encode_documents(["my note content"])
        call_args = mock_model.encode.call_args[0][0]
        assert call_args[0].startswith("search_document: ")

    def test_encode_query_adds_query_prefix(self) -> None:
        """Covers line 45: encode_query prepends search_query prefix."""
        e = Embedder.__new__(Embedder)
        e.dims = 768
        mock_model = _mock_model(768)
        e._model = mock_model
        result = e.encode_query("quantum computing")
        call_args = mock_model.encode.call_args[0][0]
        assert call_args[0].startswith("search_query: ")
        assert result.shape == (768,)
        assert result.dtype == np.float32

    def test_encode_query_returns_1d_array(self) -> None:
        e = Embedder.__new__(Embedder)
        e.dims = 768
        e._model = _mock_model(768)
        result = e.encode_query("test query")
        assert result.ndim == 1
        assert len(result) == 768


class TestEmbedderSearcher:
    def test_default_model_name(self) -> None:
        e = Embedder()
        assert e.model_name == "nomic-ai/nomic-embed-text-v1.5"

    def test_default_dims(self) -> None:
        e = Embedder()
        assert e.dims == 768

    def test_model_none_before_load(self) -> None:
        e = Embedder()
        assert e._model is None
