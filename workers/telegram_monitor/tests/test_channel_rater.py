"""Unit tests for channel_rater.py.

ChannelRating's order_rate/verdict are pure and tested directly. rate_channel
needs a Telethon-like client, so tests use a small in-memory fake exposing
just the two calls channel_rater.py relies on (get_entity, iter_messages) -
no real network, no real Telegram session.
"""

import pytest

from workers.telegram_monitor.channel_rater import ChannelRater, ChannelRating
from workers.telegram_monitor.config import ChannelEntry, KeywordCategory, KeywordsConfig, Settings
from workers.telegram_monitor.matcher import Matcher

# --- ChannelRating: order_rate / verdict, pure ---------------------------


def test_order_rate_is_zero_with_no_posts():
    rating = ChannelRating(identifier="x")

    assert rating.order_rate == 0.0


def test_order_rate_calculation():
    rating = ChannelRating(identifier="x", subscribed=True, total_posts=20, orders=5)

    assert rating.order_rate == 0.25


@pytest.mark.parametrize(
    ("orders", "total", "expected_verdict"),
    [
        (3, 20, "ДЕРЖАТЬ"),  # 15% - exactly at the "keep" threshold
        (10, 20, "ДЕРЖАТЬ"),  # 50% - well above
        (2, 20, "СЛАБО"),  # 10% - in the weak band
        (1, 20, "СЛАБО"),  # 5% - exactly at the "weak" threshold
        (0, 20, "ВЫКИНУТЬ"),  # 0% - below weak threshold
    ],
)
def test_verdict_thresholds(orders, total, expected_verdict):
    rating = ChannelRating(identifier="x", subscribed=True, total_posts=total, orders=orders)

    assert rating.verdict == expected_verdict


def test_verdict_unreachable_when_not_subscribed():
    rating = ChannelRating(identifier="x", subscribed=False, total_posts=0, orders=0)

    assert rating.verdict == "НЕДОСТУПЕН"


def test_verdict_unreachable_when_subscribed_but_zero_posts_read():
    # e.g. a channel that resolved fine but has no history at all - should
    # not be reported as "ВЫКИНУТЬ" (that implies we actually evaluated
    # content and found it weak).
    rating = ChannelRating(identifier="x", subscribed=True, total_posts=0, orders=0)

    assert rating.verdict == "НЕДОСТУПЕН"


# --- ChannelRater.rate_channel: counts orders/hiring/noise from mocked ---
# --- post history, via a fake Telethon-like client -----------------------


class _FakeMessage:
    def __init__(self, raw_text: str):
        self.raw_text = raw_text


class _FakeClient:
    """Exposes only what channel_rater.py actually calls on a Telethon
    client: get_entity() (awaited) and iter_messages() (an async iterator,
    NOT awaited itself - matches real Telethon usage)."""

    def __init__(self, posts_by_target: dict, unreachable: set | None = None):
        self._posts_by_target = posts_by_target
        self._unreachable = unreachable or set()

    async def get_entity(self, target):
        if target in self._unreachable:
            raise ValueError(f"cannot resolve: {target}")
        return target  # the "entity" is just the target itself - good enough for these tests

    def iter_messages(self, entity, limit):
        posts = self._posts_by_target.get(entity, [])

        async def _gen():
            for text in posts[:limit]:
                yield _FakeMessage(text)

        return _gen()


@pytest.fixture
def keywords() -> KeywordsConfig:
    return KeywordsConfig(
        intent=KeywordCategory(weight=3, keywords=["нужен сайт", "ищу разработчика"]),
        domain=KeywordCategory(weight=2, keywords=["сайт", "лендинг", "бот"]),
        budget=KeywordCategory(weight=1, keywords=["бюджет"]),
        anti_hiring=["в штат", "оклад", "график работы"],
        order_signals=["разовая задача", "бюджет"],
    )


@pytest.fixture
def matcher(keywords: KeywordsConfig) -> Matcher:
    return Matcher(keywords)


@pytest.fixture
def settings() -> Settings:
    return Settings(tg_api_id=1, tg_api_hash="test-hash", rate_sample=50, rate_channel_delay_seconds=0)


@pytest.mark.asyncio
async def test_rate_channel_counts_orders_hiring_and_noise(settings: Settings, matcher: Matcher):
    posts = [
        "Нужен сайт под ключ, бюджет 50000",  # order (intent)
        "Есть бюджет, разовая задача на лендинг",  # order (domain + order_signals)
        "Ищу разработчика в штат, оклад 150000, график работы 5/2",  # hiring (rejected)
        "Всем привет, как дела?",  # noise
        "Красивый закат сегодня, всем добра",  # noise
    ]
    client = _FakeClient(posts_by_target={"good_channel": posts})
    rater = ChannelRater(settings, matcher, client)
    entry = ChannelEntry(username="good_channel")

    rating = await rater.rate_channel(entry)

    assert rating.subscribed is True
    assert rating.total_posts == 5
    assert rating.orders == 2
    assert rating.hiring == 1
    assert rating.noise == 2
    assert rating.order_rate == pytest.approx(2 / 5)
    assert rating.verdict == "ДЕРЖАТЬ"  # 40% order rate


@pytest.mark.asyncio
async def test_rate_channel_marks_unreachable_channel_without_crashing(settings: Settings, matcher: Matcher):
    client = _FakeClient(posts_by_target={}, unreachable={"private_channel"})
    rater = ChannelRater(settings, matcher, client)
    entry = ChannelEntry(username="private_channel")

    rating = await rater.rate_channel(entry)

    assert rating.subscribed is False
    assert rating.total_posts == 0
    assert rating.verdict == "НЕДОСТУПЕН"


@pytest.mark.asyncio
async def test_rate_all_rates_every_configured_channel(settings: Settings, matcher: Matcher):
    client = _FakeClient(
        posts_by_target={
            "orders_channel": ["Нужен сайт под ключ, бюджет 50000"] * 3,
            "empty_channel": [],
        },
        unreachable={"gone_channel"},
    )
    rater = ChannelRater(settings, matcher, client)
    entries = [
        ChannelEntry(username="orders_channel"),
        ChannelEntry(username="empty_channel"),
        ChannelEntry(username="gone_channel"),
    ]

    ratings = await rater.rate_all(entries)

    assert [r.identifier for r in ratings] == ["orders_channel", "empty_channel", "gone_channel"]
    assert ratings[0].verdict == "ДЕРЖАТЬ"
    assert ratings[1].verdict == "НЕДОСТУПЕН"  # subscribed but zero posts
    assert ratings[2].verdict == "НЕДОСТУПЕН"  # not subscribed
