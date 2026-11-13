from __future__ import annotations

import atexit
import logging
import os
import queue as _queue
import re
import zipfile
from collections.abc import Iterable, Mapping
from datetime import date
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path

_TRUTHY = {"1", "true", "yes", "y", "on", "debug"}


def _is_truthy(v: str | None) -> bool:
    return v is not None and v.strip().lower() in _TRUTHY


def _parse_log_level(raw: str | int | None, *, default: int) -> int:
    if isinstance(raw, int):
        return raw
    if raw is None:
        return default
    v = raw.strip().upper()
    if not v:
        return default
    if v.isdigit():
        try:
            return int(v)
        except ValueError:
            return default
    if v in ("CRITICAL", "FATAL"):
        return logging.CRITICAL
    if v == "ERROR":
        return logging.ERROR
    if v in ("WARNING", "WARN"):
        return logging.WARNING
    if v == "INFO":
        return logging.INFO
    if v in ("DEBUG", "TRACE"):
        return logging.DEBUG
    if v == "NOTSET":
        return logging.NOTSET
    return default


def _litellm_debug_enabled() -> bool:
    return _is_truthy(os.getenv("PRISMCLAW_LITELLM_DEBUG")) or _is_truthy(os.getenv("LITELLM_LOG"))


def _iter_logger_level_pairs(raw: str) -> Iterable[tuple[str, str]]:
    for chunk in raw.replace(";", ",").split(","):
        item = chunk.strip()
        if not item:
            continue
        if "=" not in item:
            continue
        name, lvl = item.split("=", 1)
        name = name.strip()
        lvl = lvl.strip()
        if name and lvl:
            yield name, lvl


def _resolve_root_level(level: str | int | None) -> int:
    raw = level if level is not None else os.getenv("PRISMCLAW_LOG_LEVEL")
    return _parse_log_level(raw, default=logging.INFO)


def _resolve_logger_level_overrides(
    root_level: int,
    *,
    logger_levels: Mapping[str, str | int] | None,
) -> dict[str, int]:
    out: dict[str, int] = {}
    env_raw = (os.getenv("PRISMCLAW_LOGGERS") or "").strip()
    if env_raw:
        for name, lvl_raw in _iter_logger_level_pairs(env_raw):
            out[name] = _parse_log_level(lvl_raw, default=root_level)
    if logger_levels:
        for name, lvl_raw in logger_levels.items():
            if not name:
                continue
            out[str(name)] = _parse_log_level(lvl_raw, default=root_level)
    return out


def _apply_log_levels(root_level: int, *, logger_levels: Mapping[str, int]) -> None:
    root = logging.getLogger()
    root.setLevel(root_level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(root_level)

    for name, lvl in logger_levels.items():
        if name:
            logging.getLogger(name).setLevel(lvl)


def _build_text_formatter(*, colored: bool | None = None) -> logging.Formatter:
    fmt = (
        "%(asctime)s.%(msecs)03d | "
        "%(levelname)-8s | "
        "%(process)d:%(thread)d | "
        "%(name)s:%(funcName)s:%(lineno)d | "
        "%(message)s"
    )
    base = logging.Formatter(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S")

    # colored=None → 自动检测终端；文件落盘必须显式传 colored=False，
    # 否则 ANSI 色码会写进日志文件。
    if colored is None:
        colored = _is_truthy(os.getenv("PRISMCLAW_LOG_COLOR")) or _terminal_supports_color()
    if not colored:
        return base

    return _ColoredFormatter(base)


def _terminal_supports_color() -> bool:
    """检测 stderr 是否连接到支持 ANSI 颜色的终端。"""
    try:
        import sys
        if not hasattr(sys.stderr, "isatty"):
            return False
        if not sys.stderr.isatty():
            return False
        # 常见无颜色环境
        if os.getenv("NO_COLOR") is not None:
            return False
        term = os.getenv("TERM", "")
        if term == "dumb":
            return False
        return True
    except Exception:
        return False


class _ColoredFormatter(logging.Formatter):
    """在 levelname 上叠加 ANSI 颜色的 Formatter。"""

    _LEVEL_COLORS: dict[int, str] = {
        logging.DEBUG: "\033[36m",       # 青色
        logging.INFO: "\033[32m",        # 绿色
        logging.WARNING: "\033[33m",     # 黄色
        logging.ERROR: "\033[31m",       # 红色
        logging.CRITICAL: "\033[1;31m",  # 加粗红色
    }
    _RESET = "\033[0m"

    def __init__(self, base: logging.Formatter) -> None:
        # 不调用 super().__init__，直接复用 base 的格式
        self._base = base

    def format(self, record: logging.LogRecord) -> str:
        color = self._LEVEL_COLORS.get(record.levelno, "")
        # 保存原始 levelname，临时替换为带颜色的版本
        original = record.levelname
        if color:
            record.levelname = f"{color}{original:<8}{self._RESET}"
        try:
            return self._base.format(record)
        finally:
            record.levelname = original


def _build_json_formatter() -> logging.Formatter:
    from pythonjsonlogger import jsonlogger

    class _PrismClawJsonFormatter(jsonlogger.JsonFormatter):
        def add_fields(self, log_record, record, message_dict) -> None:
            super().add_fields(log_record, record, message_dict)
            log_record["timestamp"] = self.formatTime(record, self.datefmt)
            log_record["level"] = record.levelname
            log_record["logger"] = record.name
            log_record["process"] = record.process
            log_record["thread"] = record.thread
            log_record["funcName"] = record.funcName
            log_record["lineno"] = record.lineno
            log_record.pop("color_message", None)

    return _PrismClawJsonFormatter(
        fmt="%(timestamp)s %(level)s %(logger)s %(process)d %(thread)d %(funcName)s %(lineno)d %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S.%03dZ",
    )


def _resolve_log_format() -> str:
    raw = (os.getenv("PRISMCLAW_LOG_FORMAT") or "text").strip().lower()
    return "json" if raw == "json" else "text"


# 文件落盘的目标 logger：root + uvicorn 自管 handler 的两个 logger。
# uvicorn 的 dictConfig 给 "uvicorn"/"uvicorn.access" 挂了自己的 handler 且
# propagate=False，必须把文件 handler 同时挂上去，否则 uvicorn 日志进不了文件。
# 注意：挂在 "uvicorn" 上的文件 handler 只有在 uvicorn 的 dictConfig 已运行
# （propagate 已被置 False）时才会收到记录；直接 Python 入口下 propagate 默认为
# True，记录会传播到 root 由 root 上的同一批 handler 处理，因此不会双写。
_FILE_TARGET_LOGGERS: tuple[str, ...] = ("", "uvicorn", "uvicorn.error", "uvicorn.access")

# 已安装的真实落盘 handler（运行在 QueueListener 的后台线程上）
_FILE_HANDLERS: list[logging.Handler] = []
# 挂在业务 logger 上的 QueueHandler（事件循环线程只做入队）
_FILE_QUEUE_HANDLERS: list[logging.Handler] = []
# 后台消费线程；None 表示文件落盘未启用
_FILE_LISTENER: QueueListener | None = None
_ATEXIT_REGISTERED = False


def _resolve_log_dir() -> Path | None:
    """解析日志目录：PRISMCLAW_LOG_DIR 显式指定（off=禁用），默认项目根/logs/app。

    在 pytest 下默认不写文件日志——避免测试用例的 WARNING/ERROR 污染生产
    logs/app/error.log。如需在测试中验证落盘行为，显式设置 PRISMCLAW_LOG_DIR。
    """
    import sys

    raw = (os.getenv("PRISMCLAW_LOG_DIR") or "").strip().lower()
    if raw == "off":
        return None
    if raw:
        return Path(raw).expanduser()
    if "_pytest" in sys.modules or "pytest" in sys.modules:
        return None
    from prismclaw.infra.paths import resolve_project_root

    return resolve_project_root() / "logs" / "app"


def _resolve_log_max_bytes() -> int:
    """单文件滚动阈值，PRISMCLAW_LOG_MAX_MB 可覆盖，默认 500MB。"""
    raw = (os.getenv("PRISMCLAW_LOG_MAX_MB") or "").strip()
    try:
        mb = int(raw) if raw else 500
    except ValueError:
        mb = 500
    return mb * 1024 * 1024


def _remove_stale_file_handlers() -> None:
    """拆除上一次 install_logging 的落盘管线（进程内重复配置 / 进程退出）。

    顺序必须与安装相反：先摘 QueueHandler（停止新记录入队），再停 listener
    （它会 flush 队列里剩余记录并关闭真实 handler），最后清理引用。
    """
    global _FILE_LISTENER
    for name in _FILE_TARGET_LOGGERS:
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if h in _FILE_QUEUE_HANDLERS:
                lg.removeHandler(h)
    for h in _FILE_QUEUE_HANDLERS:
        try:
            h.close()
        except Exception:
            pass
    _FILE_QUEUE_HANDLERS.clear()

    if _FILE_LISTENER is not None:
        try:
            _FILE_LISTENER.stop()
        except Exception:
            pass
        _FILE_LISTENER = None

    for h in _FILE_HANDLERS:
        try:
            h.close()
        except Exception:
            pass
    _FILE_HANDLERS.clear()


class _OncePerHandlerFilter(logging.Filter):
    """同一 handler 对象挂在多个 logger（root + uvicorn）上时，
    一条记录沿传播链可能多次到达同一 handler；按「记录 × handler」去重。"""

    def __init__(self, mark: str) -> None:
        super().__init__()
        self._mark = mark

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, self._mark, False):
            return False
        setattr(record, self._mark, True)
        return True


class _RawQueueHandler(QueueHandler):
    """QueueHandler 变体：把「格式化 + 异常渲染」整体留给消费线程。

    stdlib ``QueueHandler.prepare`` 会在**调用方线程**执行 ``self.format()``，
    即事件循环线程上仍要跑完整格式化（含 traceback 渲染）。本变体只做一次
    ``makeLogRecord`` 浅拷贝入队，``exc_info`` / ``args`` / ``stack_info``
    原样保留，由 QueueListener 线程上的真实 FileHandler 统一格式化。

    为什么必须重写而不是用默认实现：默认 prepare 会把 ``exc_info`` 置 None
    并把异常文本塞进 ``msg``，导致下游 handler 的 formatter 无法再按自己的
    格式（JSON 的 exception 字段 / text 的多行 traceback）渲染。
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # makeLogRecord 以 dict 更新到新 LogRecord，exc_info 等字段随之复制，
        # 且不会修改原记录（同一条记录还会发给 stdout handler）。
        return logging.makeLogRecord(record.__dict__)


# 单个基础文件保留的压缩归档上限
_LOG_ARCHIVE_KEEP = 14


class _ZipRotatingFileHandler(RotatingFileHandler):
    """按日期 + 大小滚动，滚动出的旧文件压缩为 zip 归档。

    - 跨天：新一天的第一条日志前，把昨天的内容归档为 log-20261110.log.zip
    - 超大小：当日内容达到阈值即归档，同名追加序号
      log-20261110.log.1.zip、log-20261110.log.2.zip …
    - 超过 _LOG_ARCHIVE_KEEP 个归档时删除最旧的
    """

    def __init__(self, filename: str | Path, *, max_bytes: int, keep: int, **kwargs) -> None:
        super().__init__(filename, maxBytes=max_bytes, **kwargs)
        self._keep = keep
        # 当前文件内容所属日期，用于归档命名；跨天时由 doRollover 重置
        self._period = date.today()

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: D102 — 覆盖 stdlib
        if date.today() != self._period:
            return True
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:  # noqa: D102 — 覆盖 stdlib
        if self.stream:
            self.stream.close()
            self.stream = None

        base_path = Path(self.baseFilename)
        if base_path.exists():
            date_str = self._period.strftime("%Y%m%d")
            stem = base_path.stem  # log / error
            target = base_path.parent / f"{stem}-{date_str}.log.zip"
            seq = 1
            while target.exists():
                target = base_path.parent / f"{stem}-{date_str}.log.{seq}.zip"
                seq += 1
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                zf.write(base_path, arcname=base_path.name)
            base_path.unlink()
            self._prune_old_archives(base_path)

        self._period = date.today()
        if not self.delay:
            self.stream = self._open()

    def _prune_old_archives(self, base_path: Path) -> None:
        archives = sorted(
            base_path.parent.glob(f"{base_path.stem}-*.log*.zip"),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(archives) - self._keep
        for old in archives[:excess]:
            try:
                old.unlink()
            except OSError:
                pass


# 每个 MCP 客户端视角的文件 handler（按 server 名去重，重复安装先摘旧的）
_MCP_FILE_HANDLERS: dict[str, list[logging.Handler]] = {}
# 每个 MCP server 独立的后台消费线程
_MCP_FILE_LISTENERS: dict[str, QueueListener] = {}


def sanitize_mcp_log_name(server_name: str) -> str:
    """server 名 → 日志目录/logger 安全名（非法字符替换为 _）。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", server_name.strip()).strip("._")
    return safe or "server"


def install_mcp_client_logging(server_name: str) -> logging.Logger:
    """客户端调用方视角：为单个 MCP server 安装独立双文件日志。

    目录 logs/mcp_client_<name>/（log.log + error.log），
    logger 名 prismclaw.mcp.client.<name>，propagate=False（不进主日志避免双写）。
    滚动压缩策略与主日志一致（>500MB → log-YYYYMMDD.log.zip）。
    返回该 server 的 logger。
    """
    safe = sanitize_mcp_log_name(server_name)
    lg = logging.getLogger(f"prismclaw.mcp.client.{safe}")

    # 幂等：已安装过则直接复用
    if safe in _MCP_FILE_HANDLERS:
        return lg

    log_dir = _resolve_log_dir()
    if log_dir is None:
        return lg
    target_dir = log_dir.parent / f"mcp_client_{safe}"

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        probe = target_dir / ".write_test"
        probe.touch()
        probe.unlink()
    except Exception:
        return lg

    file_formatter = (
        _build_json_formatter()
        if _resolve_log_format() == "json"
        else _build_text_formatter(colored=False)
    )
    max_bytes = _resolve_log_max_bytes()

    def _mk(filename: str, level: int) -> logging.Handler:
        h = _ZipRotatingFileHandler(
            target_dir / filename,
            max_bytes=max_bytes,
            keep=_LOG_ARCHIVE_KEEP,
            encoding="utf-8",
            delay=True,
        )
        h.setFormatter(file_formatter)
        h.setLevel(level)
        return h

    handlers = [
        _mk("log.log", logging.INFO),
        _mk("error.log", logging.WARNING),
    ]
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    # 与主日志同一原则：真实写盘不在事件循环线程。MCP 工具调用同样跑在
    # 主循环上，直接挂 FileHandler 会把「工具执行」变成同步 I/O 点。
    log_queue: _queue.SimpleQueue = _queue.SimpleQueue()
    listener = QueueListener(log_queue, *handlers, respect_handler_level=True)
    listener.start()
    queue_handler = _RawQueueHandler(log_queue)
    queue_handler.setLevel(logging.NOTSET)
    lg.addHandler(queue_handler)
    _MCP_FILE_HANDLERS[safe] = handlers
    _MCP_FILE_LISTENERS[safe] = listener
    return lg


def _install_file_handlers(file_formatter: logging.Formatter, root_level: int) -> None:
    """安装 log.log / error.log 双文件落盘管线（异步化写入）。

    - log.log   全量（跟随 root 级别）
    - error.log WARNING 及以上
    - 单文件超过 PRISMCLAW_LOG_MAX_MB（默认 500MB）滚动为
      log-YYYYMMDD.log.zip / error-YYYYMMDD.log.zip

    关键：真实 FileHandler **不直接挂在业务 logger 上**，而是由 QueueListener
    持有的后台线程消费。事件循环线程只执行 ``QueueHandler.emit``（一次
    ``makeLogRecord`` + 无界队列 put_nowait），因此格式化、文件写入、
    滚动压缩（``_ZipRotatingFileHandler.doRollover`` 的同步 zip）全部离开
    事件循环线程 —— 这是「decide 偶发 47s」与「leader 锁心跳丢失」的共同
    根因（同步 I/O 阻塞事件循环，导致任何 await 都无法被调度）。
    """
    global _FILE_LISTENER, _ATEXIT_REGISTERED

    _remove_stale_file_handlers()

    log_dir = _resolve_log_dir()
    if log_dir is None:
        return

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".write_test"
        probe.touch()
        probe.unlink()
    except Exception:
        # 目录不可写（如只读容器文件系统）：静默退回纯 stdout/stderr
        return

    max_bytes = _resolve_log_max_bytes()

    def _mk(filename: str, level: int) -> logging.Handler:
        h = _ZipRotatingFileHandler(
            log_dir / filename,
            max_bytes=max_bytes,
            keep=_LOG_ARCHIVE_KEEP,
            encoding="utf-8",
            delay=True,
        )
        h.setFormatter(file_formatter)
        h.setLevel(level)
        return h

    file_handlers = [
        _mk("log.log", root_level),
        _mk("error.log", logging.WARNING),
    ]
    _FILE_HANDLERS.extend(file_handlers)

    # SimpleQueue：无界、无锁竞争开销，且 put_nowait 永不阻塞调用方线程。
    log_queue: _queue.SimpleQueue = _queue.SimpleQueue()
    listener = QueueListener(
        log_queue, *file_handlers, respect_handler_level=True
    )
    listener.start()
    _FILE_LISTENER = listener

    queue_handler = _RawQueueHandler(log_queue)
    queue_handler.setLevel(logging.NOTSET)
    # 一条记录沿传播链（uvicorn → root）可能多次到达同一个 QueueHandler，
    # 按「记录 × 排队管线」去重，保证每个文件只写一行。
    queue_handler.addFilter(_OncePerHandlerFilter("_prismclaw_file_queued"))
    _FILE_QUEUE_HANDLERS.append(queue_handler)
    for name in _FILE_TARGET_LOGGERS:
        logging.getLogger(name).addHandler(queue_handler)

    if not _ATEXIT_REGISTERED:
        atexit.register(shutdown_logging)
        _ATEXIT_REGISTERED = True


def shutdown_logging() -> None:
    """停止日志消费线程并 flush 队列（进程退出 / 测试清理）。

    幂等：重复调用只做一次拆除。挂在 atexit 上保证相对 ``logging.shutdown``
    （logging 模块导入期注册）先执行 —— atexit 按注册逆序运行。
    """
    _remove_stale_file_handlers()
    for safe, listener in list(_MCP_FILE_LISTENERS.items()):
        lg = logging.getLogger(f"prismclaw.mcp.client.{safe}")
        for h in list(lg.handlers):
            if isinstance(h, _RawQueueHandler):
                lg.removeHandler(h)
        try:
            listener.stop()
        except Exception:
            pass
    _MCP_FILE_LISTENERS.clear()
    _MCP_FILE_HANDLERS.clear()


def _console_enabled() -> bool:
    """控制台输出开关：PRISMCLAW_LOG_CONSOLE=off 时只落盘。

    本地测试由启动脚本把 stdout/stderr 重定向到 log.log，此时 uvicorn 自带的
    StreamHandler 会和文件 handler 写同一份内容；关掉控制台可避免重复行。
    """
    raw = (os.getenv("PRISMCLAW_LOG_CONSOLE") or "").strip().lower()
    return raw not in {"0", "off", "false", "no"}


def _install_log_format(*, use_json: bool = False) -> None:
    formatter = _build_json_formatter() if use_json else _build_text_formatter()

    root = logging.getLogger()
    if not _console_enabled():
        for name in _FILE_TARGET_LOGGERS:
            lg = logging.getLogger(name)
            for h in list(lg.handlers):
                if type(h) is logging.StreamHandler:
                    lg.removeHandler(h)
        return

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)

    for h in root.handlers:
        h.setFormatter(formatter)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "prismclaw"):
        lg = logging.getLogger(name)
        for h in lg.handlers:
            h.setFormatter(formatter)


def _install_noise_filters(*, litellm_debug: bool) -> None:
    class _PrismClawNoiseFilter(logging.Filter):
        _prismclaw_noise_filter = True

        def filter(self, record: logging.LogRecord) -> bool:
            name = record.name or ""
            try:
                msg = record.getMessage()
            except Exception:
                msg = ""

            if name.startswith("watchfiles") and record.levelno < logging.INFO:
                if not _is_truthy(os.getenv("PRISMCLAW_WATCHFILES_DEBUG")):
                    if "/.idea/" in msg or "workspace.xml" in msg:
                        return False

            if name == "LiteLLM" or name == "litellm" or name.startswith("litellm."):
                if litellm_debug:
                    return True
                if record.levelno < logging.WARNING:
                    return False
                if "response_cost_failure_debug_information" in msg:
                    return False

            return True

    root = logging.getLogger()
    for h in root.handlers:
        if any(getattr(f, "_prismclaw_noise_filter", False) for f in getattr(h, "filters", [])):
            continue
        h.addFilter(_PrismClawNoiseFilter())


def _configure_library_loggers(*, litellm_debug: bool) -> None:
    logging.getLogger("LiteLLM").setLevel(logging.DEBUG if litellm_debug else logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.DEBUG if litellm_debug else logging.WARNING)
    try:
        import litellm

        litellm.suppress_debug_info = not litellm_debug
    except Exception:
        return


def _install_access_log_filter() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    for existing in access_logger.filters:
        if getattr(existing, "_prismclaw_static_filter", False):
            return

    silent = re.compile(r' "(GET|HEAD) /(assets/|favicon\.|@vite/|@react-refresh|src/)')

    class _DropStaticAssets(logging.Filter):
        _prismclaw_static_filter = True

        def filter(self, record: logging.LogRecord) -> bool:
            try:
                msg = record.getMessage()
            except Exception:
                return True
            return silent.search(msg) is None

    access_logger.addFilter(_DropStaticAssets())


def install_logging(
    *,
    level: str | int | None = None,
    logger_levels: Mapping[str, str | int] | None = None,
    log_format: str | None = None,
) -> None:
    litellm_debug = _litellm_debug_enabled()
    use_json = (log_format or _resolve_log_format()) == "json"
    _install_log_format(use_json=use_json)
    root_level = _resolve_root_level(level)
    overrides = _resolve_logger_level_overrides(root_level, logger_levels=logger_levels)
    _apply_log_levels(root_level, logger_levels=overrides)
    _install_file_handlers(
        _build_json_formatter() if use_json else _build_text_formatter(colored=False),
        root_level,
    )
    _install_noise_filters(litellm_debug=litellm_debug)
    _configure_library_loggers(litellm_debug=litellm_debug)
    _install_access_log_filter()
