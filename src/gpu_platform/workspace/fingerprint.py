"""Identify repeatable script invocations without executing or saving user code."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat


def script_command(argv):
    if argv[:2] == ["/bin/bash", "-c"] and len(argv) == 3:
        # Shell expansions, control operators and redirections can change the workload.
        if re.search(r"[^\w./:=+,%@'\x22 -]", argv[2]):
            return None
        try:
            argv = shlex.split(argv[2])
        except ValueError:
            return None
    if not argv or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(argv[0]).name):
        return None
    index = 1
    while index < len(argv) and argv[index] in ("-u", "-B", "-O", "-OO"):
        index += 1
    if index < len(argv) and not argv[index].startswith("-") and argv[index].endswith(".py"):
        return argv, index
    return None


def fingerprint_keys(user, cwd, argv, preferred, live_exe=None):
    command = script_command(argv)
    if not command:
        return None
    args, index = command
    cwd = Path(cwd)
    path = os.defpath + ":/usr/local/bin"
    if preferred:
        path = str(Path(preferred).parent) + ":" + path
    executable = args[0]
    try:
        if "/" in executable:
            launcher = cwd / executable
        else:
            launcher = Path(shutil.which(executable, path=path) or "")
        interpreter = launcher.resolve(strict=True)
        if not interpreter.is_file():
            return None
        if live_exe is not None and not interpreter.samefile(live_exe):
            return None
        identity = interpreter.stat()
        key = [user, str(cwd.resolve()), argv, preferred, str(interpreter),
               identity.st_ino, identity.st_mtime_ns]
        paths = [args[index]]
        for arg in args[index + 1:]:
            value = arg.partition("=")[2] if "=" in arg else arg
            if Path(value).suffix.lower() in (".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".py"):
                paths.append(value)
        if len(paths) > 12:
            return None
        for name in paths:
            fd = os.open(cwd / name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
            with os.fdopen(fd, "rb") as f:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024**2:
                    return None
                data = f.read(2 * 1024**2 + 1)
                if len(data) != info.st_size or os.fstat(fd).st_mtime_ns != info.st_mtime_ns:
                    return None
                key.append(hashlib.sha256(data).hexdigest())
        legacy = hashlib.sha256(json.dumps(key).encode()).hexdigest()
        # Keep the script directory (Python import path), options and workload arguments.
        # Normalize the shell wrapper and the entry filename; content digests follow.
        key[2] = [str(launcher.parent.resolve()), args[1:index], str((cwd / args[index]).resolve().parent), args[index + 1:]]
        key.insert(0, "python-entry-content-v2")
        return {"key": hashlib.sha256(json.dumps(key).encode()).hexdigest(), "legacy": legacy}
    except (OSError, ValueError, RuntimeError):
        return None


def fingerprint(user, cwd, argv, preferred):
    keys = fingerprint_keys(user, cwd, argv, preferred)
    return keys["key"] if keys else None


def process_fingerprint(user, pid, birth, preferred):
    proc = Path("/proc") / str(int(pid))

    def matches():
        return (proc.stat().st_uid == os.getuid()
                and (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] == birth)

    try:
        if not matches():
            return None
        with (proc / "cmdline").open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            return None
        args = raw.rstrip(b"\0").decode().split("\0")
        cwd = (proc / "cwd").readlink()
        keys = fingerprint_keys(user, cwd, args, preferred, live_exe=proc / "exe")
        if keys and matches():
            return keys["key"]
    except (OSError, UnicodeError, ValueError, IndexError):
        pass
    return None
