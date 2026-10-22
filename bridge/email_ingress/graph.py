"""Microsoft Graph mail transport (S0-arch-13) — the deployed feed into ``land()``.

The intake mailbox (``rfp-quotes@…``) lives in M365. The app registration holds the
Graph ``Mail.Read`` *application* permission, restricted by an Exchange Online
Application Access Policy (or RBAC for Applications) to that ONE mailbox — never the
brokers' own mailboxes. Graph POSTs a change notification per new message to the
email webhook (``bridge.edge.email_ingress.build_email_app``), which fetches the raw
MIME here and lands it on the ``email_ingress`` seam.

Security posture:

* **Fixed hosts only** (``login.microsoftonline.com`` / ``graph.microsoft.com``) and
  ``follow_redirects=False`` — a 3xx is a failure, never followed.
* **Always the CONFIGURED mailbox.** ``fetch_mime`` builds the URL from the mailbox it
  was configured with and the message id alone; it never reads the notification's
  ``resource`` path (which carries a user GUID anyway). A forged notification can at
  most make the service read its own mailbox.
* The message id is validated (``^[A-Za-z0-9+/=_-]{1,512}$``) before any request, and
  path-quoted with ``safe=''``.
* The body is streamed and refused past the raw-message cap (:class:`MessageTooLarge`,
  a ``ValueError("oversize")``). It is the ONLY ``ValueError`` the webhook treats as a
  permanent refusal; any other failure (a garbled token response included) is a
  :class:`GraphError` → 503, so Graph retries and no mail is acked unlanded.
* Short timeouts (2.5 s): Graph wants the notification answered within ~3 s.

``httpx`` is imported lazily (the repo idiom for an already-present dependency);
``transport`` is injectable so tests drive an ``httpx.MockTransport``.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from bridge.email_ingress.mime import max_mail_bytes

LOGIN_HOST = "https://login.microsoftonline.com"
GRAPH_HOST = "https://graph.microsoft.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

TENANT_ENV = "BRIDGE_EMAIL_GRAPH_TENANT"
CLIENT_ID_ENV = "BRIDGE_EMAIL_GRAPH_CLIENT_ID"
CLIENT_SECRET_ENV = "BRIDGE_EMAIL_GRAPH_CLIENT_SECRET"
MAILBOX_ENV = "BRIDGE_EMAIL_MAILBOX"
CLIENT_STATE_ENV = "BRIDGE_EMAIL_CLIENT_STATE"

MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9+/=_-]{1,512}$")
# Below every documented maximum for an Outlook-message subscription; renew before it.
DEFAULT_SUBSCRIPTION_MINUTES = 4230
MAX_CLIENT_STATE = 128
DEFAULT_TIMEOUT_S = 2.5


class GraphError(RuntimeError):
    """A failed Graph call (non-2xx or transport). ``status`` is 0 for transport errors."""

    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class MessageTooLarge(ValueError):
    """The raw message exceeds the cap — a permanent refusal (recorded, never retried)."""

    def __init__(self) -> None:
        super().__init__("oversize")


def validate_message_id(message_id: str) -> str:
    """Return ``message_id`` when it is a plausible Graph id, else ``ValueError``."""
    if not isinstance(message_id, str) or not MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError(f"invalid Graph message id {message_id!r}")
    return message_id


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Graph mail transport requires {name}")
    return value


def client_state_from_env() -> str:
    """``BRIDGE_EMAIL_CLIENT_STATE`` (the notification shared secret); RuntimeError if unset."""
    value = _require_env(CLIENT_STATE_ENV)
    if len(value) > MAX_CLIENT_STATE:
        raise ValueError(f"{CLIENT_STATE_ENV} must be <= {MAX_CLIENT_STATE} chars")
    return value


class GraphMailClient:
    """A minimal client-credentials Graph client for ONE mailbox."""

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        mailbox: str,
        transport=None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        for name, value in (
            ("tenant_id", tenant_id),
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("mailbox", mailbox),
        ):
            if not value:
                raise ValueError(f"GraphMailClient requires {name}")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self._client_secret = client_secret
        self.mailbox = mailbox
        self._transport = transport
        self._timeout = timeout
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, **kwargs) -> GraphMailClient:
        """Build from ``BRIDGE_EMAIL_GRAPH_{TENANT,CLIENT_ID,CLIENT_SECRET}`` + ``BRIDGE_EMAIL_MAILBOX``."""
        return cls(
            tenant_id=_require_env(TENANT_ENV),
            client_id=_require_env(CLIENT_ID_ENV),
            client_secret=_require_env(CLIENT_SECRET_ENV),
            mailbox=_require_env(MAILBOX_ENV),
            **kwargs,
        )

    def _http(self):
        import httpx

        return httpx.Client(
            transport=self._transport, timeout=self._timeout, follow_redirects=False
        )

    def _access_token(self) -> str:
        import httpx

        with self._lock:
            if self._token and time.monotonic() < self._token_expiry:
                return self._token
            url = f"{LOGIN_HOST}/{quote(self.tenant_id, safe='')}/oauth2/v2.0/token"
            try:
                with self._http() as http:
                    resp = http.post(
                        url,
                        data={
                            "grant_type": "client_credentials",
                            "scope": GRAPH_SCOPE,
                            "client_id": self.client_id,
                            "client_secret": self._client_secret,
                        },
                    )
            except httpx.HTTPError as exc:
                raise GraphError(f"token request failed: {type(exc).__name__}") from exc
            if not resp.is_success:
                raise GraphError(
                    f"token request failed: HTTP {resp.status_code}",
                    status=resp.status_code,
                )
            try:
                body = resp.json()
                token = body.get("access_token")
                expires_in = int(body.get("expires_in", 3599))
            except (ValueError, TypeError, AttributeError) as exc:
                # A garbled 2xx (a proxy page, a non-object) is transient, never
                # "oversize": a ValueError here would ack the mail without landing it.
                raise GraphError("token response was not a JSON token object") from exc
            if not token or not isinstance(token, str):
                raise GraphError("token response carried no access_token")
            self._token = token
            self._token_expiry = time.monotonic() + max(expires_in - 60, 0)
            return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _mailbox_path(self) -> str:
        return f"{GRAPH_HOST}/v1.0/users/{quote(self.mailbox, safe='@')}"

    def fetch_mime(self, message_id: str, *, max_bytes: int | None = None) -> bytes:
        """GET ``/users/{mailbox}/messages/{id}/$value`` — the raw RFC 5322 bytes.

        ``ValueError`` for an invalid id (no request issued), :class:`MessageTooLarge`
        for an oversize body; ``GraphError`` for a non-2xx answer (redirects included) or a transport error.
        """
        import httpx

        validate_message_id(message_id)
        limit = max_mail_bytes() if max_bytes is None else max_bytes
        url = f"{self._mailbox_path()}/messages/{quote(message_id, safe='')}/$value"
        headers = self._headers()
        try:
            with self._http() as http, http.stream("GET", url, headers=headers) as resp:
                if not resp.is_success:
                    raise GraphError(
                        f"message fetch failed: HTTP {resp.status_code}",
                        status=resp.status_code,
                    )
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise MessageTooLarge()
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise MessageTooLarge()
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError as exc:
            raise GraphError(f"message fetch failed: {type(exc).__name__}") from exc

    def latest_message_ids(self, top: int = 1) -> list[str]:
        """The newest Inbox message ids (the operator / live-test probe)."""
        import httpx

        url = f"{self._mailbox_path()}/mailFolders('Inbox')/messages"
        params = {
            "$top": str(top),
            "$orderby": "receivedDateTime desc",
            "$select": "id",
        }
        try:
            with self._http() as http:
                resp = http.get(url, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise GraphError(f"message list failed: {type(exc).__name__}") from exc
        if not resp.is_success:
            raise GraphError(
                f"message list failed: HTTP {resp.status_code}", status=resp.status_code
            )
        return [item["id"] for item in resp.json().get("value", [])]

    @staticmethod
    def _expiry(minutes: int) -> str:
        if minutes < 1:
            raise ValueError("subscription minutes must be >= 1")
        at = datetime.now(UTC) + timedelta(minutes=minutes)
        return at.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

    def _send(self, method: str, url: str, body: dict) -> dict:
        import httpx

        try:
            with self._http() as http:
                resp = http.request(method, url, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise GraphError(f"{method} {url} failed: {type(exc).__name__}") from exc
        if not resp.is_success:
            raise GraphError(
                f"{method} {url} failed: HTTP {resp.status_code}",
                status=resp.status_code,
            )
        return resp.json()

    def create_subscription(
        self,
        notification_url: str,
        client_state: str,
        *,
        minutes: int = DEFAULT_SUBSCRIPTION_MINUTES,
    ) -> dict:
        """POST ``/subscriptions`` for ``created`` messages in the mailbox's Inbox."""
        if not notification_url.startswith("https://"):
            raise ValueError("Graph requires an https notification URL")
        if not client_state or len(client_state) > MAX_CLIENT_STATE:
            raise ValueError(f"client_state must be 1..{MAX_CLIENT_STATE} chars")
        return self._send(
            "POST",
            f"{GRAPH_HOST}/v1.0/subscriptions",
            {
                "changeType": "created",
                "notificationUrl": notification_url,
                "resource": f"users/{self.mailbox}/mailFolders('Inbox')/messages",
                "expirationDateTime": self._expiry(minutes),
                "clientState": client_state,
            },
        )

    def renew_subscription(
        self, subscription_id: str, *, minutes: int = DEFAULT_SUBSCRIPTION_MINUTES
    ) -> dict:
        """PATCH ``/subscriptions/{id}`` with a new expiry."""
        if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", subscription_id or ""):
            raise ValueError(f"invalid subscription id {subscription_id!r}")
        return self._send(
            "PATCH",
            f"{GRAPH_HOST}/v1.0/subscriptions/{subscription_id}",
            {"expirationDateTime": self._expiry(minutes)},
        )


__all__ = [
    "CLIENT_STATE_ENV",
    "DEFAULT_SUBSCRIPTION_MINUTES",
    "MAILBOX_ENV",
    "GraphError",
    "GraphMailClient",
    "MessageTooLarge",
    "client_state_from_env",
    "validate_message_id",
]
