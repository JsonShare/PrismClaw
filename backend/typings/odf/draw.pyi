"""Type stubs for ``odf.draw``."""

from typing import Any

from .opendocument import Element

class Page(Element):
    def __init__(self, **kwargs: Any) -> None: ...
