from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:
        # Ignore api paths to let the FastAPI routers handle them
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
            
        is_html = False
        try:
            response = await super().get_response(path, scope)
            if path == "" or path.endswith(".html"):
                is_html = True
        except HTTPException:
            response = await super().get_response("index.html", scope)
            is_html = True
            
        if is_html or "text/html" in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            
        return response
