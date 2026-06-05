from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from typing import Iterable

import httpx
import numpy as np


DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"
DEFAULT_EMBEDDING_DIMENSIONS = 1536
FAKE_EMBEDDING_DIMENSIONS = 64


class EmbeddingProvider(ABC):
    name: str
    model: str
    dimensions: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class FakeEmbeddingProvider(EmbeddingProvider):
    name = "fake"
    model = "fake-hashing-v1"
    dimensions = FAKE_EMBEDDING_DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_fake_embed(text, self.dimensions) for text in texts]


class OpenRouterEmbeddingProvider(EmbeddingProvider):
    name = "openrouter"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.model = model
        self.dimensions = dimensions
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for OpenRouter embeddings")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        data = sorted(payload["data"], key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in data]


def provider_from_env() -> EmbeddingProvider:
    provider = os.getenv("YKM_EMBEDDING_PROVIDER", "fake").strip().lower()
    if provider == "fake":
        return FakeEmbeddingProvider()
    if provider == "openrouter":
        model = os.getenv("YKM_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        dimensions = int(os.getenv("YKM_EMBEDDING_DIMENSIONS", str(DEFAULT_EMBEDDING_DIMENSIONS)))
        return OpenRouterEmbeddingProvider(model=model, dimensions=dimensions)
    raise ValueError(f"Unknown embedding provider: {provider}")


def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    av = np.array(list(a), dtype=np.float32)
    bv = np.array(list(b), dtype=np.float32)
    denom = float(np.linalg.norm(av) * np.linalg.norm(bv))
    if denom == 0:
        return 0.0
    return float(np.dot(av, bv) / denom)


def _fake_embed(text: str, dimensions: int) -> list[float]:
    vector = np.zeros(dimensions, dtype=np.float32)
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = np.linalg.norm(vector)
    if norm:
        vector = vector / norm
    return vector.astype(float).tolist()


def _tokens(text: str) -> list[str]:
    return [part.lower() for part in "".join(_normalize_chars(text)).split() if part]


def _normalize_chars(text: str) -> Iterable[str]:
    for char in text:
        yield char if char.isalnum() else " "

