from app.main import app as fastapi_app


class ApiPrefixApp:
    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            path = scope.get("path", "")
            scope = dict(scope)
            scope["root_path"] = ""
            if path and path != "/health" and not path.startswith("/api"):
                scope["path"] = f"/api{path}"
        await fastapi_app(scope, receive, send)


app = ApiPrefixApp()
