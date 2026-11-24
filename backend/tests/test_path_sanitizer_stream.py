"""流式路径脱敏的跨分片回归护栏。

真实故障：LLM 按 token 分片吐正文，``/tmp/gz_pages/doctor1.html`` 被切成
``/tmp/g`` + ``z_pages/doctor1.html``。逐片 redact 会把前片替成 ``[内部路径]``，
后片不再以绝对路径开头于是原样漏出——用户实时看到 ``[内部路径]z_pages/doctor1.html``。
"""

from __future__ import annotations

from prismclaw.schema.shared.path_sanitizer import (
    StreamingPathRedactor,
    redact_server_paths,
    split_stream_safe,
)


def test_chunked_abs_path_does_not_leak_suffix():
    r = StreamingPathRedactor()
    parts = ["结果见 /tmp/g", "z_pages/doctor1.html 页面"]
    out = "".join(r.feed(p) for p in parts) + r.flush()
    assert out == "结果见 [内部路径] 页面"
    assert "z_pages" not in out


def test_chunked_home_path_does_not_leak_suffix():
    r = StreamingPathRedactor()
    parts = ["配置在 ~/.prismclaw/sk", "ills/a.md"]
    out = "".join(r.feed(p) for p in parts) + r.flush()
    assert out == "配置在 [内部路径]"
    assert ".prismclaw" not in out


def test_one_char_at_a_time_matches_plain_redact():
    text = "见 /tmp/gz_pages/doctor1.html 与 ~/.prismclaw/skills/a.md 末"
    r = StreamingPathRedactor()
    out = "".join(r.feed(ch) for ch in text) + r.flush()
    assert out == redact_server_paths(text)


def test_flush_emits_held_back_tail():
    r = StreamingPathRedactor()
    assert r.feed("路径是 /tmp/") == "路径是 "
    assert r.flush() == redact_server_paths("/tmp/")
    assert r.flush() == ""


def test_plain_text_is_not_held_back():
    assert split_stream_safe("这是一段中文") == ("这是一段中文", "")
    assert split_stream_safe("hello world") == ("hello world", "")
    assert split_stream_safe("") == ("", "")


def test_holdback_is_bounded():
    """超长路径串不能无限延迟正文：滞留上限外强制推送。"""
    r = StreamingPathRedactor()
    emitted = r.feed("a/" * 400)
    assert emitted, "超过滞留上限必须已经吐出内容"
