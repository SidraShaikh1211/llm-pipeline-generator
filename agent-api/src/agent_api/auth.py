from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str | None = None


def get_current_user() -> CurrentUser:
    """Stub auth: returns a hardcoded local-dev user.

    Replace with real SSO/JWT validation when deploying. The dependency
    signature stays the same; only the body changes.
    """
    return CurrentUser(user_id="local-dev", email="local-dev@example.com")
