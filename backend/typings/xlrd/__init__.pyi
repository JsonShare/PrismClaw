"""Type stubs for ``xlrd`` covering the legacy .xls read path used here."""

from __future__ import annotations

from typing import Any

class Sheet:
    name: str
    nrows: int
    ncols: int
    def row_values(self, rowx: int, start_colx: int = ..., end_colx: int | None = ...) -> list[Any]: ...
    def cell_value(self, rowx: int, colx: int) -> Any: ...

class Book:
    def sheets(self) -> list[Sheet]: ...
    def sheet_by_index(self, sheetx: int) -> Sheet: ...
    def sheet_names(self) -> list[str]: ...
    def release_resources(self) -> None: ...

def open_workbook(
    filename: str | None = ...,
    logfile: Any = ...,
    verbosity: int = ...,
    use_mmap: bool = ...,
    file_contents: bytes | None = ...,
    encoding_override: str | None = ...,
    formatting_info: bool = ...,
    on_demand: bool = ...,
    ragged_rows: bool = ...,
) -> Book: ...
