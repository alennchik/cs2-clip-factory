from pathlib import Path

import pytest

from clipfactory import db
from clipfactory.models import RenderStatus
from clipfactory.review import ReviewBot, discover_chat_ids, parse_callback

from .conftest import clip_record

OWNER = 777


class FakeTelegram:
    def __init__(self, updates=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.updates = updates or []
        self._next_id = 100

    def _msg(self) -> dict:
        self._next_id += 1
        return {"message_id": self._next_id}

    def get_updates(self, offset, timeout=50):
        self.calls.append(("get_updates", {"offset": offset}))
        return [u for u in self.updates if u["update_id"] >= offset]

    def send_message(self, chat_id, text, reply_markup=None):
        self.calls.append(
            ("send_message", {"chat_id": chat_id, "text": text, "markup": reply_markup})
        )
        return self._msg()

    def send_video(self, chat_id, path, caption="", reply_markup=None, **kw):
        self.calls.append(
            (
                "send_video",
                {
                    "chat_id": chat_id,
                    "path": Path(path),
                    "caption": caption,
                    "markup": reply_markup,
                },
            )
        )
        return self._msg()

    def send_document(self, chat_id, path, caption="", reply_markup=None):
        self.calls.append(
            ("send_document", {"path": Path(path), "caption": caption, "markup": reply_markup})
        )
        return self._msg()

    def answer_callback(self, callback_id, text=""):
        self.calls.append(("answer", {"text": text}))

    def clear_buttons(self, chat_id, message_id):
        self.calls.append(("clear", {"message_id": message_id}))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def bot(conn, settings, tmp_path):
    settings.telegram_chat_id = OWNER
    db.upsert_clip(conn, clip_record())
    video = tmp_path / "render.mp4"
    video.write_bytes(b"fake mp4")
    rid = db.create_render(
        conn,
        "Clip1",
        hook_text="A: один",
        hook_options=["A: один", "A: два"],
        layout="split",
        start=0,
        duration=12,
        path=str(video),
    )
    tg = FakeTelegram()
    rerenders: list[tuple[int, str]] = []

    def fake_rerender(render_id: int, hook: str) -> int:
        rerenders.append((render_id, hook))
        db.update_render(conn, render_id, status=RenderStatus.SUPERSEDED)
        return db.create_render(
            conn,
            "Clip1",
            hook_text=hook,
            hook_options=["A: один", "A: два"],
            layout="split",
            start=0,
            duration=12,
            path=str(video),
        )

    def fake_export(render_id: int) -> Path:
        out = tmp_path / f"ready_{render_id}.mp4"
        out.write_bytes(b"final")
        out.with_suffix(".txt").write_text("A: один\n\nКлип: twitch.tv/streamer_a\n", "utf-8")
        return out

    instance = ReviewBot(conn, settings, tg, rerender=fake_rerender, export=fake_export)
    instance.rid = rid  # type: ignore[attr-defined]
    instance.rerenders = rerenders  # type: ignore[attr-defined]
    return instance


def press(data: str, user: int = OWNER) -> dict:
    return {"update_id": 1, "callback_query": {
        "id": "cb1", "from": {"id": user}, "data": data,
        "message": {"message_id": 55, "chat": {"id": OWNER}}}}  # fmt: skip


def say(text: str, user: int = OWNER) -> dict:
    return {"update_id": 2, "message": {"chat": {"id": user}, "from": {"id": user}, "text": text}}


def test_webcam_warning_is_visible_before_caption_truncation(bot, conn):
    db.log_event(
        conn,
        "webcam_detection",
        render_id=bot.rid,
        detail='{"reason":"estimated_crop", "review_required":true}',
    )
    bot.send_pending()
    assert bot.tg.calls[0][1]["caption"].startswith("⚠️ Автовебка:")


def test_parse_callback():
    assert parse_callback("a:12") == ("a", 12, None)
    assert parse_callback("h:12:1") == ("h", 12, 1)
    for bad in ("x:1", "a", "h:3", "a:b"):
        with pytest.raises(ValueError):
            parse_callback(bad)


def test_send_pending_sends_drafts_with_buttons(bot, conn):
    assert bot.send_pending() == 1
    name, payload = bot.tg.calls[0]
    assert name == "send_video" and payload["chat_id"] == OWNER
    buttons = [b["callback_data"] for row in payload["markup"]["inline_keyboard"] for b in row]
    assert buttons == [f"a:{bot.rid}", f"r:{bot.rid}", f"h:{bot.rid}:1", f"e:{bot.rid}"]
    assert "A: два" in payload["caption"] and "clips.twitch.tv/Clip1" in payload["caption"]
    assert db.get_render(conn, bot.rid)["status"] == RenderStatus.SENT
    assert bot.send_pending() == 0  # nothing is sent twice


def test_strangers_cannot_press_buttons(bot, conn):
    bot.handle_update(press(f"a:{bot.rid}", user=123))
    assert db.get_render(conn, bot.rid)["status"] == RenderStatus.DRAFT
    assert bot.tg.calls == [("answer", {"text": "Нет доступа"})]
    bot.handle_update(say("привет", user=123))
    assert len(bot.tg.calls) == 1  # messages from strangers are ignored silently


def test_approve_exports_and_sends_final_file(bot, conn):
    bot.handle_update(press(f"a:{bot.rid}"))
    render = db.get_render(conn, bot.rid)
    assert render["status"] == RenderStatus.APPROVED and render["export_path"]
    doc = next(p for n, p in bot.tg.calls if n == "send_document")
    assert doc["path"].name == f"ready_{bot.rid}.mp4"
    assert "twitch.tv/streamer_a" in doc["caption"]
    assert "clear" in bot.tg.names()

    bot.handle_update(press(f"p:{bot.rid}"))
    assert db.get_render(conn, bot.rid)["status"] == RenderStatus.POSTED
    bot.handle_update(press(f"a:{bot.rid}"))
    assert bot.tg.calls[-1] == ("answer", {"text": "Уже одобрено"})


def test_reject(bot, conn):
    bot.handle_update(press(f"r:{bot.rid}"))
    assert db.get_render(conn, bot.rid)["status"] == RenderStatus.REJECTED
    bot.handle_update(press(f"h:{bot.rid}:1"))  # stale buttons do nothing
    assert bot.rerenders == []


def test_other_hook_option_rerenders_and_resends(bot, conn):
    bot.handle_update(press(f"h:{bot.rid}:1"))
    assert bot.rerenders == [(bot.rid, "A: два")]
    assert bot.tg.names()[-1] == "send_video"
    assert "Хук: A: два" in bot.tg.calls[-1][1]["caption"]


def test_own_hook_flow(bot, conn):
    bot.handle_update(press(f"e:{bot.rid}"))
    assert "Пришли новый хук" in bot.tg.calls[-1][1]["text"]
    bot.handle_update(say("  Electronic:   я бы   замашнил "))
    assert bot.rerenders == [(bot.rid, "Electronic: я бы замашнил")]
    assert bot.tg.names()[-1] == "send_video"


def test_plain_message_without_edit_gets_help_and_commands_work(bot):
    bot.handle_update(say("что это"))
    assert "Сюда приходят ролики" in bot.tg.calls[-1][1]["text"]
    bot.handle_update(say("/status"))
    assert "Ролики" in bot.tg.calls[-1][1]["text"]


def test_run_loop_persists_offset(bot, conn):
    bot.tg.updates = [press(f"r:{bot.rid}") | {"update_id": 41}]
    bot.run(max_cycles=1, poll_timeout=0)
    assert db.kv_get(conn, "telegram_offset") == "42"
    assert db.get_render(conn, bot.rid)["status"] == RenderStatus.REJECTED


def test_discover_chat_ids():
    tg = FakeTelegram(
        updates=[{"update_id": 5, "message": {"chat": {"id": 42, "username": "alen"}}}]
    )
    assert discover_chat_ids(tg) == [(42, "alen")]


def test_start_command_shows_persistent_create_button(bot):
    bot.handle_update(say("/start"))
    name, data = bot.tg.calls[-1]
    assert name == "send_message"
    assert "Создать ролик" in str(data["markup"])
    assert data["markup"]["is_persistent"] is True


@pytest.mark.parametrize("message", ["/new", "🎬 Создать ролик"])
def test_manual_create_button_starts_one_clip(bot, message):
    calls = []
    bot.start_pipeline = lambda: calls.append("launch") or 4567
    bot.handle_update(say(message))
    assert calls == ["launch"]
    assert "Запускаю создание одного ролика" in bot.tg.calls[-1][1]["text"]


def test_manual_create_button_rejects_busy_run(bot):
    from clipfactory.run_control import PipelineBusy

    def busy():
        raise PipelineBusy("already active")

    bot.start_pipeline = busy
    bot.handle_update(say("🎬 Создать ролик"))
    assert "уже запущена" in bot.tg.calls[-1][1]["text"]


def test_manual_create_rejects_stranger(bot):
    calls = []
    bot.start_pipeline = lambda: calls.append("launch") or 1
    bot.handle_update(say("/new", user=123))
    bot.handle_update(press("a:1", user=123))
    assert calls == []
    assert not any(n == "send_message" for n, _ in bot.tg.calls)


def test_manual_create_reports_startup_error_without_secret(bot):
    def fail():
        raise RuntimeError("sensitive local details")

    bot.start_pipeline = fail
    bot.handle_update(say("/new"))
    assert "Не удалось запустить" in bot.tg.calls[-1][1]["text"]
    assert "sensitive local details" not in bot.tg.calls[-1][1]["text"]


def test_queue_and_status_keyboard_buttons(bot):
    bot.handle_update(say("📋 Очередь"))
    assert bot.tg.names()[-1] == "send_video"
    bot.handle_update(say("📊 Статус"))
    assert "Ролики" in bot.tg.calls[-1][1]["text"]
