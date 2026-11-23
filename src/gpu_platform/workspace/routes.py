"""Authenticated workbench endpoints; the API never opens the user's files."""
import asyncio
import contextlib
import shlex
import socket
from urllib.parse import quote, urlsplit

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gpu_platform.api.auth import authenticate, current_user
from gpu_platform.workspace.internal import until_disconnect


class SavedFile(BaseModel):
    path: str = Field(max_length=4096)
    text: str = Field(max_length=2 * 1024 * 1024)


async def workspace_call(request, path, body):
    try:
        return await request.app.state.runner.call("POST", "/workspace/" + path, json=body)
    except httpx.HTTPStatusError as error:
        response = error.response
        if response.status_code in {400, 403, 404, 409, 422, 504}:
            raise HTTPException(response.status_code, response.json().get("detail", "操作失败")) from error
        raise


def create_router():
    router = APIRouter(prefix="/api")

    @router.get("/files")
    async def read(request: Request, path: str = Query(max_length=4096), user: str = Depends(current_user)):
        return await workspace_call(request, "files/read", {"path": path, "user": user})

    @router.put("/files")
    async def save(request: Request, item: SavedFile, user: str = Depends(current_user)):
        return await workspace_call(request, "files/write", {**item.model_dump(), "user": user})

    @router.get("/tasks/{task_id}/connect")
    async def connect(request: Request, task_id: str, user: str = Depends(current_user)):
        item = await request.app.state.engine.get_owned(task_id, user)
        if item["kind"] != "jupyter" or item["state"] != "running":
            raise HTTPException(409, "Jupyter 会话尚未运行")
        result = await workspace_call(request, "connect", {"run_id": item["run_id"], "user": user})
        port = int(result["port"])
        destination = shlex.quote(user + "@" + socket.gethostname())
        return JSONResponse({
            "url": f"http://127.0.0.1:{port}/lab?token={quote(result['token'], safe='')}",
            "tunnel": f"ssh -N -L {port}:127.0.0.1:{port} -- {destination}",
        }, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @router.websocket("/terminal/{task_id}")
    async def attach(websocket: WebSocket, task_id: str):
        origin = websocket.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc != websocket.headers.get("host"):
                await websocket.close(code=1008)
                return
        await websocket.accept()
        try:
            payload = await asyncio.wait_for(websocket.receive_json(), 5)
            if not isinstance(payload, dict) or not isinstance(payload.get("token"), str):
                raise HTTPException(401, "登录凭据无效")
            settings = websocket.app.state.settings
            user = authenticate(settings, payload["token"])
            item = await websocket.app.state.engine.get_owned(task_id, user)
            if item["kind"] != "terminal" or item["state"] != "running":
                raise HTTPException(409, "终端尚未运行")
            uri = "ws://localhost/workspace/terminal/" + quote(user, safe="") + "/" + quote(item["run_id"], safe="")
            async with websockets.unix_connect(str(settings.socket), uri=uri, open_timeout=5) as runner:
                async def to_runner():
                    while True:
                        await runner.send(await websocket.receive_text())

                async def to_browser():
                    async for message in runner:
                        await websocket.send_text(message)

                await until_disconnect(to_runner(), to_browser())
        except (HTTPException, ValueError, asyncio.TimeoutError, WebSocketDisconnect, OSError,
                RuntimeError, websockets.WebSocketException):
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1008)
            return
        with contextlib.suppress(RuntimeError):
            await websocket.close()
    return router
