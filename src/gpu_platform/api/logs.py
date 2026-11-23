import asyncio
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .auth import authenticate

router = APIRouter()


@router.websocket("/api/tasks/{task_id}/logs")
async def stream_logs(ws: WebSocket, task_id: str):
    origin = ws.headers.get("origin")
    if origin and urlsplit(origin).netloc != ws.headers.get("host"):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 5)
        if not isinstance(hello, dict) or not isinstance(hello.get("token"), str):
            await ws.close(code=1008)
            return
        user = authenticate(ws.app.state.settings, hello["token"])
        offset = hello.get("offset", 0)
        if type(offset) is not int or not 0 <= offset < 2**63:
            await ws.close(code=1008)
            return
        # Clients can disconnect while a job is quiet or still queued.
        incoming = asyncio.create_task(ws.receive())
        try:
            while not incoming.done():
                task = await ws.app.state.engine.get_owned(task_id, user)
                ended = task["state"] in {"succeeded", "failed", "cancelled"}
                chunk = {"text": "", "next_offset": offset}
                if task["run_id"]:
                    chunk = await ws.app.state.runner.call(
                        "GET", f"/runs/{task['run_id']}/logs", params={"offset": offset})
                advanced = chunk["next_offset"] > offset
                if advanced or ended:
                    await asyncio.wait_for(ws.send_json({**chunk, "done": ended and not advanced}), 8)
                offset = chunk["next_offset"]
                if not advanced:
                    if ended:
                        await ws.close()
                        return
                    await asyncio.wait([incoming], timeout=0.4)
        finally:
            incoming.cancel()
            await asyncio.gather(incoming, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except (ValueError, HTTPException, asyncio.TimeoutError):
        await ws.close(code=1008)
    except httpx.HTTPError:
        await ws.close(code=1013)
