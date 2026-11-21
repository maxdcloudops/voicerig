"""A stand-in for the agent's backend. It answers with canned JSON and RECORDS every call, so a scenario can
assert "the agent tried to cancel the order" (or did not).

    GET  /__calls     every call so far: [{"t", "method", "path", "body"}]
    POST /__reset     forget them

Routes come from `stub.routes` in rig.yaml: path (fnmatch globs allowed) -> {status, json, delay}.
Anything unmatched answers 200 {"ok": true}. Headers are never recorded, so credentials the agent sends
cannot end up in a results file.
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
import time
from typing import Any

from aiohttp import web

DEFAULT = {"status": 200, "json": {"ok": True}}


def make_app(routes: dict[str, dict[str, Any]]) -> web.Application:
    calls: list[dict] = []

    def route_for(path: str) -> dict[str, Any]:
        if path in routes:
            return routes[path]
        for pattern, spec in routes.items():
            if fnmatch.fnmatch(path, pattern):
                return spec
        return DEFAULT

    async def handle(request: web.Request) -> web.Response:
        if request.path == "/__calls":
            return web.json_response(calls)
        if request.path == "/__reset":
            calls.clear()
            return web.json_response({"ok": True})
        try:
            body = await request.json()
        except Exception:
            body = {}
        calls.append({"t": time.time(), "method": request.method, "path": request.path,
                      "query": dict(request.query), "body": body if isinstance(body, dict) else {"_": body}})
        spec = {**DEFAULT, **route_for(request.path)}
        if spec.get("delay"):
            await asyncio.sleep(float(spec["delay"]))
        return web.json_response(spec["json"], status=int(spec["status"]))

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def main() -> None:
    from .config import load
    cfg = load(os.environ.get("RIG_CONFIG", "/rig/rig.yaml"), check_audio=False)
    web.run_app(make_app(cfg.stub.get("routes", {})), host="0.0.0.0",
                port=int(os.getenv("STUB_PORT", "8080")), print=lambda *_: None)


if __name__ == "__main__":
    main()
