"""Unprivileged session host, invoked by systemd under the task owner's UID."""
import argparse
import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import pty
import pwd
import re
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import termios


def launch_argv(kind, run_id, argv, idle_seconds=1800):
    if kind not in {"terminal", "jupyter"}:
        return list(argv)
    return [sys.executable, "-I", str(Path(__file__).resolve()), "--idle-seconds",
            str(idle_seconds), kind, run_id, "--", *argv]


def session_directory(run_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", run_id):
        raise ValueError("无效运行 ID")
    root = Path("/tmp") / f"gpu-ws-{os.getuid()}"
    for directory in (root, root / run_id):
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("会话目录权限不正确")
    return root / run_id


async def terminal(directory, argv):
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.environ["TERM"] = "xterm-256color"
            shell = pwd.getpwuid(os.getuid()).pw_shell or "/bin/bash"
            command = argv or [shell, "-l"]
            os.execvpe(command[0], command, os.environ)
        except OSError as error:
            os.write(2, (str(error) + "\n").encode())
        os._exit(127)
    loop = asyncio.get_running_loop()
    history = bytearray()
    clients = set()
    closed = loop.create_future()
    os.set_blocking(master, False)

    def receive_output():
        try:
            block = os.read(master, 65536)
        except BlockingIOError:
            return
        except OSError:
            block = b""
        if not block:
            loop.remove_reader(master)
            if not closed.done():
                closed.set_result(None)
            return
        history.extend(block)
        del history[:-128 * 1024]
        for client in list(clients):
            if client.transport.get_write_buffer_size() > 256 * 1024:
                client.close()
                clients.discard(client)
            else:
                client.write(block)

    async def accept(reader, writer):
        clients.add(writer)
        try:
            writer.write(history)
            await writer.drain()
            while line := await reader.readline():
                frame = json.loads(line)
                if "resize" in frame:
                    cols, rows = (max(1, min(1000, int(n))) for n in frame["resize"])
                    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                else:
                    data = memoryview(frame["input"].encode())
                    while data:
                        try:
                            count = os.write(master, data)
                            data = data[count:]
                        except BlockingIOError:
                            await asyncio.sleep(.01)
        except (ValueError, KeyError, TypeError, OSError):
            pass
        finally:
            clients.discard(writer)
            writer.close()

    path = directory / "pty.sock"
    def stop_session():
        if not closed.done():
            closed.set_result(None)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGTERM)

    loop.add_signal_handler(signal.SIGTERM, stop_session)
    loop.add_reader(master, receive_output)
    try:
        server = await asyncio.start_unix_server(accept, path=str(path), limit=1024 * 1024)
        async with server:
            await closed
    finally:
        loop.remove_reader(master)
        for client in clients:
            client.close()
        os.close(master)
        path.unlink(missing_ok=True)
    _, status = await asyncio.to_thread(os.waitpid, pid, 0)
    return max(0, os.waitstatus_to_exitcode(status)) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)


def jupyter(directory, argv, idle_seconds):
    # Ask the application to fail if its selected port becomes unavailable.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env = {**os.environ, "JUPYTER_TOKEN": token, "JUPYTER_RUNTIME_DIR": str(directory)}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent / "jupyter_plugin")
    command = argv or ["jupyter-lab"]
    child = subprocess.Popen([
        *command, "--ServerApp.kernel_manager_class=gpu_platform_kernel_manager.IdleKernelManager",
        f"--MappingKernelManager.cull_idle_timeout={idle_seconds}",
        f"--MappingKernelManager.cull_interval={min(15, idle_seconds)}",
        "--MappingKernelManager.cull_busy=False", "--MappingKernelManager.cull_connected=True",
        f"--ServerApp.shutdown_no_activity_timeout={idle_seconds * 2}",
        "--no-browser", "--ServerApp.ip=127.0.0.1", f"--ServerApp.port={port}",
        "--ServerApp.port_retries=0", "--ServerApp.allow_remote_access=False",
        "--ServerApp.base_url=/", "--ServerApp.sock=", "--ServerApp.certfile=", "--ServerApp.keyfile=",
    ], env=env)
    signal.signal(signal.SIGTERM, lambda *_: child.terminate())
    connection = directory / "connection.json"
    try:
        connection.write_text(json.dumps({"pid": child.pid, "port": port, "token": token}))
        return child.wait()
    finally:
        connection.unlink(missing_ok=True)
        (directory / "jupyter_cookie_secret").unlink(missing_ok=True)


def connection_info(run_id):
    directory = session_directory(run_id)
    data = json.loads((directory / "connection.json").read_text())
    # Runtime file appears only after Jupyter has finished binding and starting.
    info = json.loads((directory / f"jpserver-{int(data['pid'])}.json").read_text())
    if info.get("port") != data["port"] or not secrets.compare_digest(info.get("token") or "", data["token"]):
        raise ValueError("Jupyter 认证信息尚未就绪")
    return {"port": data["port"], "token": data["token"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--idle-seconds", type=int, default=1800)
    parser.add_argument("kind", choices=["terminal", "jupyter"])
    parser.add_argument("run_id")
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    directory = session_directory(args.run_id)
    try:
        if args.kind == "terminal":
            result = asyncio.run(terminal(directory, command))
        else:
            result = jupyter(directory, command, args.idle_seconds)
        sys.exit(result)
    finally:
        with contextlib.suppress(OSError):
            directory.rmdir()
