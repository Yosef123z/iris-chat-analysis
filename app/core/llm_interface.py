"""Provider abstraction for LLM-backed contract features."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Type, TypeVar

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class AIProviderError(RuntimeError):
    """Base exception for provider failures that should be surfaced as 503."""


class AIProviderConfigurationError(AIProviderError):
    """Raised when required provider configuration is missing."""


async def retry_with_backoff(
    func,
    *args,
    max_retries: int = 2,
    timeout_seconds: int = 25,
    **kwargs,
):
    last_exception: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            last_exception = exc
            logger.warning(
                "Attempt %d/%d failed: %s",
                attempt + 1,
                max_retries,
                type(exc).__name__,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(2**attempt)
    raise AIProviderError(str(last_exception)) from last_exception


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """Standard chat completion."""

    @abstractmethod
    async def structured_output(
        self,
        messages: List[Dict[str, str]],
        model: str,
        output_model: Type[T],
        temperature: float = 0.0,
    ) -> T:
        """Generate a structured Pydantic object from the LLM response."""

    @abstractmethod
    def get_embeddings_model(self) -> Embeddings:
        """Return an embeddings model for auxiliary features that need it."""


class OpenAIProvider(LLMProvider):
    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY.strip()
        self.client = AsyncOpenAI(api_key=self.api_key) if self.api_key else None
        self._embeddings = (
            OpenAIEmbeddings(openai_api_key=self.api_key) if self.api_key else None
        )

    def _require_configured(self) -> None:
        if not self.api_key or self.client is None or self._embeddings is None:
            raise AIProviderConfigurationError("OPENAI_API_KEY is required for this endpoint")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        self._require_configured()

        async def _request():
            assert self.client is not None
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""

        return await retry_with_backoff(_request)

    async def structured_output(
        self,
        messages: List[Dict[str, str]],
        model: str,
        output_model: Type[T],
        temperature: float = 0.0,
    ) -> T:
        self._require_configured()

        async def _request():
            assert self.client is not None
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            return output_model.model_validate_json(response.choices[0].message.content)

        return await retry_with_backoff(_request)

    def get_embeddings_model(self) -> Embeddings:
        self._require_configured()
        assert self._embeddings is not None
        return self._embeddings
