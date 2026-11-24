"""Type stubs for the ``odf`` (odfpy) subset used by the office reader."""

from __future__ import annotations

from typing import Any

class Element:
    qname: tuple[str | None, str]
    childNodes: list[Element]
    attributes: dict[str, Any]
    def getElementsByType(self, type: object, **kwargs: Any) -> list[Element]: ...
    def getAttribute(self, attr: str) -> str | None: ...
    def addElement(self, element: Element) -> None: ...

class OpenDocument:
    text: Element
    spreadsheet: Element
    presentation: Element
    def save(self, outputfile: str | Any, addsuffix: bool = ...) -> None: ...

def load(inputfile: str | Any) -> OpenDocument: ...
