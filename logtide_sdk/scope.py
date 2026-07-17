"""Scope: per-request/per-task context merged into every captured entry.

Built on :mod:`contextvars`, so scopes are isolated across threads and
asyncio tasks. Use :func:`push_scope` for per-request isolation; the
module-level helpers (:func:`set_tag`, :func:`set_user`,
:func:`add_breadcrumb`, ...) operate on the current scope.

Reserved metadata keys written by the scope (spec 003 §3): ``tags``,
``user``, ``breadcrumbs``. ``session_id`` and the trace context go to the
entry's top-level fields.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from logtide_sdk.models import LogEntry

__all__ = [
    "Breadcrumb",
    "Scope",
    "User",
    "add_breadcrumb",
    "get_current_scope",
    "push_scope",
    "set_extra",
    "set_session_id",
    "set_tag",
    "set_user",
]

DEFAULT_MAX_BREADCRUMBS = 100


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass(frozen=True)
class User:
    """User context (spec 003 §6). All fields optional."""

    id: str | None = None
    email: str | None = None
    username: str | None = None
    ip: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            k: v
            for k, v in (
                ("id", self.id),
                ("email", self.email),
                ("username", self.username),
                ("ip", self.ip),
            )
            if v is not None
        }


@dataclass
class Breadcrumb:
    """A discrete event recorded before an entry (spec 003 §5)."""

    message: str = ""
    type: str = "custom"  # http | navigation | ui | console | query | error | custom
    category: str | None = None
    data: dict[str, Any] | None = None
    level: str = "info"
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "message": self.message,
            "type": self.type,
            "level": self.level,
            "timestamp": self.timestamp,
        }
        if self.category is not None:
            out["category"] = self.category
        if self.data:
            out["data"] = self.data
        return out


class Scope:
    """Container for tags, extras, user, breadcrumbs and trace context."""

    def __init__(self, max_breadcrumbs: int = DEFAULT_MAX_BREADCRUMBS) -> None:
        self.max_breadcrumbs = max_breadcrumbs
        self.tags: dict[str, str] = {}
        self.extra: dict[str, Any] = {}
        self.user: User | None = None
        self.session_id: str | None = None
        self.trace_id: str | None = None
        self.span_id: str | None = None
        self.breadcrumbs: deque[Breadcrumb] = deque(maxlen=max_breadcrumbs)

    # ---------------------------------------------------------- mutation

    def set_tag(self, key: str, value: str) -> None:
        self.tags[key] = value

    def remove_tag(self, key: str) -> None:
        self.tags.pop(key, None)

    def set_extra(self, key: str, value: Any) -> None:
        self.extra[key] = value

    def set_user(self, user: User | dict[str, str] | None) -> None:
        if isinstance(user, dict):
            user = User(**user)
        self.user = user

    def set_session_id(self, session_id: str | None) -> None:
        self.session_id = session_id

    def set_trace_context(self, trace_id: str | None, span_id: str | None = None) -> None:
        self.trace_id = trace_id
        self.span_id = span_id

    def add_breadcrumb(self, crumb: Breadcrumb | dict[str, Any]) -> None:
        if isinstance(crumb, dict):
            crumb = Breadcrumb(**crumb)
        self.breadcrumbs.append(crumb)

    def clear_breadcrumbs(self) -> None:
        self.breadcrumbs.clear()

    # ------------------------------------------------------------- clone

    def clone(self) -> Scope:
        clone = Scope(max_breadcrumbs=self.max_breadcrumbs)
        clone.tags = dict(self.tags)
        clone.extra = dict(self.extra)
        clone.user = self.user
        clone.session_id = self.session_id
        clone.trace_id = self.trace_id
        clone.span_id = self.span_id
        clone.breadcrumbs = deque(self.breadcrumbs, maxlen=self.max_breadcrumbs)
        return clone

    # ------------------------------------------------------------- apply

    def apply_to_entry(self, entry: LogEntry) -> None:
        """Merge scope state into an entry. Entry-level values win."""
        if self.extra:
            entry.metadata = {**self.extra, **entry.metadata}
        if self.tags and "tags" not in entry.metadata:
            entry.metadata["tags"] = dict(self.tags)
        if self.user is not None and "user" not in entry.metadata:
            user = self.user.to_dict()
            if user:
                entry.metadata["user"] = user
        if self.breadcrumbs and "breadcrumbs" not in entry.metadata:
            entry.metadata["breadcrumbs"] = [c.to_dict() for c in self.breadcrumbs]
        if entry.session_id is None and self.session_id is not None:
            entry.session_id = self.session_id
        if entry.trace_id is None and self.trace_id is not None:
            entry.trace_id = self.trace_id
        if entry.span_id is None and self.span_id is not None:
            entry.span_id = self.span_id


# --------------------------------------------------------- current scope

_current_scope: ContextVar[Scope | None] = ContextVar("logtide_scope", default=None)


def get_current_scope() -> Scope:
    """Return the current scope, creating the root scope on first use."""
    scope = _current_scope.get()
    if scope is None:
        scope = Scope()
        _current_scope.set(scope)
    return scope


@contextmanager
def push_scope() -> Generator[Scope]:
    """Activate a clone of the current scope for the duration of the block.

    Mutations inside the block (tags, breadcrumbs, user, ...) do not leak
    to the outer scope. Use one per request/job for isolation.
    """
    scope = get_current_scope().clone()
    token = _current_scope.set(scope)
    try:
        yield scope
    finally:
        _current_scope.reset(token)


# ----------------------------------------------------- module-level sugar


def set_tag(key: str, value: str) -> None:
    get_current_scope().set_tag(key, value)


def set_extra(key: str, value: Any) -> None:
    get_current_scope().set_extra(key, value)


def set_user(user: User | dict[str, str] | None) -> None:
    get_current_scope().set_user(user)


def set_session_id(session_id: str | None) -> None:
    get_current_scope().set_session_id(session_id)


def add_breadcrumb(crumb: Breadcrumb | dict[str, Any]) -> None:
    get_current_scope().add_breadcrumb(crumb)
