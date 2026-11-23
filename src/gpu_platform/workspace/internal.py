"""Local runner routes. All filesystem and session I/O runs as the target user."""
import asyncio
import codecs
import contextlib
import json
import os
from pathlib import Path
import pwd
import sys

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field


router = APIRouter(prefix="/workspace", include_in_schema=False)


class FileRead(BaseModel):
    user: str
    path: str = Field(max_length=4096)


class FileWrite(FileRead):
    text: str = Field(max_length=2 * 1024 * 1024)


class Connect(BaseModel):
    user: str
    run_id: str


async def user_process(settings, user, operation, *args):
    if user not in settings.users:
        raise HTTPException(403, "账号未配置")
    try:
        account = pwd.getpwnam(user)
    except KeyError as error:
        raise HTTPException(403, "Unix 账号不存在") from error
    if account.pw_uid == 0 or (os.geteuid() != 0 and account.pw_uid != os.geteuid()):
        raise HTTPException(403, "执行器不能使用该用户身份")
    credentials = {}
    if os.geteuid() == 0:
        if not settings.system_mode or account.pw_uid < 1000 or account.pw_uid == 65534:
            raise HTTPException(403, "系统执行器只接受已配置的普通用户")
        credentials = dict(user=account.pw_uid, group=account.pw_gid,
                           extra_groups=os.getgrouplist(user, account.pw_gid))
    env = {"HOME": account.pw_dir, "USER": user, "LOGNAME": user,
           "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    if settings.system_mode and operation in {"read", "write"}:
        operation = "scoped_" + operation
    helper = Path(__file__).with_name("user_io.py")
    return await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(helper.resolve()), operation, *args, env=env, cwd="/",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, **credentials)


async def action(request, operation, payload):
    process = await user_process(request.app.state.settings, payload.user, operation)
    try:
        output, _ = await asyncio.wait_for(process.communicate(payload.model_dump_json().encode()), 20)
    except asyncio.TimeoutError as error:
        raise HTTPException(504, "本机操作超时") from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    try:
        result = json.loads(output)
    except ValueError as error:
        raise HTTPException(502, "用户进程未返回结果，请检查执行器安装路径及权限") from error
    if process.returncode:
        status = 409 if operation == "connect" and result.get("status") == 404 else result.get("status", 400)
        detail = "Jupyter 正在启动或会话已结束" if status == 409 else result.get("detail")
        raise HTTPException(status, detail)
    return result


@router.post("/files/read")
async def files_read(request: Request, payload: FileRead):
    return await action(request, "read", payload)


@router.post("/files/write")
async def files_write(request: Request, payload: FileWrite):
    return await action(request, "write", payload)


class ProfileRequest(BaseModel):
    user: str
    cwd: str
    argv: list[str]
    preferred: str | None = None


@router.post("/profile")
async def profile(request: Request, payload: ProfileRequest):
    payload.preferred = request.app.state.settings.interpreters.get(payload.user)
    return await action(request, "profile", payload)


@router.post("/connect")
async def connect(request: Request, payload: Connect):
    return await action(request, "connect", payload)


async def until_disconnect(*coroutines):
    tasks = [asyncio.create_task(coro) for coro in coroutines]
    try:
        finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in finished:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.websocket("/terminal/{user}/{run_id}")
async def terminal(websocket: WebSocket, user: str, run_id: str):
    process = await user_process(websocket.app.state.settings, user, "attach", run_id)
    await websocket.accept()

    async def read_browser():
        while True:
            message = await websocket.receive_text()
            if len(message) > 128 * 1024:
                raise ValueError("输入过长")
            frame = {"input": message}
            if message.startswith('{"resize"'):
                try:
                    resize = json.loads(message)["resize"]
                except (ValueError, KeyError, TypeError):
                    resize = None
                if isinstance(resize, list) and len(resize) == 2 and all(type(x) is int for x in resize):
                    frame = {"resize": resize}
            process.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode())
            await process.stdin.drain()

    async def write_browser():
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while chunk := await process.stdout.read(32768):
            await websocket.send_text(decoder.decode(chunk))

    try:
        await until_disconnect(read_browser(), write_browser())
    except (WebSocketDisconnect, OSError, RuntimeError, ValueError):
        pass
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
        await process.wait()
        with contextlib.suppress(RuntimeError):
            await websocket.close()
