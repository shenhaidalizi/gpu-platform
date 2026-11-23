import asyncio
import os

import httpx

from gpu_platform.api.app import create_app
from gpu_platform.config import Settings, digest


class IdleRunner:
    def __init__(self, socket):
        self.client = self

    async def aclose(self):
        pass

    async def call(self, method, path, **kwargs):
        if path == "/usage":
            return {"boot": "test", "sampled": 100, "cards": [], "processes": []}
        if path == "/events":
            await asyncio.sleep(1)
            return {"seq": 0}
        if path in ("/reconcile", "/gpus"):
            return [{"uuid": "gpu-a", "available": False, "conflict": "external process"}]
        raise AssertionError(path)


async def test_agent_repeat_submission_and_access_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr("gpu_platform.api.app.Runner", IdleRunner)
    settings = Settings(tmp_path, tmp_path / "socket", ("gpu-a",), False, os.getuid(),
                        "127.0.0.1", 8765, {"alice": digest("key-a"), "bob": digest("key-b")})
    alice = {"Authorization": "Bearer key-a"}
    bob = {"Authorization": "Bearer key-b"}
    app = create_app(settings)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://workbench"
    ) as client:
        for _ in range(100):
            if (await client.get("/api/health")).is_success:
                break
            await asyncio.sleep(.01)
        assert (await client.get("/api/tasks")).status_code == 401
        assert (await client.get("/api/server-status")).status_code == 401
        app.state.engine.host_gpu_sample = {"sampled": 123, "cards": [
            dict(idx=1, model="B", gpu="gpu-b", util=None, used=3, total=16),
            dict(idx=0, model="A", gpu="gpu-a", util=25, used=4, total=16)]}
        status = (await client.get("/api/server-status", headers=alice)).json()
        assert [(g["index"], g["managed"], g["util"]) for g in status["gpus"]] == [(0, True, 25), (1, False, None)]
        assert [d["mount"] for d in status["disks"]] == ["/d", "/e"]
        body = {"cwd": "/tmp", "argv": ["/bin/true"], "gpu_count": 1}
        assert (await client.post("/api/tasks", json=body)).status_code == 401
        task = (await client.post("/api/tasks", json=body, headers=alice)).json()
        duplicate = (await client.post("/api/tasks", json=body, headers=alice)).json()
        assert duplicate["id"] != task["id"]
        path = "/api/tasks/" + task["id"]
        assert (await client.get(path, headers=bob)).status_code == 404
        assert (await client.post(path + "/cancel", headers=bob)).status_code == 404
        queue = (await client.get("/api/queue", headers=bob)).json()
        assert all(row["id"] is None for row in queue)
        oversized = {**body, "gpu_count": 2}
        response = await client.post("/api/tasks", json=oversized, headers=alice)
        assert response.status_code == 422
        assert (await client.post(path + "/cancel", headers=alice)).json()["state"] == "cancelled"
