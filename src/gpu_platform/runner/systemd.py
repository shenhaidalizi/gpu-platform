"""The service manager owns workloads even when the API or runner restarts."""
import asyncio
import base64
import json
import os
import pwd
import re
import stat
from pathlib import Path

from gpu_platform.sharing import accessible, can_fit
from .gpu import snapshot


RUN_ID = re.compile(r"[a-zA-Z0-9_-]{1,80}")
JOB = Path(__file__).with_name("job.py").resolve()


def unit_name(run_id):
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run id")
    return f"gpu-run-{run_id}.service"


async def command(*args):
    child = await asyncio.create_subprocess_exec(*map(str, args),
                                                  stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.PIPE)
    out, err = await child.communicate()
    return child.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def cmd_quote(value):
    # systemd syntax, never shell syntax. Escape specifiers and environment references.
    return json.dumps(str(value).replace("%", "%%").replace("$", "$$"), ensure_ascii=False)


def workspace_properties(user):
    uid = pwd.getpwnam(user).pw_uid
    props = ["TemporaryFileSystem=/d:ro,mode=0111 /e:ro,mode=0111",
             "ProtectHome=yes", "ProtectProc=invisible", "RestrictNamespaces=yes"]
    for disk in (Path("/d"), Path("/e")):
        root = disk / user
        try:
            info = root.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(info.st_mode) and info.st_uid == uid:
            props.append(f"BindPaths={root}")
    return ["--property=" + prop for prop in props]


def unit_gpu_node(minor):
    # Only systemd can traverse this directory. The unit sees its granted device
    # at the usual path, while the host node retains its original permissions.
    directory = Path("/dev/gpu-platform-units")
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise PermissionError("untrusted private device directory")
    name = f"nvidia{minor}"
    host = Path("/dev") / name
    source = host.stat()
    if not stat.S_ISCHR(source.st_mode):
        raise ValueError("assigned GPU has no device node")
    private = directory / name
    try:
        os.mknod(private, stat.S_IFCHR | 0o600, source.st_rdev)
    except FileExistsError:
        pass
    info = private.lstat()
    if not stat.S_ISCHR(info.st_mode) or info.st_uid != 0 or info.st_rdev != source.st_rdev:
        raise PermissionError("invalid private GPU node")
    private.chmod(0o666)
    return f"--property=BindPaths={private}:{host}"


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def describe_holders(card, allocations, known):
    notebooks = {run for run, claim in allocations.items() if claim == "on_demand"}
    holders = {run: claim for run, claim in allocations.items() if run not in notebooks}
    observed = set(card.get("observed_runs", ()))
    card["runs"] = list(holders)
    card["jupyter_runs"] = sorted(notebooks)
    card["jupyter_using_gpu"] = sorted(observed & notebooks)
    card["jupyter_used_mib"] = sum(card.get("used_by_run_mib", {}).get(run, 0) for run in notebooks)
    if allocations.keys() - known or observed - allocations.keys():
        card["conflict"] = "平台运行单元与设备分配尚未对齐"
    if holders:
        card["available"] = False
    return holders


class Units:
    def __init__(self, settings):
        self.settings = settings
        self.manager = [] if settings.system_mode else ["--user"]
        self.root = ((Path("/var/lib/gpu-platform-runner") if settings.system_mode
                      else settings.state_dir / "runner") / "runs").resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_uid != os.geteuid() or self.root.stat().st_mode & 0o022:
            raise PermissionError("runner state must be owned and writable only by runner")

    def files(self, run_id):
        unit_name(run_id)
        return self.root / run_id

    async def inspect(self, run_id):
        keys = "LoadState,ActiveState,SubState,Result,ControlGroup,ExecMainStatus,Job"
        code, out, err = await command("/usr/bin/systemctl", *self.manager, "show",
                                        "--property=" + keys, unit_name(run_id))
        if code:
            raise RuntimeError(err.strip() or "systemd inspection failed")
        state = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        path = self.files(run_id)
        active = state.get("ActiveState")
        if active in ("active", "activating", "reloading") or state.get("Job"):
            return dict(state="running", empty=False)
        if active == "deactivating":
            return dict(state="stopping", empty=False)
        group = state.get("ControlGroup")
        if group:
            events = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
            try:
                if "populated 1" in events.read_text().splitlines():
                    return dict(state="stopping", empty=False)
            except FileNotFoundError:
                pass
        result = path / "result"
        if result.exists():
            status = json.loads(result.read_text())
            code = status.get("EXIT_STATUS", "")
            exit_code = int(code) if code and code.isdecimal() else None
            ok = status.get("SERVICE_RESULT") == "success" and exit_code == 0
            return dict(state="succeeded" if ok else "failed", empty=True,
                        exit_code=exit_code, error=None if ok else
                        f"{status.get('SERVICE_RESULT')}: {code or 'unknown exit'}")
        if active == "failed" or (path / "accepted").exists():
            return dict(state="failed", empty=True, exit_code=None,
                        error=state.get("Result") if active == "failed" else "exit result unavailable")
        return dict(state="missing", empty=True)

    async def active_allocations(self):
        # Read unit receipts even for processes without active CUDA contexts.
        rc, out, err = await command("/usr/bin/systemctl", *self.manager, "list-units",
                                     "--no-legend", "--plain", "--all",
                                     "--state=activating,active,deactivating,reloading",
                                     "gpu-run-*.service")
        if rc:
            raise RuntimeError(err.strip() or "cannot inventory managed units")
        by_gpu = {uuid: {} for uuid in self.settings.gpu_uuids}
        for line in out.splitlines():
            if not line.split():
                continue
            unit = line.split()[0]
            if not unit.startswith("gpu-run-") or not unit.endswith(".service"):
                continue
            run_id = unit[len("gpu-run-"):-len(".service")]
            try:
                receipt = json.loads((self.files(run_id) / "allocation.json").read_text())
                assigned = receipt["gpus"]
                budget = receipt.get("gpu_memory_mib")
                if budget is not None and (type(budget) is not int or budget <= 0 or len(assigned) != 1):
                    raise ValueError("invalid reservation")
                if not isinstance(assigned, list) or not all(isinstance(item, str) for item in assigned):
                    raise ValueError("invalid device list")
                if receipt.get("gpu_on_demand") is True:
                    budget = "on_demand"
            except (OSError, ValueError, KeyError, TypeError):
                # A recognized platform unit with a lost receipt could own any managed GPU.
                assigned = self.settings.gpu_uuids
                budget = None
            for uuid in assigned:
                if uuid in by_gpu:
                    by_gpu[uuid][run_id] = budget
        return by_gpu

    def validate(self, spec):
        user = spec.user
        if user not in self.settings.users:
            raise PermissionError("user is not enrolled")
        uid = pwd.getpwnam(user).pw_uid
        if uid < 1000 or uid == 65534 or (not self.settings.system_mode and uid != os.getuid()):
            raise PermissionError("workload must run as a permitted, unprivileged Linux user")
        if spec.gpu_on_demand and spec.kind != "jupyter":
            raise ValueError("on-demand GPU access requires Jupyter")
        if len(spec.gpus) != spec.gpu_count or len(set(spec.gpus)) != spec.gpu_count:
            raise ValueError("inconsistent GPU allocation")
        if not set(spec.gpus).issubset(self.settings.gpu_uuids):
            raise ValueError("GPU outside managed pool")
        return uid

    async def start(self, run_id, spec, inventory):
        self.validate(spec)
        existing = await self.inspect(run_id)
        if existing["state"] != "missing":
            return existing
        assigned = []
        if spec.gpus:
            held = await self.active_allocations()
            devices = await asyncio.to_thread(snapshot)
            rows = {row["uuid"]: row for row in inventory.view(devices)}
            for uuid in spec.gpus:
                device = next((d for d in devices if d.uuid == uuid), None)
                card = rows[uuid]
                holders = describe_holders(card, held[uuid], inventory.known)
                if spec.gpu_on_demand:
                    fits = accessible(card)
                else:
                    fits = can_fit(card, holders, spec.gpu_memory_mib,
                                   self.settings.shared_limit, self.settings.shared_headroom_mib)
                if not device or not fits:
                    raise BlockingIOError("requested GPU unavailable or insufficient capacity")
                assigned.append(device)
        path = self.files(run_id)
        path.mkdir(mode=0o700, exist_ok=True)
        write_json(path / "allocation.json", {"gpus": list(spec.gpus), "user": spec.user,
                                             "gpu_memory_mib": spec.gpu_memory_mib,
                                             "gpu_on_demand": spec.gpu_on_demand})
        argv = spec.argv
        if spec.kind in ("jupyter", "terminal"):
            from gpu_platform.workspace.session import launch_argv
            argv = launch_argv(spec.kind, run_id, argv, self.settings.jupyter_idle_seconds)
        launch = dict(argv=argv, gpus=spec.gpus, cwd=spec.cwd, executable=spec.argv[0],
                      preferred_python=getattr(self.settings, "interpreters", {}).get(spec.user))
        payload = base64.urlsafe_b64encode(json.dumps(launch).encode()).decode()
        # ExecStopPost runs after worker cleanup, including stops from outside the API.
        # The only elevated command writes three manager-supplied fields to our private file.
        post = ("import os,json,sys; from pathlib import Path; p=Path(sys.argv[1]);"
                "t=p.with_suffix('.tmp');"
                "t.write_text(json.dumps({k:os.getenv(k) for k in "
                "('SERVICE_RESULT','EXIT_CODE','EXIT_STATUS')})); t.replace(p)")
        post_cmd = ("+" if self.settings.system_mode else "") + "/usr/bin/python3 -I -c "
        post_cmd += cmd_quote(post) + " " + cmd_quote(path / "result")
        cmd = ["/usr/bin/systemd-run", *self.manager, "--no-ask-password", "--quiet",
               "--unit=" + unit_name(run_id), "--service-type=exec",
               "--property=KillMode=control-group", "--property=TimeoutStopSec=5s",
               "--property=StandardOutput=append:" + str(path / "output").replace("%", "%%"),
               "--property=StandardError=inherit", "--property=ExecStopPost=" + post_cmd]
        if self.settings.system_mode:
            cmd += ["--uid=" + spec.user, "--property=NoNewPrivileges=yes",
                    "--property=DevicePolicy=closed", *workspace_properties(spec.user)]
            cmd.extend(unit_gpu_node(dev.minor) for dev in assigned)
            if assigned:
                for node in ["/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools",
                             *[f"/dev/nvidia{dev.minor}" for dev in assigned]]:
                    if Path(node).is_char_device():
                        cmd.append(f"--property=DeviceAllow={node} rw")
        cmd += ["--", "/usr/bin/python3", "-I", JOB, payload]
        code, _, err = await command(*cmd)
        if code:
            raise RuntimeError(err.strip() or "failed to launch systemd unit")
        (path / "accepted").touch()
        return await self.inspect(run_id)

    async def stop(self, run_id):
        status = await self.inspect(run_id)
        if status["empty"]:
            return status
        code, _, err = await command("/usr/bin/systemctl", *self.manager,
                                     "stop", "--no-block", unit_name(run_id))
        if code:
            final = await self.inspect(run_id)
            if not final["empty"]:
                raise RuntimeError(err.strip() or "failed to stop unit")
            return final
        return dict(state="stopping", empty=False)

    def logs(self, run_id, offset):
        path = self.files(run_id) / "output"
        try:
            with path.open("rb") as stream:
                stream.seek(offset)
                block = stream.read(256 * 1024)
                return {"text": block.decode(errors="replace"), "next_offset": stream.tell()}
        except FileNotFoundError:
            return {"text": "", "next_offset": offset}
