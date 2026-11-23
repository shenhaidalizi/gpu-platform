"""Runner API, reachable only over the private local Unix socket."""
import asyncio
from contextlib import asynccontextmanager, suppress
import fcntl
import logging
import os
import pwd
from pathlib import Path
import socket
import struct
import time

from dbus_next import BusType, Message, MessageType
from dbus_next.aio import MessageBus
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
import uvicorn

from gpu_platform.config import config_path, load
from gpu_platform.models import Workload
from .gpu import GPUInventory, snapshot
from .systemd import Units, describe_holders, unit_name
from .telemetry import HostTelemetry


class Start(Workload):
    gpu_memory_mib: int | None = Field(default=None, gt=0, le=2**31-1, strict=True)
    gpu_on_demand: bool = False
    user: str
    gpus: list[str]

    @model_validator(mode="after")
    def single_card_budget(self):
        if self.gpu_memory_mib is not None and self.gpu_count != 1:
            raise ValueError("共享预算仅支持单卡")
        return self


class Reconcile(BaseModel):
    runs: list[str]


class Events:
    def __init__(self):
        self.seq = time.time_ns() // 1000
        self.event = asyncio.Event()

    def notify(self):
        self.seq += 1
        self.event.set()
        self.event = asyncio.Event()

    async def watch_systemd(self, system_mode):
        while True:
            bus = None
            try:
                bus = await MessageBus(bus_type=BusType.SYSTEM if system_mode else BusType.SESSION).connect()
                rule = ("type='signal',sender='org.freedesktop.systemd1',"
                        "path_namespace='/org/freedesktop/systemd1/unit'")
                match = await bus.call(Message(destination="org.freedesktop.DBus",
                              path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
                              member="AddMatch", signature="s", body=[rule]))
                if match.message_type == MessageType.ERROR:
                    raise RuntimeError(match.body)
                subscribe = await bus.call(Message(destination="org.freedesktop.systemd1",
                                path="/org/freedesktop/systemd1",
                                interface="org.freedesktop.systemd1.Manager", member="Subscribe"))
                if subscribe.message_type == MessageType.ERROR:
                    raise RuntimeError(subscribe.body)

                def handle(message):
                    if ((message.path or "").startswith("/org/freedesktop/systemd1/unit/gpu_2drun_2d")
                            and message.member == "PropertiesChanged"):
                        self.notify()
                bus.add_message_handler(handle)
                await bus.wait_for_disconnect()
            except (OSError, RuntimeError, EOFError) as exc:
                logging.warning("systemd subscription unavailable: %s", exc)
            finally:
                if bus is not None:
                    bus.disconnect()
            await asyncio.sleep(2)


def build_app(settings):
    units = Units(settings)
    gpu = GPUInventory(settings.gpu_uuids)
    telemetry = HostTelemetry()
    events = Events()
    mutations = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        watcher = asyncio.create_task(events.watch_systemd(settings.system_mode))
        try:
            yield
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    from gpu_platform.workspace.internal import router
    app.include_router(router)

    def check(run_id):
        try:
            unit_name(run_id)
        except ValueError as exc:
            raise HTTPException(422, "invalid run id") from exc

    @app.post("/runs/{run_id}/start")
    async def start(run_id: str, spec: Start):
        check(run_id)
        async with mutations:
            try:
                result = await units.start(run_id, spec, gpu)
            except BlockingIOError as exc:
                raise HTTPException(409, str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except (ValueError, KeyError) as exc:
                raise HTTPException(422, str(exc)) from exc
            gpu.known.add(run_id)
            events.notify()
            return result

    @app.get("/runs/{run_id}")
    async def inspect(run_id: str):
        check(run_id)
        # An API restart can race an already accepted start or cancellation.
        # Inspect only after the runner has resolved prior operations.
        async with mutations:
            return await units.inspect(run_id)

    @app.post("/runs/{run_id}/stop")
    async def stop(run_id: str):
        check(run_id)
        async with mutations:
            result = await units.stop(run_id)
            events.notify()
            return result

    @app.get("/runs/{run_id}/logs")
    async def logs(run_id: str, offset: int = Query(default=0, ge=0)):
        check(run_id)
        return await asyncio.to_thread(units.logs, run_id, offset)

    async def add_allocations(rows):
        held = await units.active_allocations()
        for row in rows:
            describe_holders(row, held[row["uuid"]], gpu.known)
        return rows

    @app.get("/usage")
    async def host_usage(request: Request):
        return await telemetry.sample(request)

    @app.get("/gpus")
    async def inventory():
        return await add_allocations(gpu.view(await asyncio.to_thread(snapshot)))

    @app.post("/reconcile")
    async def reconcile(spec: Reconcile):
        for run_id in spec.runs:
            check(run_id)
        async with mutations:
            return await add_allocations(await asyncio.to_thread(gpu.reconcile, spec.runs))

    @app.get("/events")
    async def poll(after: int = 0):
        if after == events.seq:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(events.event.wait(), timeout=2)
        return {"seq": events.seq}

    return app


class LocalSocket(socket.socket):
    """Reject clients with a different kernel-verified Unix UID, including WebSockets."""
    api_uid: int

    def accept(self):
        while True:
            conn, address = super().accept()
            credentials = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _, uid, _ = struct.unpack("3i", credentials)
            if uid == self.api_uid:
                return conn, address
            conn.close()


def ensure_safe_system_install(settings):
    if settings.api_uid == 0:
        raise RuntimeError("API must not run as root")
    if settings.system_mode != (os.geteuid() == 0):
        raise RuntimeError("system mode requires root runner; user mode forbids root")
    if not settings.system_mode and settings.api_uid != os.getuid():
        raise RuntimeError("user-mode API and runner must share a UID")
    if settings.system_mode:
        if any(pwd.getpwnam(name).pw_uid == settings.api_uid for name in settings.users):
            raise RuntimeError("system mode requires a dedicated API user outside the enrolled users")
        import sys
        for path in (Path(__file__), config_path(), Path(sys.prefix), settings.socket.parent):
            for part in (path.resolve(), *path.resolve().parents):
                if not part.exists() and part == settings.socket.parent:
                    continue
                if part.stat().st_uid != 0 or part.stat().st_mode & 0o022:
                    raise RuntimeError(f"privileged runner requires root-controlled path: {part}")


def main():
    settings = load()
    ensure_safe_system_install(settings)
    path = settings.socket
    try:
        path.parent.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    else:
        if settings.system_mode:
            # This directory was just created by us; do not chmod existing system paths.
            path.parent.chmod(0o711)
    if path.parent.stat().st_uid != os.geteuid() or path.parent.stat().st_mode & 0o022:
        raise RuntimeError("untrusted runner socket directory")
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a") as lock, LocalSocket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.api_uid = settings.api_uid
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path.unlink(missing_ok=True)
        try:
            previous_umask = os.umask(0o177)
            try:
                sock.bind(str(path))
            finally:
                os.umask(previous_umask)
            os.chmod(path, 0o600)
            if settings.system_mode:
                os.chown(path, settings.api_uid, -1)
            sock.listen(128)
            options = uvicorn.Config(build_app(settings), access_log=False)
            asyncio.run(uvicorn.Server(options).serve(sockets=[sock]))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
