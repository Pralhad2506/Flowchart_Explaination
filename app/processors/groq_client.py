"""
processors/groq_client.py — Async Groq API wrapper with retry logic.

Provides:
  - GroqClient: async context manager with connection pooling
  - ask(): send a prompt, get back a text response
  - ask_with_system(): send system + user messages
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from groq import AsyncGroq, APIStatusError, APIConnectionError

from  app.config import settings
from  app.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY = 2.0   # seconds; doubled on each retry


class GroqClient:
    """Thin async wrapper around the Groq AsyncGroq client."""

    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. "
                "Export it as an environment variable or add it to .env"
            )
        self._client = AsyncGroq(api_key=settings.groq_api_key)

    async def ask(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Send a single prompt to Groq and return the text response.

        Parameters
        ----------
        prompt : str
            The user message.
        system_prompt : str, optional
            Override the default system prompt.
        max_tokens : int, optional
            Override settings.groq_max_tokens.
        temperature : float, optional
            Override settings.groq_temperature.

        Returns
        -------
        str
            Model response text.

        Raises
        ------
        RuntimeError
            After *_MAX_RETRIES* failed attempts.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        return await self._call_with_retry(
            messages=messages,
            max_tokens=max_tokens or settings.groq_max_tokens,
            temperature=temperature or settings.groq_temperature,
        )

    async def ask_with_system(
        self,
        system_prompt: str,
        user_messages: List[dict],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Full message list version for multi-turn or complex prompts.
        *user_messages* should be a list of {"role": ..., "content": ...} dicts.
        """
        messages = [{"role": "system", "content": system_prompt}] + user_messages
        return await self._call_with_retry(
            messages=messages,
            max_tokens=max_tokens or settings.groq_max_tokens,
            temperature=temperature or settings.groq_temperature,
        )

    async def _call_with_retry(
        self, messages: list, max_tokens: int, temperature: float
    ) -> str:
        delay = _RETRY_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=settings.groq_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                content = response.choices[0].message.content
                logger.debug(
                    "Groq call succeeded on attempt %d (tokens used: %s)",
                    attempt,
                    getattr(response.usage, "total_tokens", "?"),
                )
                return content or ""

            except APIStatusError as exc:
                logger.warning(
                    "Groq API status error (attempt %d/%d): %s — %s",
                    attempt, _MAX_RETRIES, exc.status_code, exc.message,
                )
                if exc.status_code in (429, 503):
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    raise

            except APIConnectionError as exc:
                logger.warning(
                    "Groq connection error (attempt %d/%d): %s",
                    attempt, _MAX_RETRIES, exc,
                )
                await asyncio.sleep(delay)
                delay *= 2

            except Exception as exc:
                logger.error("Unexpected Groq error: %s", exc)
                raise

        raise RuntimeError(
            f"Groq API call failed after {_MAX_RETRIES} attempts."
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()

    async def __aenter__(self) -> "GroqClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()