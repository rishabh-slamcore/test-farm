"""Shared domain models."""

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict


class ClientStatus(StrEnum):
    """Terminal client outcomes shared across toy-client and invocation code."""

    TIMED_OUT = "timed_out"
    STARTUP_FAILED = "startup_failed"
    SUCCESS = "success"
    DOWNLOAD_FAILED = "download_failed"
    CHECKSUM_MISMATCH = "checksum_mismatch"


class BundlePayload(TypedDict):
    bundle_id: str
    byte_count: int
    checksum: str


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

    def to_payload(self) -> BundlePayload:
        """Serialize the bundle for JSON result payloads.

        :returns: JSON-serializable bundle payload.
        """

        return {
            "bundle_id": self.bundle_id,
            "byte_count": self.byte_count,
            "checksum": self.checksum,
        }


class ClientOutcomePayload(TypedDict):
    client_id: str
    client_status: ClientStatus
    bundle_id: str
    error_detail: NotRequired[str | None]
    reported_bundle: NotRequired[BundlePayload]


@dataclass(frozen=True)
class ClientOutcome:
    """One client outcome recorded in a result file."""

    client_id: str
    client_status: ClientStatus
    bundle_id: str
    error_detail: str | None
    reported_bundle: Bundle | None = None

    def to_payload(self) -> ClientOutcomePayload:
        payload: ClientOutcomePayload = {
            "client_id": self.client_id,
            "client_status": self.client_status,
            "bundle_id": self.bundle_id,
            "error_detail": self.error_detail,
        }
        if self.error_detail is not None:
            payload["error_detail"] = self.error_detail
        if self.reported_bundle is not None:
            payload["reported_bundle"] = self.reported_bundle.to_payload()
        return payload


@dataclass(frozen=True)
class Receipt:
    """Normalized client observation posted to the controller receipt route."""

    client_status: Literal["success", "download_failed"]
    reported_bundle: Bundle | None
    error_detail: str | None

    def to_payload(self) -> dict[str, object]:
        """Serialize the receipt for JSON transport."""

        payload: dict[str, object] = {"client_status": self.client_status}
        if self.reported_bundle is not None:
            payload["reported_bundle"] = self.reported_bundle.to_payload()
        if self.error_detail is not None:
            payload["error_detail"] = self.error_detail
        return payload


DEFAULT_BUNDLE_BYTES = b"baseline bundle placeholder\n"
DEFAULT_BUNDLE = Bundle.from_bytes(
    bundle_id="baseline",
    bundle_bytes=DEFAULT_BUNDLE_BYTES,
)

DEVICE_VARIANTS: tuple[str, ...] = ("mk2", "mk3a", "mk3b", "mk3c")


@dataclass(frozen=True)
class DiscoveredDevice:
    """A real Slamcore Aware device discovered by the Disruptor."""

    device_id: str
    ip_address: str
    variant: str
