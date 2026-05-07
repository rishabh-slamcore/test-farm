"""Shared domain models."""

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Bundle:
    """Expected bundle metadata shared across controller and invocation code."""

    bundle_id: str
    byte_count: int
    checksum: str

    def to_payload(self) -> dict[str, str | int]:
        """Serialize the bundle for JSON result payloads.

        :returns: JSON-serializable bundle payload.
        """

        return {
            "bundle_id": self.bundle_id,
            "byte_count": self.byte_count,
            "checksum": self.checksum,
        }


DEFAULT_BUNDLE_BYTES = b"baseline bundle placeholder\n"
DEFAULT_BUNDLE = Bundle(
    bundle_id="baseline",
    byte_count=len(DEFAULT_BUNDLE_BYTES),
    checksum=hashlib.sha256(DEFAULT_BUNDLE_BYTES).hexdigest(),
)
