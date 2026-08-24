"""Groq API Client wrapper for tool-calling chat completions with model discovery and retry."""

from __future__ import annotations

import logging
import time
from typing import Any

from groq import APIConnectionError, APIError, Groq, RateLimitError

logger = logging.getLogger("PRReviewBot.GroqClient")


class GroqClient:
    """Wrapper around the Groq Python SDK with dynamic model discovery and retry handling.

    Why Groq?
    - Ultra-fast LPUs achieve 300-800 tok/sec, reducing multi-step agent latency from >1min to 5-8s.
    - Full OpenAI-compatible function-calling support.
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "llama-3.3-70b-versatile",
        temperature: float = 0.1,
        mock_mode: bool = False,
    ):
        self.api_key = api_key
        self.default_model = default_model
        self.temperature = temperature
        self.mock_mode = mock_mode
        self._client: Groq | None = None

        if not self.mock_mode and api_key:
            self._client = Groq(api_key=api_key)

    def list_available_models(self) -> list[str]:
        """Query the Groq API to retrieve all models accessible to the current API key."""
        if self.mock_mode:
            return [
                "llama-3.3-70b-versatile",
                "llama-3.1-70b-versatile",
                "llama-3.1-8b-instant",
                "llama3-70b-8192",
            ]

        if not self._client:
            raise RuntimeError("Cannot list Groq models without a configured API key.")

        try:
            models_page = self._client.models.list()
            model_ids = [m.id for m in models_page.data if getattr(m, "active", True)]
            return sorted(model_ids)
        except Exception as err:
            logger.error("Failed to list available Groq models: %s", err)
            raise

    def generate_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_retries: int = 3,
        initial_delay: float = 2.0,
    ) -> Any:
        """Call Groq chat completions with exponential backoff on rate limits or connection errors."""
        if self.mock_mode or not self._client:
            raise RuntimeError("Cannot execute live Groq completion in mock mode.")

        selected_model = model or self.default_model
        delay = initial_delay

        for attempt in range(1, max_retries + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": selected_model,
                    "messages": messages,
                    "temperature": self.temperature,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                logger.debug(
                    "Dispatching completion request to Groq model '%s' (attempt %d)",
                    selected_model,
                    attempt,
                )
                response = self._client.chat.completions.create(**kwargs)
                return response

            except RateLimitError as err:
                logger.warning(
                    "Groq RateLimitError on attempt %d/%d for '%s'. Backing off for %.1fs. Error: %s",
                    attempt,
                    max_retries,
                    selected_model,
                    delay,
                    err,
                )
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
            except APIConnectionError as err:
                logger.warning(
                    "Groq API connection failure on attempt %d/%d for '%s': %s. Retrying...",
                    attempt,
                    max_retries,
                    selected_model,
                    err,
                )
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
            except APIError as err:
                err_str = str(err).lower()
                is_model_not_found = (
                    getattr(err, "status_code", None) == 404
                    or "model_not_found" in err_str
                    or "does not exist" in err_str
                    or "do not have access" in err_str
                )
                if is_model_not_found:
                    try:
                        available = self.list_available_models()
                        available_str = ", ".join(available)
                        raise ValueError(
                            f"Model '{selected_model}' is not accessible on this Groq account. "
                            f"Available models for your API key: [{available_str}]"
                        ) from err
                    except ValueError:
                        raise
                    except Exception:
                        raise err from None
                else:
                    logger.error("Groq API error on model '%s': %s", selected_model, err)
                    raise
            except Exception as err:
                logger.error(
                    "Unexpected error calling Groq with model '%s': %s", selected_model, err
                )
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
