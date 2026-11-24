"""Type stubs for ``odf.table``."""

from typing import Any

from .opendocument import Element

class Table(Element):
    def __init__(self, name: str | None = ..., **kwargs: Any) -> None: ...

class TableRow(Element): ...
class TableCell(Element): ...
