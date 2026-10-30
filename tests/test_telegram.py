import json

import httpx
import pytest

from clipfactory.telegram import TelegramClient, TelegramError


def _client(handler) -> TelegramClient:
    return TelegramClient("123:ABC", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_missing_token():
    with pytest.raises(TelegramError, match="TELEGRAM_BOT_TOKEN"):
        TelegramClient("")


def test_send_video_uploads_multipart_with_json_markup(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00\x01video")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    markup = {"inline_keyboard": [[{"text": "✅", "callback_data": "a:1"}]]}
    result = _client(handler).send_video(1, video, "подпись", markup, width=1080, height=1920)
    assert result == {"message_id": 9}
    assert seen["url"].endswith("/bot123:ABC/sendVideo")
    body = seen["body"]
    assert b'name="video"; filename="v.mp4"' in body and b"\x00\x01video" in body
    assert json.dumps(markup, ensure_ascii=False).encode() in body
    assert b'name="supports_streaming"\r\n\r\ntrue' in body


def test_api_error_raises_with_description():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    with pytest.raises(TelegramError, match="chat not found"):
        _client(handler).send_message(1, "hi")


def test_non_json_response_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    with pytest.raises(TelegramError, match="HTTP 502"):
        _client(handler).get_updates(0, timeout=0)


def test_transport_failure_never_exposes_bot_token_in_exception_or_traceback():
    import traceback

    def handler(request):
        raise httpx.ReadTimeout(f"request failed at {request.url}", request=request)

    with pytest.raises(TelegramError) as caught:
        _client(handler).send_message(1, "test")
    assert "123:ABC" not in "".join(traceback.format_exception(caught.value))


def test_server_error_description_redacts_echoed_token():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "description": f"bad URL {request.url}"})

    with pytest.raises(TelegramError) as caught:
        _client(handler).send_message(1, "test")
    assert "123:ABC" not in str(caught.value)
