"""Entry point.

stdio (default, dev):   python -m web_search_mcp
http (prod container):  python -m web_search_mcp --transport http --port 9002
"""

from __future__ import annotations

import argparse

import uvicorn

from .config import Settings
from .server.app import build_http_app, mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="web-search-mcp")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9002)
    args = parser.parse_args()

    settings = Settings()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        uvicorn.run(
            build_http_app(),
            host=args.host,
            port=args.port,
            log_level=settings.log_level.lower(),
        )


if __name__ == "__main__":
    main()
