"""Selected-user persistence across the top nav.

The operator picks a user once (by opening their detail page or any editor);
we remember that choice in a cookie so the top-nav menus jump straight to that
user's editors instead of bouncing back to the picker each time.

The cookie is set transparently by the middleware in ``web.app`` for any
``/users/{id}...`` request, so routes never have to write it themselves —
they only read it via :func:`selected_user_id` / :func:`remembered_redirect`.
"""

from __future__ import annotations

from typing import Iterable

from fastapi import Request
from fastapi.responses import RedirectResponse

COOKIE_NAME = "lb_user"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def selected_user_id(request: Request) -> int | None:
    """The remembered user id, or None when nothing valid is stored."""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def remembered_redirect(request: Request, suffix: str, users: Iterable) -> RedirectResponse | None:
    """A redirect to the remembered user's editor, or None to fall back.

    ``suffix`` is the per-editor path tail (e.g. ``"/edit/items"`` or
    ``"/upgrades"``). Returns None when no user is remembered or the stored id
    is no longer a real user, so callers keep their existing single-user /
    picker behaviour.
    """
    sel = selected_user_id(request)
    if sel is None:
        return None
    if any(u.user_id == sel for u in users):
        return RedirectResponse(url=f"/users/{sel}{suffix}", status_code=303)
    return None
