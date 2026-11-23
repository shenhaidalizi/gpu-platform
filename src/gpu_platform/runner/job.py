"""Exec a validated command as the unit's Linux user. No shell interpolation."""
import base64
import json
import os
import pwd
import sys


def main():
    if os.getuid() == 0:
        raise SystemExit("refusing to start a root workload")
    spec = json.loads(base64.urlsafe_b64decode(sys.argv[1]))
    os.chdir(spec["cwd"])
    account = pwd.getpwuid(os.getuid())
    argv = spec["argv"]
    path = os.defpath + ":/usr/local/bin"
    preferred = spec.get("preferred_python")
    if preferred:
        path = os.path.dirname(preferred) + ":" + path
    explicit = spec["executable"]
    if os.path.isabs(explicit) and (os.path.basename(explicit).startswith("python")
                                     or os.path.basename(explicit) in ("torchrun", "jupyter-lab")):
        path = os.path.dirname(explicit) + ":" + path
    env = dict(PATH=path, HOME=account.pw_dir, USER=account.pw_name,
               LOGNAME=account.pw_name, SHELL=account.pw_shell or "/bin/bash",
               LANG="C.UTF-8", TERM="xterm-256color", PYTHONUNBUFFERED="1",
               CUDA_VISIBLE_DEVICES=",".join(spec["gpus"]))
    runtime = "/run/user/" + str(os.getuid())
    if os.path.isdir(runtime):
        env["XDG_RUNTIME_DIR"] = runtime
    os.execvpe(argv[0], argv, env)


if __name__ == "__main__":
    main()
