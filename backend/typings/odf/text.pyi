"""Type stubs for ``odf.text``."""

from typing import Any

from .opendocument import Element

class P(Element):
    def __init__(self, **kwargs: Any) -> None: ...

class H(Element):
    def __init__(self, **kwargs: Any) -> None: ...
