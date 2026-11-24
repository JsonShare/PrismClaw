"""文本化工具调用（DSML）兜底的回归护栏。

真实故障：vLLM 端点未启用 tool-call parser 时，DeepSeek 把原生工具调用协议
以纯文本写进 content。旧逻辑只看「有可见文字」，于是把这轮判成 ok → 当作完成
申报，工具从未执行，用户只看到一堆标记。

⚠️ 本文件的核心用例内嵌**线上真实样本**（2026-11-18 从 Redis 会话树导出），
不得替换为自造样本。历史教训：早期实现只认 ``<|DSML|tool_calls>``（ASCII 竖线
+ tool_calls），而真实形态是全角竖线 U+FF5C + ``calls``，导致 9/9 样本一条都
没匹配上，兜底代码从未生效却"测试通过"——自造样本自证等于没测。
"""

from __future__ import annotations

import random
from types import SimpleNamespace

from prismclaw.engine.execution.dispatch.response_handling import (
    decide_no_tool_turn,
    retry_limit_for,
)
from prismclaw.engine.execution.dispatch.turn_utils import (
    classify_turn,
    strip_noise,
    strip_thinking,
)
from prismclaw.schema.shared.tool_markup import (
    MarkupStreamFilter,
    has_tool_markup,
    strip_tool_markup,
)

# ── 线上真实样本（原样粘贴，不做任何归一化） ──────────────────────────────
# 特征：竖线是全角 U+FF5C；块名是 calls；参数体里含整段 python 脚本。
REAL_DSML = (
    "<\uff5cDSML\uff5ccalls>\n"
    '<\uff5cDSML\uff5cinvoke name="bash">\n'
    '<\uff5cDSML\uff5cparameter name="command" string="true">'
    "cd /tmp && python3 - <<'PY'\n"
    "import urllib.request,re\n"
    "def ab(pid):\n"
    '    u="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id="+pid\n'
    "    return urllib.request.urlopen(u,timeout=60).read()\n"
    "PY</\uff5cDSML\uff5cparameter>\n"
    '<\uff5cDSML\uff5cparameter name="description" string="true">取队列规模</\uff5cDSML\uff5c'
    "parameter>\n"
    "</\uff5cDSML\uff5cinvoke>\n"
    "</\uff5cDSML\uff5ccalls>"
)

# ASCII 竖线 + tool_calls 变体（部分端点/历史版本使用）
ASCII_DSML = (
    "<|DSML|tool_calls>\n"
    '<|DSML|invoke name="bash">\n'
    '<|DSML|parameter name="command">ls -la</|DSML|parameter>\n'
    "</|DSML|invoke>\n"
    "</|DSML|tool_calls>"
)


def test_real_sample_is_actually_detected():
    """守住根因：线上样本必须被识别（旧实现此断言失败）。"""
    assert "\uff5c" in REAL_DSML, "样本必须保留全角竖线"
    assert "DSML|tool_calls" not in REAL_DSML, "样本块名是 calls，不是 tool_calls"
    assert has_tool_markup(REAL_DSML)


def test_real_sample_fully_stripped():
    out = strip_tool_markup(REAL_DSML)
    assert out == ""
    assert "DSML" not in out
    # 脚本正文不能漏出（它在块内部）
    assert "urllib" not in out


def test_ascii_variant_still_supported():
    assert has_tool_markup(ASCII_DSML)
    assert strip_tool_markup(ASCII_DSML) == ""


def test_spaced_markup_variant():
    spaced = (
        "< \uff5c DSML \uff5c calls >< \uff5c DSML \uff5c invoke "
        'name="bash" >x< / \uff5c DSML \uff5c calls >'
    )
    assert has_tool_markup(spaced)
    assert strip_tool_markup(spaced) == ""


def test_prose_around_markup_is_kept():
    assert strip_tool_markup("我来执行一下。\n" + REAL_DSML).strip() == "我来执行一下。"
    assert strip_tool_markup(REAL_DSML + "\n完成。").strip() == "完成。"


def test_clean_text_not_touched():
    """零误伤：正常 Markdown / HTML / 位运算竖线都不能被吃。"""
    for text in (
        "普通回答，只有正文",
        "a < b 的情况",
        "HTML: <b>粗体</b> 与 <br/>",
        "代码 `a | b` 位运算",
        "表格 | 列1 | 列2 |",
        "<div class='x'>内容</div>",
    ):
        assert not has_tool_markup(text), text
        assert strip_tool_markup(text) == text, text


def test_unclosed_block_is_dropped():
    assert strip_tool_markup('<\uff5cDSML\uff5ccalls>\n<\uff5cDSML\uff5cinvoke name="bash">') == ""


def test_strip_noise_keeps_thinking_behaviour():
    assert strip_noise("答案<thinking>内心戏</thinking>") == "答案"
    assert strip_thinking("答案<thinking>内心戏</thinking>") == "答案"
    # thinking + DSML 同时存在
    assert strip_noise("答案<thinking>x</thinking>" + REAL_DSML) == "答案"


def test_classify_marks_markup_only_turn_as_tool_markup():
    assert classify_turn(text=REAL_DSML, tool_calls=None) == "tool_markup"
    assert classify_turn(text=ASCII_DSML, tool_calls=None) == "tool_markup"
    # 有实质正文的回合不因混入标记而被否定
    assert classify_turn(text="已生成页面。\n" + REAL_DSML, tool_calls=None) == "ok"
    # 真正解析出 tool_calls 的回合永远是 ok
    assert classify_turn(text=REAL_DSML, tool_calls=[{"id": "c1"}]) == "ok"


def test_markup_turn_is_retried_then_exhausted():
    response = SimpleNamespace(text=REAL_DSML, tool_calls=[])
    config = SimpleNamespace(retry=SimpleNamespace(reasoning_only_limit=2, empty_response_limit=1))

    first = decide_no_tool_turn(response=response, config=config, retry_counts={"tool_markup": 0})
    assert first.classification == "tool_markup"
    assert first.action == "retry"

    second = decide_no_tool_turn(response=response, config=config, retry_counts={"tool_markup": 1})
    assert second.action == "complete"


def test_retry_limit_defaults_to_empty_limit():
    config = SimpleNamespace(retry=SimpleNamespace(reasoning_only_limit=2, empty_response_limit=1))
    assert retry_limit_for("tool_markup", config, None) == 1


def test_stream_filter_handles_char_by_char():
    text = "说明：\n" + REAL_DSML + "\n结束。"
    f = MarkupStreamFilter()
    out = "".join(f.feed(ch) for ch in text) + f.flush()
    assert out == strip_tool_markup(text)
    assert "DSML" not in out


def test_stream_filter_is_chunk_boundary_agnostic():
    """任意切分点都必须与整段剥离等价——token 切流不保证边界落在标记外。"""
    text = "开头。\n" + REAL_DSML + "\n结尾。"
    expected = strip_tool_markup(text)
    rng = random.Random(20261118)
    for _ in range(200):
        cuts = sorted(rng.sample(range(1, len(text)), k=rng.randint(1, 40)))
        f = MarkupStreamFilter()
        parts: list[str] = []
        prev = 0
        for cut in cuts:
            parts.append(f.feed(text[prev:cut]))
            prev = cut
        parts.append(f.feed(text[prev:]))
        parts.append(f.flush())
        assert "".join(parts) == expected


def test_stream_filter_unclosed_block_flushes_empty():
    f = MarkupStreamFilter()
    assert (
        f.feed('正文。\n<\uff5cDSML\uff5ccalls>\n<\uff5cDSML\uff5cinvoke name="bash">')
        == "正文。\n"
    )
    assert f.flush() == ""
