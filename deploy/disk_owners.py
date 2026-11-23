"""Read inode metadata and publish one atomic owner summary per filesystem."""
from collections import defaultdict
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile
import time


def account(rows, device):
    totals = defaultdict(int)
    linked = set()
    for line in rows:
        dev, uid, blocks, links, inode, kind = line.split()
        if int(dev) != device:
            continue
        if kind != b"d" and int(links) > 1:
            if inode in linked:
                continue
            linked.add(inode)
        totals[int(uid)] += int(blocks) * 512
    return totals


def owner_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"UID {uid}"


def scan(mount):
    if not os.path.ismount(mount):
        raise OSError(f"{mount} is not a mount point")
    device = os.stat(mount).st_dev
    cmd = ["/usr/bin/find", str(mount), "-xdev", "-ignore_readdir_race"]
    # Also exclude same-device bind mounts to avoid counting their data twice.
    for row in Path("/proc/self/mountinfo").read_text().splitlines():
        nested = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), row.split()[4])
        if nested.startswith(str(mount) + "/"):
            pattern = re.sub(r"([\\*?\[])", r"\\\1", nested)
            cmd.extend(["-path", pattern, "-prune", "-o"])
    cmd.extend(["-printf", r"%D %U %b %n %i %y\n"])
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        totals = account(proc.stdout, device)
        partial = proc.wait() != 0
    if os.stat(mount).st_dev != device:
        raise OSError(f"{mount} changed during scan")
    fs = os.statvfs(mount)
    users = [{"uid": uid, "name": owner_name(uid), "bytes": size}
             for uid, size in sorted(totals.items(), key=lambda item: (-item[1], item[0])) if size]
    return {"device": device, "sampled": int(time.time()), "partial": partial, "users": users,
            "total": fs.f_blocks * fs.f_frsize, "used": (fs.f_blocks - fs.f_bfree) * fs.f_frsize,
            "available": fs.f_bavail * fs.f_frsize, "reserved": (fs.f_bfree - fs.f_bavail) * fs.f_frsize}


def publish(mount, directory):
    result = scan(mount)
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            json.dump(result, stream)
            stream.flush()
            os.fchmod(stream.fileno(), 0o640)
            os.replace(tmp, directory / (mount.name + ".json"))
        finally:
            tmp.unlink(missing_ok=True)
    print(f"{mount}: {len(result['users'])} owners", flush=True)


if __name__ == "__main__":
    directory = Path(os.environ["STATE_DIRECTORY"])
    failed = False
    for mount in (Path("/d"), Path("/e")):
        try:
            publish(mount, directory)
        except OSError as exc:
            print(exc, file=sys.stderr)
            failed = True
    sys.exit(int(failed))
