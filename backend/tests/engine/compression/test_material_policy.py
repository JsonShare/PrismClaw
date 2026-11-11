"""P0 压缩层修复回归测试。

背景（真实故障因果链，全部已用代码证据坐实）：
1. read 被 _material_policy 归为 document_extraction；
2. document_extraction 的语义摘要器只保留"以 ':' 结尾的行 + 长度>40 的行"，
   对源码严重退化 → 每次 read 返回同一份 ~554 字符残片；
3. read 的 tool_class="read" 禁止持久化，_materialize_single_result 将
   超限结果截断到 ~4140 字符（> per_result_chars=4000）→ 语义摘要必然触发；
4. 残片逐字节相同 → 调用签名相同 → 被判 doom-loop 硬终止。

修复：read 按扩展名与内容分流（M1 合并 read_office 后含文档格式）——
- 源码/结构化文本（.py/.json/…、无扩展名嗅探命中、.xlsx/.pptx 结构锚点）
  → generic_text（无 reducer，无损截断）；
- 散文文档（.md/.docx/.pdf/…）→ document_extraction（文档摘要）。
"""
from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.compression._material_policy import (
    _detect_material_type,
    _policy_for,
)
from prismclaw.engine.compression._semantic_compress import _semantic_compact_text
from prismclaw.engine.compression.result_compaction import _compact_single_result
from prismclaw.engine.execution.output.output_compressor import compress_tool_result
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.content_types import ToolResult


def _result(text: str) -> ToolResult:
    return ToolResult(
        tool_call_id="c1",
        content=[TextBlock(text=text)],
        is_error=False,
    )


def _call(tool_name: str, arguments: dict | str) -> dict:
    return {"id": "c1", "function": {"name": tool_name, "arguments": arguments}}


def _deps(per_result_char_cap: int = 4000) -> SimpleNamespace:
    class _Ports:
        compress_tool_result = staticmethod(compress_tool_result)

        @staticmethod
        def is_persisted_content(text: str) -> bool:
            return False

        @staticmethod
        def persist_tool_result_sync(**_kwargs):
            return None

    tools = SimpleNamespace(get=lambda name: None)
    config = SimpleNamespace(
        context=SimpleNamespace(
            compaction=SimpleNamespace(
                tool_results=SimpleNamespace(per_result_char_cap=per_result_char_cap)
            ),
        ),
    )
    return SimpleNamespace(tools=tools, config=config, ports=_Ports())


_PY_SOURCE = "\n".join(
    f"def handler_{i}(x):" if i % 20 == 0 else f"    value_{i} = x * {i}"
    for i in range(400)
)
_PROSE = "\n".join(
    f"第 {i} 章：这是一段足够长的散文内容，用于模拟文档抽取结果的普通行。"
    for i in range(120)
)
# read 对 .xlsx 的输出形态：短锚点行（不以 ':' 结尾且 <40 字符）。
_XLSX_ANCHORS = "\n".join(
    f"s#{si}!r{si * 3}c1: v{si * 7}" if si % 10 == 0 else f"s#{si}!r{si * 3}c2 v{si * 11}"
    for si in range(1200)
)


# ── material type 分流：read ─────────────────────────────────────

def test_read_source_code_by_extension_is_generic_text():
    mt = _detect_material_type(
        "read", _result(_PY_SOURCE), _call("read", {"file_path": "gen_complaint.py"})
    )
    assert mt == "generic_text"


def test_read_json_by_extension_is_generic_text():
    mt = _detect_material_type(
        "read", _result('{"key": "value"}'), _call("read", {"file_path": "conf.json"})
    )
    assert mt == "generic_text"


def test_read_prose_md_is_document_extraction():
    mt = _detect_material_type(
        "read", _result(_PROSE), _call("read", {"file_path": "report.md"})
    )
    assert mt == "document_extraction"


def test_read_extensionless_source_sniffed_as_generic_text():
    mt = _detect_material_type(
        "read",
        _result("#!/bin/bash\nset -euo pipefail\n" + "echo step\n" * 100),
        _call("read", {"file_path": "Dockerfile"}),
    )
    assert mt == "generic_text"


def test_read_extensionless_prose_sniffed_as_document_extraction():
    mt = _detect_material_type(
        "read", _result(_PROSE), _call("read", {"file_path": "NOTICE"})
    )
    assert mt == "document_extraction"


def test_read_unknown_extension_source_sniffed_as_generic_text():
    # 未知扩展名（.log 等）也走内容嗅探兜底，避免把源码误摘。
    mt = _detect_material_type(
        "read",
        _result("import os\n" + "print('x')\n" * 200),
        _call("read", {"file_path": "debug.log"}),
    )
    assert mt == "generic_text"


def test_read_unknown_extension_prose_is_document_extraction():
    mt = _detect_material_type(
        "read", _result(_PROSE), _call("read", {"file_path": "notes.dat"})
    )
    assert mt == "document_extraction"


def test_read_txt_prose_is_document_extraction():
    mt = _detect_material_type(
        "read", _result(_PROSE), _call("read", {"file_path": "notes.txt"})
    )
    assert mt == "document_extraction"


def test_read_with_json_string_arguments():
    # 主链路 call 的 arguments 是 JSON 字符串（tool_call_types.to_openai_dict）。
    mt = _detect_material_type(
        "read", _result(_PY_SOURCE), _call("read", '{"file_path": "gen_complaint.py"}')
    )
    assert mt == "generic_text"


def test_read_without_call_prose_is_document_extraction():
    # call 缺失 → file_path=None → 直接内容嗅探；散文无源码特征 →
    # document_extraction（保守文档归类）。
    mt = _detect_material_type("read", _result(_PROSE), None)
    assert mt == "document_extraction"


def test_read_without_call_source_sniffed_as_generic_text():
    # call 缺失但内容是源码 → 嗅探兜底仍归 generic_text。
    mt = _detect_material_type("read", _result(_PY_SOURCE), None)
    assert mt == "generic_text"


# ── material type 分流：read 的 office 文档（B1 迁移验证）─────────

def test_read_docx_is_document_extraction():
    mt = _detect_material_type(
        "read", _result(_PROSE), _call("read", {"file_path": "合同.docx"})
    )
    assert mt == "document_extraction"


def test_read_pdf_is_document_extraction():
    mt = _detect_material_type(
        "read", _result(_PROSE), _call("read", {"file_path": "报告.pdf"})
    )
    assert mt == "document_extraction"


def test_read_xlsx_is_generic_text():
    # B1 核心断言：.xlsx 输出结构锚点文本，文档摘要器会把 47k 字符摘成
    # ~283 字符确定性残片（与 read .py 故障同构），必须归 generic_text。
    # read_office 分支删除后该判定经 _looks_like_source_code 的
    # _STRUCTURED_OFFICE_SUFFIXES 扩展名优先序承接。
    mt = _detect_material_type(
        "read", _result(_XLSX_ANCHORS), _call("read", {"file_path": "big.xlsx"})
    )
    assert mt == "generic_text"


def test_read_pptx_is_generic_text():
    mt = _detect_material_type(
        "read", _result(_XLSX_ANCHORS), _call("read", {"file_path": "deck.pptx"})
    )
    assert mt == "generic_text"


def test_legacy_dead_tool_names_fall_through():
    # pdf/docx/pptx/xlsx 工具名在代码库中不存在（registry 注册清单
    # 无此四名）；确认它们不再被 document_extraction 分支吞掉，而是
    # 走通用内容检测路径（散文 → structured/generic，而非文档摘要）。
    for name in ("pdf", "docx", "pptx", "xlsx"):
        mt = _detect_material_type(name, _result(_PROSE))
        assert mt in ("generic_text", "structured_dataset_preview")


# ── policy 装配 ──────────────────────────────────────────────────

def test_policy_for_read_py_is_generic_text():
    policy = _policy_for(_call("read", {"file_path": "x.py"}), _result(_PY_SOURCE), _deps())
    assert policy.material_type == "generic_text"


def test_policy_for_read_docx_is_document_extraction():
    policy = _policy_for(
        _call("read", {"file_path": "x.docx"}), _result(_PROSE), _deps()
    )
    assert policy.material_type == "document_extraction"


def test_policy_for_read_xlsx_is_generic_text():
    policy = _policy_for(
        _call("read", {"file_path": "big.xlsx"}), _result(_XLSX_ANCHORS), _deps()
    )
    assert policy.material_type == "generic_text"


# ── reducer 直测 ─────────────────────────────────────────────────

def test_semantic_compact_skips_generic_text():
    # generic_text 无 reducer：原样返回，无 strategies。
    text, strategies = _semantic_compact_text(
        _PY_SOURCE, material_type="generic_text", max_chars=4000
    )
    assert text == _PY_SOURCE
    assert strategies == []


def test_semantic_compact_degrades_source_via_document_extraction():
    # 复现旧 bug：同一段源码若走 document_extraction，语义摘要会把它
    # 摘成残片（这就是死循环故障的残片来源）。
    text, strategies = _semantic_compact_text(
        _PY_SOURCE, material_type="document_extraction", max_chars=4000
    )
    assert "[文档抽取摘要]" in text
    assert len(text) < len(_PY_SOURCE)
    assert strategies == ["semantic_reduce:document_extraction"]


# ── 端到端：_compact_single_result 整链（policy → 截断 → reducer 分派）──

def _end_to_end(tool_name: str, file_path: str, text: str) -> tuple[str, list[str]]:
    artifact = _compact_single_result(
        call=_call(tool_name, {"file_path": file_path}),
        result=_result(text),
        deps=_deps(),
        compression_enabled=True,
        working_dir=None,
    )
    return _text(artifact.result), artifact.strategies


def _text(result: ToolResult) -> str:
    return "".join(b.text for b in result.content if isinstance(b, TextBlock))


def test_end_to_end_large_py_file_has_no_summary_residue():
    # 原故障场景：>4000 字符的 .py 文件经 read 读取。
    # 期望：generic_text → 无 semantic_reduce 策略、无
    # [文档抽取摘要] 残片、文件头部保留（无损截断）。
    out_text, strategies = _end_to_end("read", "gen_complaint.py", _PY_SOURCE)
    assert "[文档抽取摘要]" not in out_text
    assert "semantic_reduce:document_extraction" not in strategies
    assert "def handler_0(x):" in out_text  # 头部保留
    assert len(out_text) < len(_PY_SOURCE)  # 确实被截断（4000 cap）


def test_end_to_end_large_py_file_prose_residue_still_summarized():
    # 对照组：同尺寸散文走 document_extraction，摘要仍生效（不因
    # 本次修复而关闭正常文档压缩）。
    out_text, strategies = _end_to_end("read", "报告.docx", _PROSE * 3)
    assert "semantic_reduce:document_extraction" in strategies


def test_end_to_end_xlsx_anchors_have_no_summary_residue():
    # 复核 A3 场景：大 .xlsx 锚点文本经 read 读取（B1 迁移后）。
    out_text, strategies = _end_to_end("read", "big.xlsx", _XLSX_ANCHORS)
    assert "[文档抽取摘要]" not in out_text
    assert "semantic_reduce:document_extraction" not in strategies
    assert "s#0!r0c1: v0" in out_text  # 头部锚点保留


def test_policy_cap_for_read_py_allows_full_page():
    # generic_text 的 cap = min(per_result_cap, 10000) = 4000（默认配置）。
    # read 分页默认 2000 行；4KB 以下的常规源码文件完整内联。
    policy = _policy_for(
        _call("read", {"file_path": "small.py"}),
        _result(_PY_SOURCE[:2000]),
        _deps(),
    )
    assert policy.per_result_chars >= 2000