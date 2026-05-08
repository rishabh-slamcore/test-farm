"""Shared domain models."""

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Bundle:
    """Expected bundle metadata shared across controller and invocation code."""

    bundle_id: str
    byte_count: int
    checksum: str

    @classmethod
    def from_bytes(cls, bundle_id: str, bundle_bytes: bytes) -> "Bundle":
        """Build bundle metadata from raw bundle bytes.

        :param bundle_id: Stable bundle identifier.
        :param bundle_bytes: Raw bundle content.
        :returns: Bundle metadata derived from the bytes.
        """

        return cls(
            bundle_id=bundle_id,
            byte_count=len(bundle_bytes),
            checksum=hashlib.sha256(bundle_bytes).hexdigest(),
        )

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
DEFAULT_BUNDLE = Bundle.from_bytes(
    bundle_id="baseline",
    bundle_bytes=DEFAULT_BUNDLE_BYTES,
)
