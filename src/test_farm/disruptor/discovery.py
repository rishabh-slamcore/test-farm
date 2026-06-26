import logging
import socket
import sys
import time

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

from test_farm.models import DEVICE_VARIANTS, DiscoveredDevice

logger = logging.getLogger(__name__)

_HAWKBITC_SERVICE_TYPE = "_hawkbitc._tcp.local."
_AWARE_DISCOVERY_WINDOW_SECONDS = 12.0


def discover_aware_devices() -> tuple[DiscoveredDevice, ...]:
    """Discover Slamcore Aware devices visible to the Disruptor.

    :returns: Discovered devices found during one bounded mDNS browse.
    """

    listener = AwareDeviceListener()
    zeroconf = Zeroconf()
    browser = ServiceBrowser(zeroconf, _HAWKBITC_SERVICE_TYPE, listener)
    try:
        time.sleep(_AWARE_DISCOVERY_WINDOW_SECONDS)
        return listener.devices()
    finally:
        browser.cancel()
        zeroconf.close()


class AwareDeviceListener(ServiceListener):
    """Collect Slamcore Aware hawkBit client services reported by Zeroconf."""

    def __init__(self) -> None:
        self._devices: dict[str, DiscoveredDevice] = {}

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._record_service(zc, type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._record_service(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        device_id = _service_device_id(name)
        if device_id:
            self._devices.pop(device_id, None)

    def devices(self) -> tuple[DiscoveredDevice, ...]:
        return tuple(sorted(self._devices.values(), key=lambda device: device.device_id))

    def _record_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        device = discovered_device_from_service(name=name, info=info)
        if device is None:
            return

        self._devices[device.device_id] = device


def discovered_device_from_service(
    *,
    name: str,
    info: ServiceInfo | None,
) -> DiscoveredDevice | None:
    logger.debug(f"Received {name} : {info}")
    if info is None:
        return None

    txt = _decode_txt_properties(info.properties)
    if txt.get("vendor") != "slamcore" or txt.get("product") != "aware":
        return None

    variant = txt.get("variant")
    if variant not in DEVICE_VARIANTS:
        return None

    addresses = _decode_addresses(info.addresses)
    if not addresses:
        return None

    device_id = _service_device_id(name)
    if not device_id:
        return None

    return DiscoveredDevice(device_id=device_id, ip_address=addresses[0], variant=variant)


def _service_device_id(name: str) -> str:
    return name.split(".", maxsplit=1)[0]


def _decode_txt_properties(properties: dict[bytes, bytes | None]) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in properties.items():
        if value is None:
            continue
        decoded[key.decode(errors="ignore")] = value.decode(errors="ignore")
    return decoded


def _decode_addresses(addresses: list[bytes]) -> list[str]:
    decoded: list[str] = []
    for address in addresses:
        try:
            family = socket.AF_INET6 if len(address) == 16 else socket.AF_INET
            decoded.append(socket.inet_ntop(family, address))
        except ValueError:
            continue
    return decoded


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(levelname).1s%(asctime)s %(process)d %(filename)s:%(lineno)d] %(message)s",
        datefmt="%m%d %H:%M:%S",
        force=True,
    )


def main() -> None:
    configure_logging(verbose=True)
    devices = discover_aware_devices()
    for device in devices:
        logger.info(
            f"[name = {device.device_id} | address = {device.ip_address} | variant = {device.variant}]"
        )


if __name__ == "__main__":
    main()
