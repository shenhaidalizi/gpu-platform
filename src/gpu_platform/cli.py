import argparse
import json
import os
import pwd
import secrets
import socket
from contextlib import ExitStack
from pathlib import Path

import uvicorn

from gpu_platform.config import GPU7, config_path, digest, load


def initialize(path, state, port):
    name = pwd.getpwuid(os.getuid()).pw_name
    if os.getuid() == 0:
        raise SystemExit("以普通 Linux 用户初始化")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    socket = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "gpu-platform" / "runner.sock"
    content = (f"state_dir = {json.dumps(str(state))}\nsocket = {json.dumps(str(socket))}\n"
               f"gpu_uuids = [{json.dumps(GPU7)}]\napi_uid = {os.getuid()}\n"
               f'host = "127.0.0.1"\nport = {port}\nsystem_mode = false\n\n'
               f"[users]\n{json.dumps(name)} = {json.dumps(digest(token))}\n")
    default_python = Path(pwd.getpwuid(os.getuid()).pw_dir) / "anaconda3/bin/python"
    if default_python.is_file():
        content += f"\n[interpreters]\n{json.dumps(name)} = {json.dumps(str(default_python))}\n"
    token_path = path.with_name("access.token")
    if path.exists() or token_path.exists():
        raise SystemExit("配置或凭据已存在，保留现有文件")
    def private_open(name, flags):
        return os.open(name, flags, 0o600)
    with open(token_path, "x", opener=private_open) as out:
        out.write(token + "\n")
    with open(path, "x", opener=private_open) as out:
        out.write(content)
    print(f"配置 {path}\n登录凭据 {token_path}（仅本人可读）")


def main():
    parser = argparse.ArgumentParser(prog="gpu-platform")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/gpu-platform")
    init.add_argument("--port", type=int, default=8765)
    commands.add_parser("serve")
    args = parser.parse_args()
    if args.command == "init":
        initialize(config_path(), args.state_dir.resolve(), args.port)
    else:
        from gpu_platform.api.app import create_app
        cfg = load()
        app = create_app(cfg)
        if cfg.lan_host:
            config = uvicorn.Config(app, host=cfg.host, port=cfg.port, access_log=False)
            with ExitStack() as stack:
                listeners = []
                for host in dict.fromkeys((cfg.host, cfg.lan_host)):
                    family = socket.AF_INET6 if ":" in host else socket.AF_INET
                    listener = socket.create_server((host, cfg.port), family=family)
                    listeners.append(stack.enter_context(listener))
                server = uvicorn.Server(config)
                server.run(sockets=listeners)
                if not server.started:
                    raise SystemExit(1)
        else:
            uvicorn.run(app, host=cfg.host, port=cfg.port, access_log=False)
