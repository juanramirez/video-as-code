from __future__ import annotations

from .i18n import msg


class KdgenError(Exception):
    """Base error for kdenlive-generator."""


class ValidationErrors(KdgenError):
    """Collects every problem found while validating a project instead of
    aborting on the first one: the user fixes them all in one pass."""

    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__(self.render())

    def render(self) -> str:
        bullet_lines = "\n".join(f"  - {m}" for m in self.messages)
        return f"{msg('validation.summary', n=len(self.messages))}\n{bullet_lines}"

    def __str__(self) -> str:
        return self.render()
