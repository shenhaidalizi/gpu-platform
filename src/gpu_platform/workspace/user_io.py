"""Short-lived file/attach utility. The runner drops credentials before exec."""
import asyncio
from contextlib import contextmanager
import pwd
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

if __package__:
    from .fingerprint import fingerprint_keys, process_fingerprint
    from .session import connection_info, session_directory
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fingerprint import fingerprint_keys, process_fingerprint
    from session import connection_info, session_directory

MAX_TEXT_BYTES = 2 * 1024 * 1024


def read_path(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("需要绝对路径")
    if path.is_dir():
        entries = []
        with os.scandir(path) as listing:
            for item in listing:
                if len(entries) == 10000:
                    raise ValueError("目录条目超过 10000")
                try:
                    is_dir = item.is_dir()
                except OSError:
                    is_dir = False
                entries.append({"name": item.name, "path": item.path, "is_dir": is_dir})
        entries.sort(key=lambda row: (not row["is_dir"], row["name"].casefold()))
        return {"entries": entries}
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("编辑器只打开普通文本文件")
        data = stream.read(MAX_TEXT_BYTES + 1)
        if len(data) > MAX_TEXT_BYTES or b"\0" in data:
            raise ValueError("仅支持不超过 2 MiB 的 UTF-8 文本")
        return {"text": data.decode("utf-8")}


def write_path(path, text, *, target=None):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("需要绝对路径")
    if target is None:
        target = path.resolve()
    data = text.encode()
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError("编辑器仅支持不超过 2 MiB 的文件")
    try:
        info = target.stat()
    except FileNotFoundError:
        info = None
    if info:
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("仅允许保存普通文本文件")
        if not os.access(target, os.W_OK):
            raise PermissionError("文件不可写")
        # A rename would discard these inode attributes or break links.
        inplace = (info.st_uid != os.getuid() or info.st_nlink > 1 or os.listxattr(target)
                   or not os.access(target.parent, os.W_OK))
        if inplace:
            flags = os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_NOCTTY
            with os.fdopen(os.open(target, flags), "wb") as stream:
                current = os.fstat(stream.fileno())
                if not stat.S_ISREG(current.st_mode) or not os.path.samestat(current, info):
                    raise ValueError("文件在保存时已发生变化，请重新打开")
                stream.write(data)
                stream.truncate(len(data))
                stream.flush()
                os.fsync(stream.fileno())
            return {"path": str(path)}
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".gpu-editor-", delete=False) as tmp:
        temp_path = Path(tmp.name)
        try:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            if info:
                os.fchown(tmp.fileno(), -1, info.st_gid)
                os.fchmod(tmp.fileno(), stat.S_IMODE(info.st_mode) & 0o777)
            os.replace(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)
    return {"path": str(path)}


@contextmanager
def workspace_anchor(path, writing):
    """Pin resolved components beneath the authenticated user's disk roots."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("需要绝对路径")
    user = pwd.getpwuid(os.getuid()).pw_name
    roots = [disk / user for disk in (Path("/d"), Path("/e"))]
    if ".ssh" in path.parts or ".ssh" in path.resolve().parts:
        raise PermissionError("认证目录不对网页编辑器开放")
    if not any(path.is_relative_to(root) for root in roots):
        raise PermissionError("超出个人工作区")
    resolved = path.resolve()
    root = next((r for r in roots if resolved.is_relative_to(r)), None)
    if root is None:
        raise PermissionError("超出个人工作区")
    flags = os.O_PATH | os.O_NOFOLLOW
    fd = os.open(root, flags | os.O_DIRECTORY)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise PermissionError("工作区归属不匹配")
        if writing and resolved == root:
            raise PermissionError("不能替换工作区根目录")
        last = resolved.parent if writing else resolved
        for part in last.relative_to(root).parts:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            if stat.S_ISLNK(os.fstat(fd).st_mode):
                raise PermissionError("路径已改变")
        pinned = Path(f"/proc/self/fd/{fd}")
        yield pinned / resolved.name if writing else pinned
    finally:
        os.close(fd)


def scoped_file(operation, body):
    path = Path(body["path"])
    writing = operation == "scoped_write"
    with workspace_anchor(path, writing) as pinned:
        if writing:
            return write_path(path, body["text"], target=pinned)
        result = read_path(pinned)
        if "entries" in result:
            result["entries"] = [entry for entry in result["entries"] if entry["name"] != ".ssh"]
        for entry in result.get("entries", []):
            entry["path"] = str(path / entry["name"])
        return result


async def relay(run_id):
    # This entire helper runs as the task owner, including the socket connect.
    reader, writer = await asyncio.open_unix_connection(str(session_directory(run_id) / "pty.sock"))
    loop = asyncio.get_running_loop()
    stdin = asyncio.StreamReader(limit=1024 * 1024)
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin.buffer)

    async def send():
        while chunk := await stdin.read(65536):
            writer.write(chunk)
            await writer.drain()

    async def receive():
        while block := await reader.read(65536):
            sys.stdout.buffer.write(block)
            sys.stdout.buffer.flush()

    flows = [asyncio.create_task(send()), asyncio.create_task(receive())]
    try:
        await asyncio.wait(flows, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for flow in flows:
            flow.cancel()
        await asyncio.gather(*flows, return_exceptions=True)
        transport.close()
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    if os.geteuid() == 0:
        sys.exit("workspace helper refuses root")
    if sys.argv[1] == "attach":
        asyncio.run(relay(sys.argv[2]))
    else:
        try:
            body = json.load(sys.stdin)
            if sys.argv[1] in {"read", "write"} and ".ssh" in Path(body["path"]).resolve().parts:
                raise PermissionError("认证目录不对网页编辑器开放")
            if sys.argv[1] in {"scoped_read", "scoped_write"}:
                result = scoped_file(sys.argv[1], body)
            elif sys.argv[1] == "read":
                result = read_path(body["path"])
            elif sys.argv[1] == "profile":
                result = fingerprint_keys(**body) or {"key": None}
            elif sys.argv[1] == "process_profile":
                result = {"key": process_fingerprint(**body)}
            elif sys.argv[1] == "write":
                result = write_path(body["path"], body["text"])
            elif sys.argv[1] == "connect":
                result = connection_info(body["run_id"])
            else:
                raise ValueError("未知操作")
            print(json.dumps(result, ensure_ascii=False))
        except (ValueError, KeyError, OSError) as error:
            if isinstance(error, FileNotFoundError):
                status = 404
            elif isinstance(error, PermissionError):
                status = 403
            else:
                status = 400
            print(json.dumps({"detail": str(error), "status": status}, ensure_ascii=False))
            sys.exit(1)
