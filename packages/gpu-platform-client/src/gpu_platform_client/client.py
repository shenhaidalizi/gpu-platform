"""HTTP interface shared by the CLI and MCP tools."""
import asyncio
import os
import re
from pathlib import Path, PurePosixPath
from time import monotonic

import httpx



DONE = {"succeeded", "failed", "cancelled"}


class PlatformError(Exception):
    pass


def connect():
    """Create a client using the caller's own platform credentials."""
    origin = os.environ.get("GPU_PLATFORM_URL", "http://127.0.0.1:8765").rstrip("/")
    url = httpx.URL(origin)
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment:
        raise PlatformError("GPU_PLATFORM_URL 需要填写平台 HTTP(S) 地址。")
    token_path = Path(os.environ.get("GPU_PLATFORM_TOKEN_FILE", "~/.config/gpu-platform/access.token")).expanduser()
    try:
        token = token_path.read_text().strip()
    except OSError as exc:
        raise PlatformError(f"无法读取凭据文件 {token_path}") from exc
    if not token or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise PlatformError("凭据文件格式无效。")
    return httpx.AsyncClient(base_url=origin + "/api/", headers={"Authorization": f"Bearer {token}"},
                             timeout=10, trust_env=False)


def task_path(task_id):
    if not re.fullmatch("[0-9a-f]{32}", task_id):
        raise PlatformError("任务 ID 应为平台返回的 32 位十六进制字符串。")
    return f"tasks/{task_id}"


class Jobs:
    def __init__(self, http):
        self.http = http

    async def request(self, method, path, **kwargs):
        hint = " 请先列出自己的任务核对提交结果，避免重复提交。" if (method, path) == ("POST", "tasks") else ""
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise PlatformError(f"平台请求失败 ({type(exc).__name__})。{hint}") from exc
        if not response.is_success:
            try:
                detail = response.json().get("detail", response.reason_phrase)
            except (ValueError, AttributeError):
                detail = response.reason_phrase
            raise PlatformError(f"HTTP {response.status_code} {detail}" + (hint if response.is_server_error else ""))
        try:
            return response.json()
        except (ValueError, UnicodeError) as exc:
            raise PlatformError(f"平台响应不是有效 JSON。{hint}") from exc

    async def submit(self, cwd, argv, gpu_count=1):
        if not PurePosixPath(cwd).is_absolute() or not argv or not argv[0] or gpu_count < 1:
            raise PlatformError("需要服务器上的绝对工作目录、非空命令和正整数 GPU 张数。")
        spec = dict(kind="batch", cwd=cwd, argv=argv, gpu_count=gpu_count)
        task = await self.request("POST", "tasks", json=spec)
        if not isinstance(task, dict) or not task.get("id"):
            raise PlatformError("提交结果缺少任务 ID。请先列出自己的任务核对。")
        return task

    async def tasks(self):
        return await self.request("GET", "tasks")

    async def get(self, task_id):
        return await self.request("GET", task_path(task_id))

    async def cancel(self, task_id):
        return await self.request("POST", task_path(task_id) + "/cancel")

    async def logs(self, task_id, offset=0):
        if offset < 0:
            raise PlatformError("日志偏移量必须大于等于 0。")
        page = await self.request("GET", task_path(task_id) + "/logs", params={"offset": offset})
        page.setdefault("next_offset", offset)  # Queued tasks have no run log yet.
        return page

    async def platform(self):
        gpus, queue = await asyncio.gather(self.request("GET", "gpus"), self.request("GET", "queue"))
        return {"gpus": gpus, "queue": queue}

    async def wait(self, task_id, timeout, offset=0):
        """Return a state snapshot and one resumable log page. Never mutate the task."""
        if not 0 <= timeout <= 3600 or offset < 0:
            raise PlatformError("等待时间范围为 0 到 3600 秒，日志偏移量必须大于等于 0。")
        deadline = monotonic() + timeout
        try:
            while True:
                task = await self.get(task_id)
                remaining = deadline - monotonic()
                if task["state"] in DONE or remaining <= 0:
                    break
                await asyncio.sleep(min(1, remaining))
            logs = await self.logs(task_id, offset)
        except PlatformError as exc:
            raise PlatformError(f"任务 {task_id} 查询失败。可以按此 ID 继续查询。{exc}") from exc
        return {"task": task, "done": task["state"] in DONE, "logs": logs}
