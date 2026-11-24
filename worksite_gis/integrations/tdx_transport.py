from datetime import timedelta
from email.utils import parsedate_to_datetime

import httpx
from django.conf import settings
from django.utils import timezone


class TdxError(httpx.HTTPError):
    def __init__(self, reason, retry_after_seconds=None):
        super().__init__(reason)
        self.retry_after_seconds = retry_after_seconds


def classify_tdx_error(exc):
    if isinstance(exc, TdxError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return TdxError("timeout")
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            raw = exc.response.headers.get("Retry-After", "")
            try:
                delay = int(raw)
            except (ValueError, OverflowError):
                try:
                    delay = int((parsedate_to_datetime(raw) - timezone.now()).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = None
            return TdxError("rate_limited", None if delay is None else min(300, max(0, delay)))
        if code == 401:
            return TdxError("token_rejected")
        if code == 403:
            return TdxError("forbidden")
        return TdxError("upstream_unavailable" if code >= 500 else "upstream_http_error")
    return TdxError("network_failure" if isinstance(exc, httpx.HTTPError) else "invalid_response")


class TdxTransport:
    """Shared OAuth and HTTP transport for the TDX adapter."""

    token_url = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"

    def __init__(self, client_id: str, client_secret: str, client=None, timeout: float = 10):
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = client or httpx.Client(timeout=timeout, trust_env=False)
        self._token = ""
        self._token_expires_at = None

    @property
    def configuration_error(self):
        if getattr(settings, "WORKSITE_DEMO", False):
            return "disabled"
        if not self.client_id or not self.client_secret:
            return "not_configured"
        return None

    def get_token(self) -> str:
        if self.configuration_error:
            raise TdxError(self.configuration_error)
        now = timezone.now()
        if self._token and self._token_expires_at and self._token_expires_at > now + timedelta(seconds=30):
            return self._token
        response = self.client.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        token = payload["access_token"]
        lifetime = int(payload.get("expires_in", 300))
        if not isinstance(token, str) or not token.strip():
            raise TdxError("invalid_response")
        if lifetime < 1:
            raise TdxError("token_expired")
        self._token = token
        self._token_expires_at = now + timedelta(seconds=min(lifetime, 86400))
        return self._token

    def get(self, url, params=None):
        token = self.get_token()
        query = {"$format": "JSON", **(params or {})}
        response = self.client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=query,
        )
        if response.status_code == 401:
            self._token = ""
            self._token_expires_at = None
        response.raise_for_status()
        return response.json()
