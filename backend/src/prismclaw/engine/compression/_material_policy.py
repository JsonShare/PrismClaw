from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal

from prismclaw.engine.foundation.tool_output import resolve_max_output_chars
from prismclaw.engine.compression._helpers import _result_text
from prismclaw.engine.toolkit.registry.tool_classification import is_network_read_tool

ResultMaterialType = Literal[
    "command_output",
    "retrieval_list",
    "long_web_page",
    "document_extraction",
    "structured_dataset_preview",
    "generic_text",
]


@dataclass(frozen=True)
class ResultCompactionPolicy:
    material_type: ResultMaterialType
    per_result_chars: int
    aggregate_priority: int
    aggressive_chars: int


# 源代码 / 标记类 / 配置类扩展名。read 工具读到这些文件时，结果归
# generic_text：该 material type 在 _semantic_compress.py 没有 reducer，
# 走无损 head/tail 截断而不是"取以 ':' 结尾的行"的文档启发式摘要——
# 后者对源码严重退化（Python 源码行极少以 ':' 结尾且多数 <40 字符），
# 是 read 同参重读死循环故障的根因。
_SOURCE_CODE_SUFFIXES = frozenset({
    # 源代码
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".go", ".rs", ".java", ".kt", ".kts", ".c", ".h", ".cpp", ".cc",
    ".hpp", ".cs", ".rb", ".php", ".swift", ".m", ".mm", ".scala",
    ".clj", ".ex", ".exs", ".erl", ".hs", ".lua", ".pl", ".pm", ".r",
    ".dart", ".zig", ".vue", ".svelte", ".astro",
    # 脚本 / 构建
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".sql", ".gradle", ".cmake",
    ".mk", ".makefile", ".proto", ".thrift", ".tf", ".tfvars",
    # 标记 / 样式
    ".html", ".htm", ".css", ".scss", ".less",
    # 数据 / 配置
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".properties", ".xml", ".env",
})

# 结构化 Office 格式：read 读到这些文件时输出带编辑锚点的文本（非散文），
# 语义摘要器会破坏锚点，必须走 generic_text 的无损截断。
_STRUCTURED_OFFICE_SUFFIXES = frozenset({".xlsx", ".pptx"})

# 已知"文档"扩展名：read 读到这些扩展名时归 document_extraction
# （散文提取摘要）。不在此集合的未知扩展名（.txt/.log/…）也走
# 内容嗅探兜底，避免把源码误摘。
_DOCUMENT_SUFFIXES = frozenset({
    ".md", ".markdown", ".rst", ".docx", ".pdf", ".doc", ".rtf", ".epub",
    ".txt",
})

# 无扩展名 / 未知扩展名文件的内容兜底：前 200 字符内命中源码特征才
# 归 generic_text。保守集合，避免把散文 Markdown 误判为源码（误判的
# 后果也仅是"不做文档摘要"，无损截断仍然保底）。
_SOURCE_CODE_SNIFF_PATTERNS = (
    "def ", "class ", "import ", "from ", "function ", "const ", "let ",
    "var ", "func ", "fn ", "package ", "pub ", "use ", "struct ",
    "impl ", "type ", "#!/usr/bin/env", "#!/bin/", "<?php",
)
_SNIFF_WINDOW = 200


def _extract_file_path(call: dict[str, Any] | None) -> str | None:
    """从原始 tool call 中提取 file_path 参数（容错 JSON 字符串参数）。"""
    if not call:
        return None
    args = ((call.get("function") or {}).get("arguments")) or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(args, dict):
        return None
    path = args.get("file_path")
    return path if isinstance(path, str) else None


def _looks_like_source_code(text: str, file_path: str | None) -> bool:
    """read 类工具结果是否为源码/结构化文本。

    扩展名优先：源码扩展名 / 结构化 Office（.xlsx/.pptx，锚点文本，
    摘要器会破坏）→ True；已知文档扩展名 → False；
    无扩展名或未知扩展名 → 前 200 字符内容嗅探。
    """
    if file_path:
        suffix = os.path.splitext(file_path)[1].lower()
        if suffix in _SOURCE_CODE_SUFFIXES:
            return True
        if suffix in _STRUCTURED_OFFICE_SUFFIXES:
            return True
        if suffix in _DOCUMENT_SUFFIXES:
            return False
        # 无扩展名（Makefile / Dockerfile / LICENSE）或未知扩展名
        # （.log / 笔记型代码 .txt 之外的杂项）→ 内容嗅探
    head = text[:_SNIFF_WINDOW]
    return any(pattern in head for pattern in _SOURCE_CODE_SNIFF_PATTERNS)


def _detect_material_type(
    tool_name: str,
    result: Any,
    call: dict[str, Any] | None = None,
) -> ResultMaterialType:
    text = _result_text(result).lower()
    if tool_name == "web_fetch":
        return "long_web_page"
    if tool_name == "read":
        # read 可读任意文本/文档文件。源码与结构化文本（含 .xlsx/.pptx
        # 锚点文本，经 _looks_like_source_code 扩展名判定）归 generic_text
        # （无摘要器，无损截断）；其余（散文、报告）归 document_extraction。
        if _looks_like_source_code(text, _extract_file_path(call)):
            return "generic_text"
        return "document_extraction"
    if tool_name in {"search", "grep", "glob"} or is_network_read_tool(tool_name):
        return "retrieval_list"
    if _looks_like_table(text) or _looks_like_structured_json(text):
        return "structured_dataset_preview"
    if tool_name == "bash":
        return "command_output"
    return "generic_text"


def _detect_context_block_material_type(block: Any) -> ResultMaterialType:
    text = block.content.lower()
    if block.source == "memory_archives":
        return "retrieval_list"
    if block.source in {"memory_facts", "memory_procedures"}:
        if _looks_like_table(text) or _looks_like_structured_json(text):
            return "structured_dataset_preview"
        return "retrieval_list"
    if (block.metadata or {}).get("source_kind") == "attachment":
        return "document_extraction"
    if _looks_like_table(text) or _looks_like_structured_json(text):
        return "structured_dataset_preview"
    return "generic_text"


def _looks_like_table(text: str) -> bool:
    pipe_rows = sum(1 for line in text.splitlines() if line.count("|") >= 3)
    csv_rows = sum(1 for line in text.splitlines() if line.count(",") >= 4)
    return pipe_rows >= 3 or csv_rows >= 5


def _looks_like_structured_json(text: str) -> bool:
    sample = text.strip()
    if len(sample) < 2 or sample[0] not in "[{":
        return False
    try:
        parsed = json.loads(sample)
    except Exception:
        return False
    return isinstance(parsed, (list, dict))


def _policy_for(
    call: dict[str, Any],
    result: Any,
    deps: Any,
) -> ResultCompactionPolicy:
    from prismclaw.engine.compression._result_budget import _resolve_default_max_chars
    tool_name = ((call or {}).get("function") or {}).get("name") or ""
    # deps.tools 可能未注入（mock/桩）；显式 None 检查比嵌套 getattr 清晰。
    tools = getattr(deps, "tools", None)
    tool_obj = tools.get(tool_name) if tools is not None else None
    default_cap = _resolve_default_max_chars(deps)
    per_result_cap = (
        resolve_max_output_chars(tool_obj, default_cap) if tool_obj is not None else default_cap
    )
    material_type = _detect_material_type(tool_name, result, call)

    profile_caps: dict[ResultMaterialType, tuple[int, int, int]] = {
        "command_output": (per_result_cap, 3, max(1200, min(per_result_cap // 3, 6000))),
        "retrieval_list": (min(per_result_cap, 10_000), 4, 2200),
        "long_web_page": (min(per_result_cap, 12_000), 1, 1800),
        "document_extraction": (min(per_result_cap, 11_000), 2, 2000),
        "structured_dataset_preview": (min(per_result_cap, 8_000), 0, 1600),
        "generic_text": (min(per_result_cap, 10_000), 5, 2200),
    }
    chosen_cap, priority, aggressive_chars = profile_caps[material_type]
    return ResultCompactionPolicy(
        material_type=material_type,
        per_result_chars=max(500, chosen_cap),
        aggregate_priority=priority,
        aggressive_chars=max(600, aggressive_chars),
    )


def _context_block_policy_for(
    *,
    block: Any,
    aggregate_budget_chars: int,
    block_count: int,
) -> ResultCompactionPolicy:
    material_type = _detect_context_block_material_type(block)
    # 无聚合预算时 base_cap=0：各 material 档位的 max(floor, base_cap) 下限
    # 已保证输出仍有保底尺寸，无需额外魔数兜底。
    base_cap = 0
    if aggregate_budget_chars > 0:
        base_cap = max(800, aggregate_budget_chars // max(1, block_count))

    profile_caps: dict[ResultMaterialType, tuple[int, int, int]] = {
        "command_output": (max(1000, base_cap), 5, max(700, base_cap // 2)),
        "retrieval_list": (max(1200, base_cap * 2), 2, max(700, base_cap)),
        "long_web_page": (max(1400, base_cap * 2), 1, max(800, base_cap)),
        "document_extraction": (max(1400, base_cap * 2), 1, max(800, base_cap)),
        "structured_dataset_preview": (max(1000, base_cap), 0, max(700, base_cap // 2)),
        "generic_text": (max(1200, base_cap * 2), 4, max(700, base_cap)),
    }
    chosen_cap, priority, aggressive_chars = profile_caps[material_type]
    return ResultCompactionPolicy(
        material_type=material_type,
        per_result_chars=chosen_cap,
        aggregate_priority=priority,
        aggressive_chars=aggressive_chars,
    )