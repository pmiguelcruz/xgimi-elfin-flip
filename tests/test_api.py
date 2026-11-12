"""Exercise the actual HTTP/XML transport, without a Home Assistant import."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

API_PATH = Path(__file__).parents[1] / "custom_components/xgimi_elfin_flip/api.py"
SPEC = importlib.util.spec_from_file_location("xgimi_elfin_flip_test_api", API_PATH)
api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = api
SPEC.loader.exec_module(api)


def description(model="WK03C", manufacturer="XGIMI", udn="uuid:test-unit"):
    return (
        '<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">'
        f"<device><manufacturer>{manufacturer}</manufacturer><modelName>{model}</modelName>"
        f"<friendlyName>My Elfin Flip</friendlyName><UDN>{udn}</UDN></device></root>"
    )


@pytest_asyncio.fixture
async def server_factory():
    resources = []

    async def create(routes, *, timeout=1.0):
        requests = []

        async def handle(request):
            body = await request.read()
            requests.append((request.method, request.path_qs, dict(request.headers), body))
            route = routes.get((request.method, request.path), (404, "not found", {}))
            if callable(route):
                return await route(request)
            status, text, headers = route
            return web.Response(
                status=status,
                body=text.encode() if isinstance(text, str) else text,
                headers=headers,
            )

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        session = aiohttp.ClientSession()
        resources.append((runner, session))
        return api.DialClient(session, "127.0.0.1", port, port, timeout), requests, port

    yield create
    for runner, session in resources:
        await session.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_identity_and_application_url(server_factory):
    client, requests, port = await server_factory({("GET", "/dd.xml"): (200, description(), {})})
    info = await client.async_get_device_info()
    assert info == api.DeviceInfo(
        "127.0.0.1",
        "XGIMI",
        "WK03C",
        "My Elfin Flip",
        "uuid:test-unit",
        f"http://127.0.0.1:{port}/apps/",
    )
    assert requests[0][:2] == ("GET", "/dd.xml")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        description(model="XK03H"),
        description(manufacturer="Another brand"),
        description(udn=""),
        "<bad>",
    ],
)
async def test_identity_rejects_wrong_or_malformed_device(server_factory, body):
    client, _, _ = await server_factory({("GET", "/dd.xml"): (200, body, {})})
    with pytest.raises(api.ProjectorProtocolError):
        await client.async_get_device_info()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://different-device.local:56789/apps/",
        "https://127.0.0.1/apps/",
        "http://name:password@127.0.0.1/apps/",
        "http://127.0.0.1/apps/?secret=1",
        "http://127.0.0.1/apps/#fragment",
    ],
)
async def test_identity_rejects_unexpected_application_url(server_factory, url):
    client, _, _ = await server_factory(
        {("GET", "/dd.xml"): (200, description(), {"Application-URL": url})}
    )
    with pytest.raises(api.ProjectorProtocolError):
        await client.async_get_device_info()


@pytest.mark.asyncio
async def test_identity_accepts_safe_advertised_application_base(server_factory):
    routes = {("GET", "/dd.xml"): (200, description(), {})}
    client, _, port = await server_factory(routes)
    routes[("GET", "/dd.xml")] = (
        200,
        description(),
        {"Application-URL": f"http://127.0.0.1:{port}/apps"},
    )
    assert (await client.async_get_device_info()).application_url.endswith("/apps/")


@pytest.mark.asyncio
async def test_no_redirect_is_followed(server_factory):
    client, requests, _ = await server_factory(
        {("GET", "/dd.xml"): (302, "", {"Location": "http://different-device.local/"})}
    )
    with pytest.raises(api.ProjectorProtocolError, match="redirect"):
        await client.async_get_device_info()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_unsafe_xml_is_rejected(server_factory):
    body = '<!DOCTYPE root [<!ENTITY secret SYSTEM "file:///etc/passwd">]><root>&secret;</root>'
    client, _, _ = await server_factory({("GET", "/dd.xml"): (200, body, {})})
    with pytest.raises(api.ProjectorProtocolError, match="unsafe XML"):
        await client.async_get_device_info()


@pytest.mark.asyncio
async def test_response_limit_applies_to_streamed_data(server_factory):
    async def stream(request):
        response = web.StreamResponse()
        await response.prepare(request)
        try:
            for _ in range(10):
                await response.write(b"x" * 16384)
        except ConnectionResetError, aiohttp.ClientConnectionError:
            pass
        return response

    client, _, _ = await server_factory({("GET", "/dd.xml"): stream})
    with pytest.raises(api.ProjectorProtocolError, match="size limit"):
        await client.async_get_device_info()


@pytest.mark.asyncio
async def test_sleep_request_is_exact_and_empty(server_factory):
    client, requests, _ = await server_factory({("POST", "/apps/system"): (200, "", {})})
    await client.async_sleep()
    method, path, headers, body = requests[0]
    assert (method, path, body) == ("POST", "/apps/system?action=sleep", b"")
    assert headers["Content-Length"] == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 500, 501])
async def test_sleep_rejection_is_not_success(server_factory, status):
    client, _, _ = await server_factory({("POST", "/apps/system"): (status, "rejected", {})})
    with pytest.raises(api.ProjectorCommandError) as caught:
        await client.async_sleep()
    assert caught.value.status == status


@pytest.mark.asyncio
async def test_deadline_is_connection_error(server_factory):
    async def slow(_request):
        await asyncio.sleep(0.1)
        return web.Response(body=description())

    client, _, _ = await server_factory({("GET", "/dd.xml"): slow}, timeout=0.01)
    with pytest.raises(api.ProjectorConnectionError):
        await client.async_get_device_info()


@pytest.mark.asyncio
async def test_connection_failure_is_connection_error():
    async with aiohttp.ClientSession() as session:
        client = api.DialClient(session, "127.0.0.1", 1, 1, 0.05)
        with pytest.raises(api.ProjectorConnectionError):
            await client.async_get_device_info()


@pytest.mark.asyncio
async def test_request_cancellation_is_not_swallowed(server_factory):
    started = asyncio.Event()

    async def slow(_request):
        started.set()
        await asyncio.sleep(0.1)
        return web.Response(body=description())

    client, _, _ = await server_factory({("GET", "/dd.xml"): slow})
    task = asyncio.create_task(client.async_get_device_info())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    "host",
    [
        "http://192.0.2.79",
        "192.0.2.79:56789",
        " name.local",
        "name.local/path",
        "user@host",
        "name..local",
        "-host.local",
        "host\\path",
    ],
)
def test_invalid_hosts_are_rejected(host):
    with pytest.raises(ValueError):
        api.normalize_host(host)


def test_host_normalization():
    assert api.normalize_host("XGIMI.Local.") == "xgimi.local"
    assert api.normalize_host("192.0.2.79") == "192.0.2.79"
    assert api.normalize_host("[::1]") == "::1"
