"""Inbound email intake edge (S0-arch-13): Graph webhook + ``RfpBoard`` processor.

Splits the public Graph landing webhook (:mod:`bridge.edge.email_webhook`) from the
board-owning drain processor (:mod:`bridge.edge.email_processor`) while preserving the
canonical ``bridge.edge.email_ingress`` import surface. See ``design/email-intake.md``.
"""

from __future__ import annotations

from bridge.edge.email_processor import (
    MAILBOX_ENV as MAILBOX_ENV,
)
from bridge.edge.email_processor import (
    READABLE_MIME_TYPES as READABLE_MIME_TYPES,
)
from bridge.edge.email_processor import (
    REASON_AMBIGUOUS as REASON_AMBIGUOUS,
)
from bridge.edge.email_processor import (
    REASON_NO_ATTACHMENTS as REASON_NO_ATTACHMENTS,
)
from bridge.edge.email_processor import (
    REASON_NO_TOKEN as REASON_NO_TOKEN,
)
from bridge.edge.email_processor import (
    REASON_PROCESS_UNAVAILABLE as REASON_PROCESS_UNAVAILABLE,
)
from bridge.edge.email_processor import (
    REASON_SENDER_AUTH as REASON_SENDER_AUTH,
)
from bridge.edge.email_processor import (
    REASON_UNSUPPORTED as REASON_UNSUPPORTED,
)
from bridge.edge.email_processor import (
    EmailProcessor as _EmailProcessor,
)
from bridge.edge.email_processor import (
    FiledAttachment as FiledAttachment,
)
from bridge.edge.email_processor import (
    MailOutcome as MailOutcome,
)
from bridge.edge.email_processor import (
    UnreadableAttachment as UnreadableAttachment,
)
from bridge.edge.email_webhook import (
    DEFAULT_DRAIN_INTERVAL as DEFAULT_DRAIN_INTERVAL,
)
from bridge.edge.email_webhook import (
    DRAIN_INTERVAL_ENV as DRAIN_INTERVAL_ENV,
)
from bridge.edge.email_webhook import (
    MAX_NOTIFICATION_BYTES as MAX_NOTIFICATION_BYTES,
)
from bridge.edge.email_webhook import (
    MAX_VALIDATION_TOKEN as MAX_VALIDATION_TOKEN,
)
from bridge.edge.email_webhook import (
    NOTIFICATIONS_PATH as NOTIFICATIONS_PATH,
)
from bridge.edge.email_webhook import (
    build_email_app as build_email_app,
)
from bridge.edge.email_webhook import (
    drain_interval_from_env as drain_interval_from_env,
)
from bridge.edge.email_webhook import (
    email_drain_loop as email_drain_loop,
)
from bridge.edge.email_webhook import (
    email_processor_from_env as email_processor_from_env,
)
from bridge.edge.email_webhook import (
    mount_email_drain_routes as mount_email_drain_routes,
)
from bridge.edge.email_webhook import (
    oversize_key as oversize_key,
)
from bridge.edge.email_webhook import (
    serve_email_webhook_container as serve_email_webhook_container,
)

EmailProcessor = _EmailProcessor

__all__ = [
    "DEFAULT_DRAIN_INTERVAL",
    "DRAIN_INTERVAL_ENV",
    "MAILBOX_ENV",
    "MAX_NOTIFICATION_BYTES",
    "MAX_VALIDATION_TOKEN",
    "NOTIFICATIONS_PATH",
    "READABLE_MIME_TYPES",
    "REASON_AMBIGUOUS",
    "REASON_NO_ATTACHMENTS",
    "REASON_NO_TOKEN",
    "REASON_PROCESS_UNAVAILABLE",
    "REASON_SENDER_AUTH",
    "REASON_UNSUPPORTED",
    "EmailProcessor",
    "FiledAttachment",
    "MailOutcome",
    "UnreadableAttachment",
    "build_email_app",
    "drain_interval_from_env",
    "email_drain_loop",
    "email_processor_from_env",
    "mount_email_drain_routes",
    "oversize_key",
    "serve_email_webhook_container",
]
