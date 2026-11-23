import fcntl
import os
import pwd
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from gpu_platform.api.auth import current_user
from gpu_platform.api.host_status import HostStatus
from gpu_platform.api.logs import router as logs_router
from gpu_platform.api.transport import Runner
from gpu_platform.config import load
from gpu_platform.models import Submission
from gpu_platform.scheduler.engine import Engine
from gpu_platform.store.db import Store


def create_app(settings=None):
    cfg = settings or load()

    @asynccontextmanager
    async def lifespan(app):
        if os.geteuid() == 0 or cfg.api_uid != os.geteuid():
            raise RuntimeError("API must run as its configured non-root Linux account")
        cfg.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (cfg.state_dir / "scheduler.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            store = await Store.open(cfg.state_dir / "platform.db")
            runner = Runner(cfg.socket)
            engine = Engine(cfg, store, runner)
            app.state.store, app.state.runner, app.state.engine = store, runner, engine
            engine.start()
            try:
                yield
            finally:
                await engine.close()
                await runner.client.aclose()
                await store.db.close()

    app = FastAPI(title="GPU 工作台", version="0.1.0", lifespan=lifespan)
    app.state.settings = cfg
    host_status = HostStatus()

    @app.exception_handler(httpx.HTTPError)
    async def runner_error(request, exc):
        return JSONResponse({"detail": "本机执行器不可用或请求失败"}, status_code=503)

    @app.get("/api/health")
    async def health(request: Request):
        ready = request.app.state.engine.ready
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    @app.get("/api/server-status")
    async def server_status(user: str = Depends(current_user)):
        host = await host_status.read()
        gpu = app.state.engine.host_gpu_sample
        cards = [{"index": card["idx"], "name": card["model"], "util": card["util"],
                  "used_mib": card["used"], "total_mib": card["total"],
                  "managed": card["gpu"] in cfg.gpu_uuids} for card in gpu["cards"]]
        return {**host, "gpu_sampled": gpu["sampled"], "gpus": sorted(cards, key=lambda c: c["index"])}

    @app.get("/api/me")
    async def me(user: str = Depends(current_user)):
        return {"user": user, "home": pwd.getpwnam(user).pw_dir,
                "jupyter_python": cfg.interpreters.get(user), "jupyter_idle_seconds": cfg.jupyter_idle_seconds}

    @app.post("/api/tasks", status_code=201)
    async def submit(spec: Submission, user: str = Depends(current_user)):
        return await app.state.engine.submit(user, spec)

    @app.get("/api/tasks")
    async def tasks(user: str = Depends(current_user)):
        return await app.state.store.rows("user=?", (user,))

    @app.get("/api/tasks/{task_id}")
    async def task(task_id: str, user: str = Depends(current_user)):
        return await app.state.engine.get_owned(task_id, user)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel(task_id: str, user: str = Depends(current_user)):
        return await app.state.engine.cancel(task_id, user)

    @app.get("/api/tasks/{task_id}/logs")
    async def logs(task_id: str, offset: int = Query(default=0, ge=0), user: str = Depends(current_user)):
        item = await app.state.engine.get_owned(task_id, user)
        if not item["run_id"]:
            return {"text": ""}
        return await app.state.runner.call("GET", f"/runs/{item['run_id']}/logs", params={"offset": offset})

    @app.get("/api/gpus")
    async def gpus(user: str = Depends(current_user)):
        engine = app.state.engine
        return [engine.gpu_info(gpu) for gpu in engine.inventory]

    @app.get("/api/queue")
    async def queue(user: str = Depends(current_user)):
        rows = await app.state.store.rows("state='queued' AND gpu_on_demand=0 AND gpu_count>0")
        return [{"position": i, "mine": row["user"] == user, "gpu_count": row["gpu_count"],
                 "id": row["id"] if row["user"] == user else None} for i, row in enumerate(rows, 1)]

    from gpu_platform.workspace.routes import create_router
    app.include_router(create_router())
    app.include_router(logs_router)
    dist = Path(os.environ.get("GPU_PLATFORM_WEB_DIST", Path(__file__).resolve().parents[3] / "web/dist"))
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="workbench")
    return app
