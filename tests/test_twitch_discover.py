from datetime import UTC, datetime, timedelta

import httpx
import pytest

from clipfactory import db
from clipfactory.config import AppConfig, DiscoveryConfig, StreamerConfig
from clipfactory.discover import discover
from clipfactory.twitch import HelixClient, TwitchError

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _clip(clip_id: str, views: int, game: str = "32399", hours_ago: int = 3) -> dict:
    return {
        "id": clip_id,
        "url": f"https://clips.twitch.tv/{clip_id}",
        "broadcaster_id": "1001",
        "broadcaster_name": "StreamerA",
        "creator_name": "fan",
        "video_id": "v1",
        "game_id": game,
        "language": "ru",
        "title": f"clip {clip_id}",
        "view_count": views,
        "created_at": (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "thumbnail_url": "",
        "duration": 30.0,
        "vod_offset": 1000 + views,
    }


class FakeTwitch:
    """httpx.MockTransport handler emulating the few Helix endpoints we use."""

    def __init__(self) -> None:
        self.token_calls = 0
        self.fail_next: list[int] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "id.twitch.tv":
            self.token_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"tok{self.token_calls}",
                    "expires_in": 3600,
                    "token_type": "bearer",
                },
            )
        assert request.headers["Client-Id"] == "cid"
        if self.fail_next:
            code = self.fail_next.pop(0)
            return httpx.Response(
                code, json={"message": "nope"}, headers={"Ratelimit-Reset": "1002"}
            )
        path = request.url.path
        if path == "/helix/users":
            logins = request.url.params.get_list("login")
            data = [
                {"id": "1001", "login": lg, "display_name": lg.title()}
                for lg in logins
                if lg == "streamer_a"
            ]
            return httpx.Response(200, json={"data": data})
        if path == "/helix/games":
            return httpx.Response(200, json={"data": [{"id": "32399", "name": "Counter-Strike"}]})
        if path == "/helix/clips":
            if request.url.params.get("after") == "page2":
                return httpx.Response(
                    200,
                    json={"data": [_clip("C3", 10), _clip("C4", 80, "509658")], "pagination": {}},
                )
            return httpx.Response(
                200,
                json={
                    "data": [_clip("C1", 900), _clip("C2", 300, hours_ago=20)],
                    "pagination": {"cursor": "page2"},
                },
            )
        return httpx.Response(404)


def _client(fake: FakeTwitch, sleeps: list[float] | None = None) -> HelixClient:
    return HelixClient(
        "cid",
        "secret",
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
        clock=lambda: 1000.0,
    )


def test_missing_credentials():
    with pytest.raises(TwitchError, match="TWITCH_CLIENT_ID"):
        HelixClient("", "")


def test_iter_clips_follows_pagination():
    fake = FakeTwitch()
    clips = list(_client(fake).iter_clips("1001", NOW, NOW))
    assert [c.id for c in clips] == ["C1", "C2", "C3", "C4"]
    assert clips[0].view_count == 900 and clips[0].vod_offset == 1900
    assert fake.token_calls == 1


def test_token_refreshed_once_on_401():
    fake = FakeTwitch()
    fake.fail_next = [401]
    users = _client(fake).get_users(["streamer_a"])
    assert users["streamer_a"].id == "1001"
    assert fake.token_calls == 2


def test_rate_limit_waits_until_reset_then_retries():
    fake = FakeTwitch()
    fake.fail_next = [429]
    sleeps: list[float] = []
    assert _client(fake, sleeps).get_game_id("Counter-Strike") == "32399"
    assert sleeps == [2.0]


def test_non_retryable_error_raises():
    fake = FakeTwitch()
    fake.fail_next = [400]
    with pytest.raises(TwitchError, match="HTTP 400"):
        _client(fake).get_game_id("Counter-Strike")


def test_discover_filters_ranks_and_is_idempotent(conn):
    cfg = AppConfig(
        discovery=DiscoveryConfig(min_views=50),
        streamers=[StreamerConfig(login="streamer_a"), StreamerConfig(login="ghost_user")],
    )
    client = _client(FakeTwitch())
    report = discover(conn, client, cfg, now=NOW)
    assert report.new == 2  # C1, C2; C3 below min_views, C4 other category
    assert report.filtered == 2
    assert report.missing_streamers == ["ghost_user"]
    rows = db.list_clips(conn)
    assert [r["clip_id"] for r in rows] == ["C1", "C2"]  # fresher and more viewed first
    assert rows[0]["broadcaster_login"] == "streamer_a"

    again = discover(conn, client, cfg, now=NOW)
    assert again.new == 0 and again.updated == 2
    assert len(db.list_clips(conn)) == 2
