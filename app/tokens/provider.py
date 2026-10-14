"""Token providers: the only place that knows how to talk to a Token Management API.

The bot depends on ``TokenProvider`` only. ``MockTokenProvider`` targets the demo mock API;
``QueueProTokenProvider`` is where the real Queue Pro contract gets mapped once it is known.
Switch with ``TOKEN_PROVIDER=mock|queuepro``.
"""

import asyncio
import logging
from datetime import datetime
from typing import Protocol

import httpx
from pydantic import BaseModel

log = logging.getLogger(__name__)


class TokenRequest(BaseModel):
    branch_code: str
    service_code: str
    mobile_no: str
    channel: str = "WHATSAPP"
    request_id: str  # idempotency key: a retry with the same id must not create a second token


class TokenResult(BaseModel):
    token_no: str
    issued_at: datetime
    waiting_count: int | None = None


class TokenProviderError(Exception):
    """``code`` is one of: BRANCH_CLOSED, SERVICE_UNAVAILABLE, LIMIT_REACHED, INVALID_REQUEST, UNAVAILABLE."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message


class TokenProvider(Protocol):
    async def issue_token(self, request: TokenRequest) -> TokenResult: ...


class HttpTokenProvider:
    """POSTs a JSON body and maps the response. Subclasses override the two mapping hooks."""

    retries_on_network_error = 1

    def __init__(self, http: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None) -> None:
        self._http = http
        self._url = url
        self._headers = headers or {}

    def build_payload(self, request: TokenRequest) -> dict:
        return request.model_dump()

    def parse_response(self, data: dict) -> TokenResult:
        return TokenResult(token_no=data["token_no"], issued_at=data["issued_at"],
                           waiting_count=data.get("waiting_count"))

    def parse_error(self, status: int, data: dict) -> TokenProviderError:
        return TokenProviderError(data.get("error_code", "UNAVAILABLE"), data.get("message", ""))

    async def issue_token(self, request: TokenRequest) -> TokenResult:
        payload = self.build_payload(request)
        for attempt in range(self.retries_on_network_error + 1):
            try:
                resp = await self._http.post(self._url, json=payload, headers=self._headers)
                break
            except httpx.TransportError as exc:  # safe to retry: request_id makes it idempotent
                log.warning("Token API network error (attempt %d): %s", attempt + 1, exc)
                if attempt == self.retries_on_network_error:
                    raise TokenProviderError("UNAVAILABLE", str(exc)) from exc
                await asyncio.sleep(0.5)

        if resp.status_code >= 500:
            raise TokenProviderError("UNAVAILABLE", f"Token API returned {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise TokenProviderError("UNAVAILABLE", "Token API returned non-JSON") from exc
        if resp.status_code >= 400:
            raise self.parse_error(resp.status_code, data.get("detail", data))
        return self.parse_response(data)


class MockTokenProvider(HttpTokenProvider):
    """Contract of ``app.mock_api`` - which is also the *assumed* Queue Pro contract."""


class QueueProTokenProvider(HttpTokenProvider):
    """Real Queue Pro Token Management API.

    TODO(integration): once the real endpoint is shared, adjust these mappings (field names,
    auth header, date format, error codes). Nothing else in the bot needs to change.
    """

    def __init__(self, http: httpx.AsyncClient, url: str, api_key: str = "") -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        super().__init__(http, url, headers)

    def build_payload(self, request: TokenRequest) -> dict:
        return request.model_dump()

    def parse_response(self, data: dict) -> TokenResult:
        return super().parse_response(data)
