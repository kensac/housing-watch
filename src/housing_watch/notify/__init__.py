"""The email: un-notified events into one digest (digest), and a changed event in words (changes)."""

from . import changes, digest
from .changes import describe, is_noise
from .digest import build, send, urgent

__all__ = ["build", "changes", "describe", "digest", "is_noise", "send", "urgent"]
