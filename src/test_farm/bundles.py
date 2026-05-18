"""Bundle source helpers for invocation and update serving."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from test_farm.models import DEFAULT_BUNDLE, DEFAULT_BUNDLE_BYTES, Bundle

DEFAULT_BUNDLE_FILE = (
    Path(__file__).resolve().parents[2] / "runtime" / "bundles" / DEFAULT_BUNDLE.bundle_id
)


class BundleSource(Protocol):
    """Resolve bundle content by Bundle ID."""

    def get_bundle(self, bundle_id: str) -> Bundle | None:
        """Return the resolved bundle for ``bundle_id`` or ``None`` when absent."""

    def get_bundle_bytes(self, bundle_id: str) -> bytes | None:
        """Return actual bundle content for ``bundle_id`` or ``None`` when absent."""


class InMemoryBundleSource:
    """Serve one in-memory bundle for fast tests."""

    def __init__(
        self,
        *,
        bundle_id: str = DEFAULT_BUNDLE.bundle_id,
        bundle_bytes: bytes = DEFAULT_BUNDLE_BYTES,
    ) -> None:
        self._bundle = Bundle.from_bytes(bundle_id=bundle_id, bundle_bytes=bundle_bytes)
        self._bundle_bytes = bundle_bytes

    def get_bundle(self, bundle_id: str) -> Bundle | None:
        if bundle_id != self._bundle.bundle_id:
            return None
        return self._bundle

    def get_bundle_bytes(self, bundle_id: str) -> bytes | None:
        if bundle_id != self._bundle.bundle_id:
            return None
        return self._bundle_bytes


class FileBackedBundleSource:
    """Serve bundles from files within one directory, keyed by filename."""

    def __init__(self, bundle_dir: Path) -> None:
        self._bundle_dir = bundle_dir

    def get_bundle(self, bundle_id: str) -> Bundle | None:
        bundle_path = self._bundle_dir / bundle_id
        if not bundle_path.is_file():
            return None

        bundle_bytes = bundle_path.read_bytes()
        return Bundle.from_bytes(bundle_id=bundle_id, bundle_bytes=bundle_bytes)

    def get_bundle_bytes(self, bundle_id: str) -> bytes | None:
        bundle_path = self._bundle_dir / bundle_id
        if not bundle_path.is_file():
            return None

        return bundle_path.read_bytes()


def load_default_bundle() -> Bundle:
    """Load the default baseline bundle from the host-side Bundle File."""

    bundle_bytes = DEFAULT_BUNDLE_FILE.read_bytes()
    return Bundle.from_bytes(bundle_id=DEFAULT_BUNDLE.bundle_id, bundle_bytes=bundle_bytes)


def create_default_bundle_source() -> BundleSource:
    """Create the default file-backed source for the baseline bundle."""

    return FileBackedBundleSource(DEFAULT_BUNDLE_FILE.parent)
