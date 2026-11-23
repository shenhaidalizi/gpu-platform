"""Stage GPU platform credentials for existing Linux accounts. Run as the platform operator."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile


# This code runs as the account receiving the credential, including on retries.
ISSUE = """
import hashlib, os, secrets, stat, sys
from pathlib import Path
directory = Path(sys.argv[1]) / ".config" / "gpu-platform"
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o022:
    raise ValueError("untrusted credential directory")
path = directory / "access.token"
try:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
except FileExistsError:
    pass
else:
    with os.fdopen(fd, "w") as out:
        out.write(secrets.token_urlsafe(32) + "\\n")
with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)) as f:
    info = os.fstat(f.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("credential must be a private regular file owned by user")
    raw = f.read(1025).strip()
if not 1 <= len(raw) <= 1024 or not all(33 <= ord(c) <= 126 for c in raw):
    raise ValueError("invalid existing credential")
print(hashlib.sha256(raw.encode()).hexdigest())
"""


def accounts():
    excluded = set(Path(__file__).with_name("inactive_users.txt").read_text().split())
    homes = {}
    for disk in (Path("/d"), Path("/e")):
        for path in sorted(disk.iterdir()):
            if path.name in excluded:
                continue
            try:
                user = pwd.getpwnam(path.name)
            except KeyError:
                continue
            info = path.lstat()
            if (stat.S_ISDIR(info.st_mode) and info.st_uid == user.pw_uid
                    and 1000 <= user.pw_uid < 65534 and user.pw_shell.split("/")[-1] not in {"false", "nologin"}):
                if user.pw_name not in homes or path == Path(user.pw_dir):
                    homes[user.pw_name] = path
    return homes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, help="issue personal credentials and save a TOML users table here")
    args = parser.parse_args()
    homes = accounts()
    if not args.stage:
        for name, home in sorted(homes.items()):
            print(name, home)
        return
    if os.getuid() == 0:
        parser.error("run as the platform operator with sudo access")
    entries = {}
    for name, home in sorted(homes.items()):
        command = ["/usr/bin/python3", "-I", "-c", ISSUE, str(home)]
        if pwd.getpwnam(name).pw_uid != os.getuid():
            command = ["sudo", "-n", "-H", "-u", name, "--", *command]
        value = subprocess.check_output(command, cwd="/", text=True).strip()
        if len(value) != hashlib.sha256().digest_size * 2 or not all(c in "0123456789abcdef" for c in value):
            raise ValueError(f"invalid credential digest for {name}")
        if value in entries.values():
            raise ValueError(f"duplicate credential for {name}")
        entries[name] = value
    with tempfile.NamedTemporaryFile(dir=args.stage.parent, mode="w", delete=False) as out:
        out.write("[users]\n")
        for name, value in entries.items():
            out.write(f"{json.dumps(name)} = {json.dumps(value)}\n")
    os.replace(out.name, args.stage)
    print(f"Staged {len(entries)} accounts in {args.stage}")


if __name__ == "__main__":
    main()
