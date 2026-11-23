"""On-demand read-only metrics for this Linux host."""
import asyncio
import json
import os
from pathlib import Path
import socket
import time


def cpu_ticks():
    with open("/proc/stat") as stream:
        # The last two fields (guest, guest_nice) are already included in user/nice.
        ticks = [int(value) for value in stream.readline().split()[1:9]]
    return sum(ticks), ticks[3] + ticks[4]


def memory_usage():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, _, rest = line.partition(":")
        if name in ("MemTotal", "MemAvailable"):
            values[name] = int(rest.split()[0]) * 1024
    total = values["MemTotal"]
    available = values["MemAvailable"]
    return {"total": total, "used": total - available, "available": available}


def disk_usage(mount):
    if not os.path.ismount(mount):
        return {"mount": mount, "error": "未挂载"}
    try:
        fs = os.statvfs(mount)
    except OSError:
        return {"mount": mount, "error": "读取失败"}
    return {"mount": mount, "total": fs.f_blocks * fs.f_frsize,
            "used": (fs.f_blocks - fs.f_bfree) * fs.f_frsize,
            "available": fs.f_bavail * fs.f_frsize,
            "reserved": (fs.f_bfree - fs.f_bavail) * fs.f_frsize}


def disk_with_owners(mount, directory=Path("/var/lib/gpu-platform-disk-usage")):
    disk = disk_usage(mount)
    if "error" in disk:
        return disk
    disk["ownership"] = None
    try:
        summary = json.loads((directory / (Path(mount).name + ".json")).read_text())
        if summary["device"] == os.stat(mount).st_dev:
            disk["ownership"] = summary
    except (OSError, ValueError, KeyError):
        pass
    return disk


def read_host():
    total, idle = cpu_ticks()
    time.sleep(0.25)
    end, end_idle = cpu_ticks()
    elapsed = end - total
    busy = 100 * (elapsed - (end_idle - idle)) / elapsed if elapsed > 0 else None
    return {"hostname": socket.gethostname(), "sampled": int(time.time()),
            "cpu": {"percent": round(max(0, min(100, busy)), 1) if busy is not None else None,
                    "logical": os.cpu_count()},
            "memory": memory_usage(), "disks": [disk_with_owners(mount) for mount in ("/d", "/e")]}


class HostStatus:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.cached = None
        self.updated = 0

    async def read(self):
        async with self.lock:
            if self.cached is None or time.monotonic() - self.updated >= 2:
                self.cached = await asyncio.to_thread(read_host)
                self.updated = time.monotonic()
            return self.cached
