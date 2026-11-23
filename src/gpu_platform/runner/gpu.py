"""Inventory and conservative cleanup of NVIDIA compute contexts on this host."""
import os
import re
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

import pynvml as nv


@dataclass
class Device:
    uuid: str
    index: int
    name: str
    minor: int
    memory_total_mib: int
    memory_used_mib: int
    pids: set[int]
    graphics: set[int] = field(default_factory=set)
    graphics_known: bool = True
    process_memory_mib: dict[int, int] = field(default_factory=dict)
    utilization: int | None = None
    process_utilization: dict[int, int] = field(default_factory=dict)


def decoded(value):
    return value.decode() if isinstance(value, bytes) else value


def snapshot(since_us=None) -> list[Device]:
    """All GPUs must be observable to determine whether a PID spans cards."""
    nv.nvmlInit()
    try:
        devices = []
        for index in range(nv.nvmlDeviceGetCount()):
            handle = nv.nvmlDeviceGetHandleByIndex(index)
            used = nv.nvmlDeviceGetComputeRunningProcesses(handle)
            try:
                graphics = {p.pid for p in nv.nvmlDeviceGetGraphicsRunningProcesses(handle)}
                graphics_known = True
            except nv.NVMLError:
                graphics, graphics_known = set(), False
            memory = nv.nvmlDeviceGetMemoryInfo(handle)
            try:
                utilization = nv.nvmlDeviceGetUtilizationRates(handle).gpu
            except nv.NVMLError:
                utilization = None
            process_util = {}
            if since_us is not None:
                try:
                    for sample in nv.nvmlDeviceGetProcessUtilization(handle, since_us):
                        if sample.timeStamp > since_us and 0 <= sample.smUtil <= 100:
                            process_util[sample.pid] = max(process_util.get(sample.pid, 0), sample.smUtil)
                except nv.NVMLError:
                    pass
            devices.append(Device(decoded(nv.nvmlDeviceGetUUID(handle)), index,
                                  decoded(nv.nvmlDeviceGetName(handle)),
                                  nv.nvmlDeviceGetMinorNumber(handle),
                                  memory.total // 1048576, memory.used // 1048576,
                                  {item.pid for item in used}, graphics, graphics_known,
                                  {p.pid: p.usedGpuMemory // 1048576 for p in used
                                   if isinstance(p.usedGpuMemory, int) and 0 <= p.usedGpuMemory <= memory.total}, utilization, process_util))
        return devices
    finally:
        nv.nvmlShutdown()


UNIT_PATTERN = re.compile(r"gpu-run-([a-zA-Z0-9_-]{1,80})\.service")


def process_identity(pid):
    process = Path("/proc") / str(pid)
    cgroup = (process / "cgroup").read_text().splitlines()
    unified = next((line[3:] for line in cgroup if line.startswith("0::")), None)
    if not unified:
        raise ValueError("cannot establish process cgroup")
    # Use the complete component; a worker can have a sub-cgroup of its own.
    run = next((m[1] for part in Path(unified).parts
                if (m := UNIT_PATTERN.fullmatch(part))), None)
    start_ticks = (process / "stat").read_text().rsplit(")", 1)[1].split()[19]
    return process.stat().st_uid, start_ticks, run


class GPUInventory:
    def __init__(self, uuids):
        self.managed = tuple(uuids)
        self.known = set()
        self.terminating = {}

    @staticmethod
    def placements(devices):
        result = {}
        for device in devices:
            for pid in device.pids | device.graphics:
                result.setdefault(pid, set()).add(device.uuid)
        return result

    def view(self, devices):
        placements = self.placements(devices)
        rows = []
        for uuid in self.managed:
            device = next((d for d in devices if d.uuid == uuid), None)
            if device is None:
                rows.append(dict(uuid=uuid, index=None, name="", conflict="设备未发现",
                                 external_pids=[], pids=[], available=False))
                continue
            external, conflicts = [], []
            observed_runs = set()
            used_by_run = {}
            for pid in sorted(device.pids | device.graphics):
                try:
                    uid, _, run = process_identity(pid)
                except (OSError, ValueError):
                    external.append(pid)
                    conflicts.append(f"进程 {pid} 归属待确认")
                    continue
                if run and run in self.known:
                    observed_runs.add(run)
                    used_by_run[run] = used_by_run.get(run, 0) + device.process_memory_mib.get(pid, 0)
                    continue
                external.append(pid)
                if run or len(placements[pid]) > 1 or uid < 1000 or uid == 65534:
                    conflicts.append(f"进程 {pid} 跨卡、受保护或归属待确认")
            rows.append(dict(uuid=uuid, index=device.index, name=device.name,
                             memory_total_mib=device.memory_total_mib,
                             memory_used_mib=device.memory_used_mib,
                             pids=sorted(device.pids), external_pids=external, observed_runs=sorted(observed_runs),
                             used_by_run_mib=used_by_run, utilization=device.utilization,
                             metering_complete=device.graphics_known and (device.pids | device.graphics) <= device.process_memory_mib.keys(),
                             conflict="；".join(conflicts), available=not (device.pids or device.graphics)))
        return rows

    def reconcile(self, runs):
        self.known = set(runs)
        initial = snapshot()
        placements = self.placements(initial)
        live = set()
        compute = {pid for d in initial for pid in d.pids}
        # Only signal compute processes when every card can be inspected for
        # both graphics and compute clients. An unrelated graphics context matters too.
        if not all(d.graphics_known for d in initial):
            return self.view(initial)
        for pid, location in placements.items():
            if pid not in compute or len(location) != 1 or not location.issubset(self.managed):
                continue
            try:
                # Pin the process before re-reading identity and cross-card placement.
                fd = os.pidfd_open(pid)
            except (ProcessLookupError, PermissionError):
                continue
            try:
                uid, birth, run = process_identity(pid)
                if run or uid < 1000 or uid == 65534:
                    continue
                current = snapshot()
                if (not all(d.graphics_known for d in current)
                        or self.placements(current).get(pid) != location
                        or not any(pid in d.pids for d in current)):
                    continue
                if process_identity(pid) != (uid, birth, run):
                    continue
                key = (pid, birth)
                live.add(key)
                now = time.monotonic()
                since = self.terminating.get(key)
                if since is None:
                    signal.pidfd_send_signal(fd, signal.SIGTERM)
                    self.terminating[key] = now
                elif now - since > 5:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
            except (OSError, ValueError):
                # A disappearing or inaccessible process must never be retargeted.
                continue
            finally:
                os.close(fd)
        self.terminating = {k: v for k, v in self.terminating.items() if k in live}
        return self.view(snapshot())
