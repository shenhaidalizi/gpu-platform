"""Read-only host-wide NVML sampling, independent of device admission and cleanup."""
import asyncio
from pathlib import Path
import pwd
import time

from fastapi import HTTPException
from pydantic import BaseModel

from gpu_platform.workspace.internal import action
from .gpu import process_identity, snapshot


class ProcessProfile(BaseModel):
    user: str
    pid: int
    birth: str
    preferred: str | None


class HostTelemetry:
    def __init__(self):
        self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.previous = {}
        self.last_us = 0
        self.lock = asyncio.Lock()

    async def sample(self, request):
        async with self.lock:
            return await self._sample(request)

    async def _sample(self, request):
        settings = request.app.state.settings
        timestamp = time.time_ns() // 1000
        since = max(timestamp - 10_000_000, self.last_us)
        devices = await asyncio.to_thread(snapshot, since)
        placements = {}
        for dev in devices:
            for pid in dev.pids | dev.graphics:
                placements.setdefault(pid, set()).add(dev.uuid)

        current = {}
        owners = {}
        for pid in placements:
            try:
                uid, birth, run = process_identity(pid)
            except (OSError, ValueError):
                # Some /proc mounts hide cgroups but still expose a stable process identity.
                proc = Path("/proc") / str(pid)
                try:
                    uid = proc.stat().st_uid
                    birth = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
                    run = None
                except (OSError, IndexError):
                    continue
            ident = (pid, birth)
            if ident in self.previous:
                key = self.previous[ident]
            else:
                key = None
                try:
                    user = pwd.getpwuid(uid).pw_name
                    if user in settings.users and not run:
                        result = await action(request, "process_profile", ProcessProfile(
                            user=user, pid=pid, birth=birth, preferred=settings.interpreters.get(user)))
                        key = result.get("key")
                except (KeyError, OSError, HTTPException):
                    pass
            current[ident] = key
            owners[pid] = birth, run, key

        cards, processes = [], []
        for dev in devices:
            clients = dev.pids | dev.graphics
            cards.append(dict(gpu=dev.uuid, model=dev.name, idx=dev.index, total=dev.memory_total_mib,
                              used=dev.memory_used_mib, util=dev.utilization, clients=len(clients)))
            for pid in clients:
                if pid not in owners:
                    continue
                birth, run, key = owners[pid]
                stable = (pid, birth) in self.previous
                solo = len(clients) == 1 and dev.graphics_known and len(placements[pid]) == 1
                sm = dev.process_utilization.get(pid) if stable else None
                # Only attribute a whole-card reading to a single observed client.
                solo_util = dev.utilization if stable and solo else None
                processes.append(dict(gpu=dev.uuid, pid=pid, birth=birth, run=run, key=key,
                                      mib=dev.process_memory_mib.get(pid), sm=sm, solo_util=solo_util,
                                      single_gpu=len(placements[pid]) == 1))
        self.previous, self.last_us = current, timestamp
        return dict(boot=self.boot, sampled=timestamp // 1_000_000, cards=cards, processes=processes)
