"""Unit tests for Groq Client wrapper and retry logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from groq import APIConnectionError, APIError, RateLimitError

from src.agent.groq_client import GroqClient


class TestGroqClient:
    """Test suite for Groq API client."""

    def test_mock_mode_guard(self):
        client = GroqClient(mock_mode=True)
        with pytest.raises(RuntimeError, match="Cannot execute live Groq completion"):
            client.generate_completion(messages=[{"role": "user", "content": "hello"}])

    def test_list_available_models_mock_mode(self):
        client = GroqClient(mock_mode=True)
        models = client.list_available_models()
        assert "llama-3.1-70b-versatile" in models
        assert "llama-3.1-8b-instant" in models

    def test_list_available_models_live_mock(self):
        client = GroqClient(api_key="gsk-test123456", mock_mode=False)
        mock_groq = MagicMock()

        m1 = MagicMock()
        m1.id = "llama-3.1-70b-versatile"
        m1.active = True

        m2 = MagicMock()
        m2.id = "llama-3.1-8b-instant"
        m2.active = True

        mock_page = MagicMock()
        mock_page.data = [m1, m2]
        mock_groq.models.list.return_value = mock_page
        client._client = mock_groq

        models = client.list_available_models()
        assert models == ["llama-3.1-70b-versatile", "llama-3.1-8b-instant"]

    def test_successful_completion(self):
        client = GroqClient(api_key="gsk-test123456", mock_mode=False)
        mock_groq = MagicMock()
        mock_response = MagicMock()
        mock_groq.chat.completions.create.return_value = mock_response
        client._client = mock_groq

        messages = [{"role": "user", "content": "Review this code"}]
        tools = [{"type": "function", "function": {"name": "test_tool"}}]

        resp = client.generate_completion(messages=messages, tools=tools)
        assert resp == mock_response
        mock_groq.chat.completions.create.assert_called_once_with(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.1,
            tools=tools,
            tool_choice="auto",
        )

    def test_retry_on_rate_limit(self):
        client = GroqClient(api_key="gsk-test123456", mock_mode=False)
        mock_groq = MagicMock()

        # Build mock response for RateLimitError
        mock_req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        mock_http_resp = httpx.Response(429, request=mock_req)
        rate_limit_err = RateLimitError(message="Rate limit exceeded", response=mock_http_resp, body={"error": "rate_limit"})

        mock_success_resp = MagicMock()
        mock_groq.chat.completions.create.side_effect = [rate_limit_err, mock_success_resp]
        client._client = mock_groq

        resp = client.generate_completion(
            messages=[{"role": "user", "content": "test"}],
            max_retries=2,
            initial_delay=0.01,
        )

        assert resp == mock_success_resp
        assert mock_groq.chat.completions.create.call_count == 2

    def test_connection_error_retry(self):
        client = GroqClient(api_key="gsk-test123456", mock_mode=False)
        mock_groq = MagicMock()

        mock_req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        conn_err = APIConnectionError(request=mock_req)

        mock_success_resp = MagicMock()
        mock_groq.chat.completions.create.side_effect = [conn_err, mock_success_resp]
        client._client = mock_groq

        resp = client.generate_completion(
            messages=[{"role": "user", "content": "test"}],
            max_retries=2,
            initial_delay=0.01,
        )

        assert resp == mock_success_resp
        assert mock_groq.chat.completions.create.call_count == 2

    def test_model_not_found_raises_informative_error(self):
        client = GroqClient(api_key="gsk-test123456", default_model="non-existent-model", mock_mode=False)
        mock_groq = MagicMock()

        mock_req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        mock_http_resp = httpx.Response(404, request=mock_req)
        not_found_err = APIError(
            message="The model `non-existent-model` does not exist or you do not have access to it.",
            request=mock_req,
            body={"error": {"code": "model_not_found"}},
        )
        not_found_err.response = mock_http_resp

        m1 = MagicMock()
        m1.id = "llama-3.1-8b-instant"
        m1.active = True
        mock_page = MagicMock()
        mock_page.data = [m1]
        mock_groq.models.list.return_value = mock_page

        mock_groq.chat.completions.create.side_effect = not_found_err
        client._client = mock_groq

        with pytest.raises(ValueError, match="not accessible on this Groq account"):
            client.generate_completion(messages=[{"role": "user", "content": "test"}])
