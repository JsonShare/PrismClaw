# 工具集

## 职责概述

工具集（Toolkit）模块为 Agent 提供「手」——一组可被 LLM 调用的工具，涵盖文件读写、Bash 命令执行、目录列表、文件搜索、任务管理、用户交互等能力。模块同时提供文件观察器（FileObserver）、文件变更队列（mutation queue）和 OCR 缓存等基础设施，确保并发安全和一致性。

## 架构总览

**[查看交互式架构图](../diagrams/10-toolkit.html)**

```
┌──────────────────────────────────────────────────────────────────┐
│                        Toolkit 双层架构                           │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                 Builtin 内置工具层                           │ │
│  │                                                             │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │ │
│  │  │   Bash   │ │  BashRead│ │   Read   │ │  Write   │      │ │
│  │  │ (write)  │ │  (read)  │ │  (read)  │ │ (write)  │      │ │
│  │  └────┬─────┘ └──────────┘ └────┬─────┘ └────┬─────┘      │ │
│  │       │                        │              │             │ │
│  │  ┌────┴─────┐ ┌──────────┐     │              │             │ │
│  │  │   Edit   │ │    LS    │     │              │             │ │
│  │  │ (write)  │ │  (read)  │     │              │             │ │
│  │  └────┬─────┘ └──────────┘     │              │             │ │
│  │       │                        │              │             │ │
│  │  ┌────┴────────────────────────┴──────────────┘             │ │
│  │  │           read_epoch 乐观并发控制                         │ │
│  │  └──────────────────────┬──────────────────────┘            │ │
│  │                         │                                   │ │
│  │  ┌──────────────┐ ┌────────────────────┐                    │ │
│  │  │ CancelTask   │ │  AskUserQuestion   │                    │ │
│  │  │ (skill-gated)│ │  (interactive)     │                    │ │
│  │  └──────────────┘ └────────────────────┘                    │ │
│  └─────────────────────────┼───────────────────────────────────┘ │
│                            │ 读写依赖                            │
│                            ▼                                     │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                File 文件基础设施层                           │ │
│  │                                                             │ │
│  │  ┌────────────────────┐  ┌────────────────────────────┐    │ │
│  │  │   FileObserver     │  │  FileMutationQueue         │    │ │
│  │  │ (SHA-256 版本跟踪) │  │  (每文件 asyncio.Lock      │    │ │
│  │  │                    │  │   并发写入串行化)           │    │ │
│  │  └─────────┬──────────┘  └────────────────────────────┘    │ │
│  │            │ read_epoch                                     │ │
│  │  ┌─────────┴──────────┐  ┌────────────────────────────┐    │ │
│  │  │     GlobTool       │  │      GrepTool              │    │ │
│  │  │  (文件模式搜索)     │  │   (内容搜索)               │    │ │
│  │  └────────────────────┘  └────────────────────────────┘    │ │
│  │                                                             │ │
│  │  ┌────────────────────┐                                    │ │
│  │  │    OCR Cache       │                                    │ │
│  │  │ (stat指纹缓存)     │                                    │ │
│  │  └────────────────────┘                                    │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

## 核心组件

### BashTool

```python
# builtin/bash.py
class BashTool:
    name = "bash"
    tool_class = "write"
    # 持久 PTY 会话
    # blocking_budget_ms: 15 秒上限
    # hard_timeout: 强制终止
    # 软超时时 detach
    # 执行: runtime.bash()
```

### ReadTool

```python
# builtin/file_tools.py
class ReadTool:
    name = "read"
    tool_class = "read"
    # 分页读取: DEFAULT_PAGE_LINES = 2000
    # MAX_LINE_LENGTH = 2000
    # read_epoch (SHA-256) 乐观并发控制
    # 自动重定向: 图片/Office/PDF → 专用工具
```

### WriteTool

```python
# builtin/file_tools.py
class WriteTool:
    name = "write"
    tool_class = "write"
    # 二进制扩展名阻断（_blocked_binary_extension）
    # review registry 集成
    # read_epoch 跟踪
```

### EditTool

```python
# builtin/file_tools.py
class EditTool:
    name = "edit"
    tool_class = "write"
    # old_string → new_string 替换
    # read_epoch 验证（乐观并发）
    # Python 语法检查
    # 模糊匹配提示
    # review registry 集成
```

### ListDirectoryTool

```python
# builtin/file_tools.py
class ListDirectoryTool:
    name = "ls"
    tool_class = "read"
    # 执行: runtime.fs_list()
```

### CancelTaskTool

```python
# builtin/task_tools.py
class CancelTaskTool:
    name = "cancel_task"
    tool_class = "write"
    skill_gated = True
    # 通过 ParallelTaskStore 取消任务
```

### AskUserQuestionTool

```python
# builtin/task_tools.py
class AskUserQuestionTool:
    name = "AskUserQuestion"
    tool_class = "read"
    requires_interactive_user = True
    # 1-4 个问题，每个 2-4 个选项
```

### GlobTool

```python
# file/glob.py
class GlobTool:
    name = "glob"
    tool_class = "read"
    # 执行: runtime.fs_glob()
    # max_results = 100
    # 工作区边界强制
```

### GrepTool

```python
# file/grep.py
class GrepTool:
    name = "grep"
    tool_class = "read"
    # 执行: runtime.fs_grep()
    # max_matches = 250
    # case_sensitive 选项
```

### FileObserver

```python
# file/file_observer.py
class FileObserver:
    # 跟踪 FileVersion(read_epoch=SHA-256, line_count)
    # VersionCheck 枚举: OK / STALE / UNKNOWN
    # sync_to_dict() / sync_from_dict() 序列化
```

每次 `ReadTool` 读取文件后，FileObserver 记录文件的 SHA-256 摘要和行数。后续 `EditTool` / `WriteTool` 写入前检查 `read_epoch`，若不一致则拒绝写入（乐观并发控制）。

### FileMutationQueue

```python
# file/file_mutation_queue.py
def with_file_mutation_queue()   # 每文件 asyncio.Lock 串行化并发写入
def clear_mutation_queues()      # session 结束清理
```

确保同一文件的并发写入操作按序执行，避免竞态条件。

### OCR 缓存

```python
# file/ocr_cache.py
def ocr_cache_path()      # 基于 stat 指纹的缓存路径（.extracted/.ocr_cache/）
def read_ocr_cache()      # 读取缓存
def write_ocr_cache()     # 写入缓存
def analysis_md_path()    # 分析结果 Markdown 路径
```

缓存键基于文件的 stat 信息（mtime + size）自动失效，避免重复 OCR 处理。

### 辅助常量与函数

```python
# builtin/file_helpers.py
BASH_DEFAULT_TIMEOUT_SECONDS = 120.0
_READ_DEFAULT_PAGE_LINES = 2000
_BASH_READBACK_MAX_CHARS = 200_000

_blocked_binary_extension()    # 阻断 .docx/.xlsx/.pptx/.pdf 写入
_binary_write_guidance()       # 提供替代方案提示
build_file_change()            # difflib hunk 计算（UI artifact），大文件截断
```

```python
# builtin/tool_helpers.py
_is_ask_user_question()
_extract_file_paths_from_tool_calls()
_clarify_timeout()
_build_budget_warning_message()
_entry_to_llm_dict()
```

## 关键数据流

### 文件读取 → 编辑流（乐观并发控制）

```
ReadTool.execute()
  → 读取文件内容
  → FileObserver 记录 read_epoch = SHA-256(file_content)
  → 返回内容 + read_epoch

EditTool.execute(old_string, new_string, read_epoch)
  → FileObserver 检查: VersionCheck
    → STALE: 拒绝编辑，返回文件已变更错误
    → OK/UNKNOWN: 继续
  → with_file_mutation_queue(file_path):
    → 执行 old_string → new_string 替换
    → Python 语法检查（.py 文件）
    → 更新 FileObserver read_epoch
  → build_file_change() 计算 diff hunk
  → 写入 ui_artifact
```

### Bash 命令执行流

```
BashTool.execute(command, blocking_budget_ms)
  → _clarify_timeout() 计算 hard_timeout
  → runtime.bash(command)
    → 持久 PTY 会话中执行
    → 软超时: detach（进程继续运行）
    → 硬超时: 强制终止
  → _build_budget_warning_message()（超限时）
```

### 文件搜索流

```
GlobTool.execute(pattern)
  → runtime.fs_glob(pattern)
  → max_results=100 截断
  → 工作区边界校验

GrepTool.execute(pattern, case_sensitive)
  → runtime.fs_grep(pattern)
  → max_matches=250 截断
```

### OCR 缓存流

```
ReadTool 检测图片/PDF → 重定向到专用工具
  → ocr_cache_path(file_path)  # stat 指纹 → 缓存键
  → read_ocr_cache()           # 命中 → 直接返回
  → 未命中 → 执行 OCR → write_ocr_cache()
```

### 并发写入保护流

```
多个并发 EditTool/WriteTool 请求同一文件
  → with_file_mutation_queue(file_path)
    → 每文件 asyncio.Lock 串行化
    → 逐个执行写入操作
  → clear_mutation_queues()  # session 结束清理
```

## 对外接口

| 接口 | 类型 | 工具类 | 说明 |
|------|------|--------|------|
| `BashTool` (name=`bash`) | 工具 | write | 持久 PTY Bash 执行 |
| `ReadTool` (name=`read`) | 工具 | read | 分页文件读取 + read_epoch |
| `WriteTool` (name=`write`) | 工具 | write | 文件写入 + 二进制阻断 |
| `EditTool` (name=`edit`) | 工具 | write | 字符串替换编辑 + 并发控制 |
| `ListDirectoryTool` (name=`ls`) | 工具 | read | 目录列表 |
| `GlobTool` (name=`glob`) | 工具 | read | 文件模式搜索 |
| `GrepTool` (name=`grep`) | 工具 | read | 文件内容搜索 |
| `CancelTaskTool` (name=`cancel_task`) | 工具 | write | 取消并行任务（skill-gated） |
| `AskUserQuestionTool` (name=`AskUserQuestion`) | 工具 | read | 用户问答交互 |
| `FileObserver` | 基础设施 | — | SHA-256 文件版本跟踪 |
| `with_file_mutation_queue()` | 基础设施 | — | 并发写入串行化 |

## 依赖关系

```
工具集模块
  ├── engine.runtime           — BashRuntime（bash/fs_list/fs_glob/fs_grep）
  ├── engine.security.sandbox  — 沙箱策略与路径校验
  ├── engine.services.review   — review registry（WriteTool/EditTool 集成）
  ├── schema.agent             — ToolContext, ToolResult
  ├── engine.subagent.runtime  — ParallelTaskStore（CancelTaskTool）
  ├── persistence.session      — session 存储（OCR 缓存路径）
  └── infra.settings           — 配置
```

## 配置项

| 配置 | 位置 | 说明 | 默认值 |
|------|------|------|--------|
| `BASH_DEFAULT_TIMEOUT_SECONDS` | `file_helpers.py` | Bash 默认超时 | `120.0` |
| `_READ_DEFAULT_PAGE_LINES` | `file_helpers.py` | ReadTool 分页行数 | `2000` |
| `_BASH_READBACK_MAX_CHARS` | `file_helpers.py` | Bash 回显最大字符数 | `200_000` |
| `MAX_LINE_LENGTH` | `file_tools.py` (ReadTool) | 单行最大长度 | `2000` |
| `blocking_budget_ms` | BashTool 参数 | 阻塞预算上限 | `15000` |
| `max_results` | GlobTool | 搜索结果上限 | `100` |
| `max_matches` | GrepTool | 匹配结果上限 | `250` |
