"""Deterministic identifier generation.

No `uuid.uuid4()` or timestamps: if the input YAML doesn't change, the
generated XML output must be byte-for-byte identical. That's what keeps Git
diffs of the generated .kdenlive (if it ever gets versioned) readable, and
it's essential for testing generation reproducibly.
"""
from __future__ import annotations

import hashlib
import uuid

# Fixed, arbitrary namespace (generated once with uuid4) used to derive
# deterministic UUIDs via uuid5. Has no special meaning beyond that.
_NAMESPACE = uuid.UUID("6f8f2a3e-3b8a-4c0a-9d3a-5b6b6e2f2b40")


def deterministic_uuid(*parts: str) -> str:
    """Deterministic UUID (kdenlive format: wrapped in braces) from `parts`."""
    seed = "::".join(parts)
    return "{%s}" % uuid.uuid5(_NAMESPACE, seed)


def deterministic_document_id(project_id: str) -> str:
    """kdenlive normally uses epoch-millis; here we derive a stable number
    with the same shape (13 digits) from the project id."""
    digest = hashlib.sha1(project_id.encode("utf-8")).hexdigest()
    number = int(digest[:16], 16) % 10**13
    return str(number).zfill(13)
