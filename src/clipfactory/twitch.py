"""Minimal Twitch Helix client (app access token): users, games, clips.

Only metadata is read here. Media download is a separate, permission-gated step (fetch.py).
Docs: https://dev.twitch.tv/docs/api/reference/#get-clips
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

HELIX_URL = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class TwitchError(RuntimeError):
    pass


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc3339(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass(frozen=True)
class TwitchUser:
    id: str
    login: str
    display_name: str


@dataclass(frozen=True)
class TwitchClip:
    id: str
    url: str
    broadcaster_id: str
    broadcaster_name: str
    creator_name: str
    video_id: str
    game_id: str
    language: str
    title: str
    view_count: int
    created_at: datetime
    thumbnail_url: str
    duration: float
    vod_offset: int | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> TwitchClip:
        vod_offset = item.get("vod_offset")
        return cls(
            id=str(item["id"]),
            url=str(item.get("url") or f"https://clips.twitch.tv/{item['id']}"),
            broadcaster_id=str(item.get("broadcaster_id", "")),
            broadcaster_name=str(item.get("broadcaster_name", "")),
            creator_name=str(item.get("creator_name", "")),
            video_id=str(item.get("video_id") or ""),
            game_id=str(item.get("game_id") or ""),
            language=str(item.get("language") or ""),
            title=str(item.get("title") or ""),
            view_count=int(item.get("view_count") or 0),
            created_at=parse_rfc3339(str(item["created_at"])),
            thumbnail_url=str(item.get("thumbnail_url") or ""),
            duration=float(item.get("duration") or 0.0),
            vod_offset=int(vod_offset) if vod_offset is not None else None,
        )


def _chunks(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class HelixClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        max_retries: int = 3,
    ) -> None:
        if not client_id or not client_secret:
            raise TwitchError("TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET are not set (see .env)")
        self.client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.Client(timeout=httpx.Timeout(20.0))
        self._sleep = sleep
        self._clock = clock
        self._max_retries = max_retries
        self._token: str | None = None
        self._token_expiry = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> HelixClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- auth --------------------------------------------------------------------------

    def _fetch_token(self) -> str:
        resp = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
        )
        if resp.status_code != 200:
            raise TwitchError(f"token request failed: HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        self._token = str(body["access_token"])
        self._token_expiry = self._clock() + float(body.get("expires_in", 3600)) - 60
        return self._token

    def _headers(self) -> dict[str, str]:
        if self._token is None or self._clock() >= self._token_expiry:
            self._fetch_token()
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {self._token}"}

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        reset = resp.headers.get("Ratelimit-Reset")
        if reset:
            try:
                return min(max(float(reset) - self._clock(), 1.0), 60.0)
            except ValueError:
                pass
        return float(min(2**attempt, 30))

    def get(self, path: str, params: Sequence[tuple[str, str]]) -> dict[str, Any]:
        refreshed = False
        for attempt in range(self._max_retries + 1):
            resp = self._http.get(
                f"{HELIX_URL}/{path}", params=list(params), headers=self._headers()
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401 and not refreshed:
                self._token = None
                refreshed = True
                continue
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self._max_retries:
                self._sleep(self._retry_delay(resp, attempt))
                continue
            raise TwitchError(f"GET /{path} failed: HTTP {resp.status_code}: {resp.text[:200]}")
        raise TwitchError(f"GET /{path}: retries exhausted")

    # --- endpoints ---------------------------------------------------------------------

    def get_users(self, logins: Sequence[str]) -> dict[str, TwitchUser]:
        users: dict[str, TwitchUser] = {}
        for chunk in _chunks([lg.lower() for lg in logins], 100):
            data = self.get("users", [("login", lg) for lg in chunk])
            for item in data.get("data", []):
                login = str(item["login"]).lower()
                users[login] = TwitchUser(
                    id=str(item["id"]),
                    login=login,
                    display_name=str(item.get("display_name") or login),
                )
        return users

    def get_game_id(self, name: str) -> str | None:
        data = self.get("games", [("name", name)])
        items = data.get("data") or []
        return str(items[0]["id"]) if items else None

    def iter_clips(
        self,
        broadcaster_id: str,
        started_at: datetime,
        ended_at: datetime,
        *,
        page_size: int = 100,
        max_pages: int = 3,
    ) -> Iterator[TwitchClip]:
        cursor: str | None = None
        for _ in range(max_pages):
            params = [
                ("broadcaster_id", broadcaster_id),
                ("started_at", rfc3339(started_at)),
                ("ended_at", rfc3339(ended_at)),
                ("first", str(page_size)),
            ]
            if cursor:
                params.append(("after", cursor))
            data = self.get("clips", params)
            for item in data.get("data", []):
                yield TwitchClip.from_api(item)
            cursor = (data.get("pagination") or {}).get("cursor")
            if not cursor:
                break
