"""Bounded, asynchronous DIAL transport for the XGIMI Elfin Flip WK03C.

This module deliberately has no Home Assistant dependency.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError

import aiohttp
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from yarl import URL

MAX_RESPONSE_BYTES = 128 * 1024
_DNS_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


class ProjectorError(Exception):
    """Base error raised by the device transport."""


class ProjectorConnectionError(ProjectorError):
    """The configured host could not be reached within the request deadline."""


class ProjectorProtocolError(ProjectorError):
    """A reachable endpoint returned an invalid or unexpected protocol response."""


class ProjectorCommandError(ProjectorError):
    """The projector explicitly rejected a request."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Identity verified against the device description endpoint."""

    host: str
    manufacturer: str
    model: str
    name: str
    udn: str
    application_url: str


def normalize_host(host: str) -> str:
    """Accept a bare IPv4/IPv6 address or DNS name, never a URL or port."""
    if not isinstance(host, str) or not host or host != host.strip():
        raise ValueError("Enter a bare IP address or hostname")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if any(char in host for char in "/\\:@?#%[]") or len(host) > 253:
        raise ValueError("Enter a bare IP address or hostname")
    host = host.rstrip(".")
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as err:
        raise ValueError("Invalid hostname") from err
    if not ascii_host or not all(_DNS_NAME.fullmatch(part) for part in ascii_host.split(".")):
        raise ValueError("Invalid hostname")
    return ascii_host


def _xml(body: bytes) -> Any:
    try:
        return ElementTree.fromstring(
            body, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except (ParseError, DefusedXmlException, ValueError) as err:
        raise ProjectorProtocolError("The projector returned invalid or unsafe XML") from err


def _text(parent: Any, name: str) -> str:
    return (parent.findtext(f"{{*}}{name}") or "").strip()


class DialClient:
    """Use one externally owned aiohttp session for bounded DIAL requests."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        description_port: int = 56790,
        control_port: int = 56789,
        timeout: float = 3.0,
    ) -> None:
        self._session = session
        self.host = normalize_host(host)
        if not all(
            isinstance(port, int) and 1 <= port <= 65535
            for port in (description_port, control_port)
        ):
            raise ValueError("Ports must be between 1 and 65535")
        if not 0 < timeout <= 30:
            raise ValueError("Request timeout must be greater than zero and at most 30 seconds")
        self.description_port = description_port
        self.control_port = control_port
        self.timeout = timeout
        self._description_url = str(
            URL.build(scheme="http", host=self.host, port=description_port, path="/dd.xml")
        )
        self._application_url = str(
            URL.build(scheme="http", host=self.host, port=control_port, path="/apps/")
        )

    def _same_host_url(self, url: str) -> str:
        """Refuse redirects, credentials, fragments, and cross-host resources."""
        try:
            parsed = urlsplit(url)
            host = normalize_host(parsed.hostname or "")
            port = parsed.port
        except (ValueError, UnicodeError) as err:
            raise ProjectorProtocolError("The projector advertised an invalid service URL") from err
        if (
            parsed.scheme != "http"
            or host != self.host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.query
            or (port is not None and not 1 <= port <= 65535)
            or any(char in url for char in ("\\", "\r", "\n", "\t"))
        ):
            raise ProjectorProtocolError("The projector advertised an unexpected service URL")
        return str(URL(url))

    async def _request(
        self,
        method: str,
        url: str,
        *,
        empty_body: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        headers: dict[str, str] = {}
        data = None
        if empty_body:
            headers["Content-Length"] = "0"
            data = b""
        try:
            async with self._session.request(
                method,
                url,
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                allow_redirects=False,
            ) as response:
                if 300 <= response.status < 400:
                    raise ProjectorProtocolError(
                        "The projector returned an unexpected HTTP redirect"
                    )
                body = bytearray()
                async for chunk in response.content.iter_chunked(16 * 1024):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ProjectorProtocolError(
                            "The projector response exceeds the size limit"
                        )
                # Normalize header names; aiohttp's mapping itself is case-insensitive.
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                return response.status, response_headers, bytes(body)
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise ProjectorConnectionError(
                "The projector did not respond to the DIAL request"
            ) from err

    @staticmethod
    def _expect_status(status: int, expected: set[int], operation: str) -> None:
        if status not in expected:
            raise ProjectorCommandError(
                f"The projector rejected {operation} (HTTP {status})", status
            )

    async def async_get_device_info(self) -> DeviceInfo:
        """Verify the WK03C identity before accepting its advertised API base."""
        status, headers, body = await self._request("GET", self._description_url)
        self._expect_status(status, {200}, "device identification")
        root = _xml(body)
        device = root.find(".//{*}device")
        if device is None:
            raise ProjectorProtocolError("The DIAL description does not contain a device")
        manufacturer = _text(device, "manufacturer")
        model = _text(device, "modelName")
        udn = _text(device, "UDN")
        if manufacturer.casefold() != "xgimi" or model.upper() != "WK03C":
            raise ProjectorProtocolError("This integration supports the XGIMI Elfin Flip WK03C")
        if not udn:
            raise ProjectorProtocolError("The projector description does not contain a stable UDN")
        application_url = self._same_host_url(headers.get("application-url", self._application_url))
        if not urlsplit(application_url).path.endswith("/"):
            application_url += "/"
        self._application_url = application_url
        return DeviceInfo(
            host=self.host,
            manufacturer=manufacturer,
            model=model,
            name=_text(device, "friendlyName") or "Elfin Flip",
            udn=udn,
            application_url=application_url,
        )

    async def async_sleep(self) -> None:
        """Request system sleep; HTTP acceptance does not itself prove standby."""
        status, _headers, _body = await self._request(
            "POST",
            self._application_url + "system?action=sleep",
            empty_body=True,
        )
        self._expect_status(status, {200}, "system sleep")
