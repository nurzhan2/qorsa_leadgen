"""Shared test plumbing: a fake web on httpx.MockTransport, a sleep that
doesn't sleep, and the fixture loader. No real network anywhere."""

from pathlib import Path

import httpx

from workers.enrich.domains import static_block_reason
from workers.enrich.fetcher import PoliteFetcher

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UA = "qorsa-leadgen-enrich/1.0 (test)"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# Route values are factories (request -> Response), so a route can be hit
# more than once - an httpx.Response body can only be read once.

def html(body: str, status: int = 200, charset: str = "utf-8"):
    encoded = body.encode(charset)

    def respond(request):
        return httpx.Response(status, headers={"content-type": f"text/html; charset={charset}"}, content=encoded)
    return respond


def text(body: str, status: int = 200):
    def respond(request):
        return httpx.Response(status, headers={"content-type": "text/plain"}, content=body.encode("utf-8"))
    return respond


def raw(status: int, content_type: str, content: bytes):
    def respond(request):
        return httpx.Response(status, headers={"content-type": content_type}, content=content)
    return respond


def redirect(location: str, status: int = 301):
    def respond(request):
        return httpx.Response(status, headers={"location": location})
    return respond


def fail(exc_type=httpx.ConnectError, message: str = "connection refused"):
    def respond(request):
        raise exc_type(message, request=request)
    return respond


class FakeWeb:
    """Routes by exact URL string. Anything unrouted is a 404, like the real web."""

    def __init__(self, routes: dict):
        self.routes = dict(routes)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(str(request.url))
        if route is None:
            return httpx.Response(404, headers={"content-type": "text/html"}, content=b"<h1>404</h1>")
        return route(request)

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


class NoSleep:
    """Records requested pauses instead of waiting."""

    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(round(seconds, 3))


async def allow_every_host(host: str) -> str | None:
    return None


async def static_guard(host: str) -> str | None:
    """The real name-based guard, without the DNS lookup."""
    return static_block_reason(host)


def make_fetcher(web: FakeWeb, **overrides):
    client = httpx.AsyncClient(transport=httpx.MockTransport(web.handler))
    options = dict(user_agent=UA, request_delay_seconds=0.0, host_guard=allow_every_host,
                   sleep=NoSleep(), clock=lambda: 0.0)
    options.update(overrides)
    return client, PoliteFetcher(client, **options)
