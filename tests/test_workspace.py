import asyncio
import json
import os
from pathlib import Path
import pwd
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from gpu_platform.workspace.internal import user_process
from gpu_platform.workspace.session import launch_argv, session_directory
from gpu_platform.workspace.user_io import read_path, write_path


def test_file_editor_uses_actual_directory_and_preserves_mode(tmp_path):
    path = tmp_path / "train.py"
    write_path(path, "print('训练')\n")
    path.chmod(0o640)
    write_path(path, "print(42)\n")
    assert read_path(path) == {"text": "print(42)\n"}
    assert read_path(tmp_path)["entries"] == [{"name": "train.py", "path": str(path), "is_dir": False}]
    assert path.stat().st_mode & 0o777 == 0o640


def test_refuses_special_files(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        read_path(fifo)
    with pytest.raises(ValueError):
        write_path(fifo, "x")


def test_session_id_is_not_a_path():
    with pytest.raises(ValueError):
        session_directory("../private")


@pytest.mark.asyncio
async def test_file_helper_checks_identity(tmp_path):
    user = pwd.getpwuid(os.getuid()).pw_name
    settings = SimpleNamespace(users={user: "", "root": ""}, system_mode=False)
    with pytest.raises(HTTPException):
        await user_process(settings, "root", "read")
    if os.getuid() == 0:
        pytest.skip("requires an ordinary test account")
    p = await user_process(settings, user, "read")
    output, _ = await p.communicate(json.dumps({"path": str(tmp_path)}).encode())
    assert p.returncode == 0 and json.loads(output) == {"entries": []}


@pytest.mark.asyncio
async def test_pty_reconnects_without_restarting_the_shell(tmp_path):
    key = uuid4().hex
    path = session_directory(key) / "pty.sock"
    process = await asyncio.create_subprocess_exec(
        *launch_argv("terminal", key, ["/bin/sh"]), cwd=tmp_path)
    try:
        for _ in range(100):
            if path.is_socket():
                break
            await asyncio.sleep(.025)
        reader, writer = await asyncio.open_unix_connection(path)
        writer.write(b'{"input":"export GPU_TEST=first; echo ok\\n"}\n')
        await writer.drain()
        await asyncio.wait_for(reader.readuntil(b"ok\r\n"), 2)
        writer.close()
        await writer.wait_closed()

        reader, writer = await asyncio.open_unix_connection(path)
        writer.write(b'{"input":"echo second-$GPU_TEST\\n"}\n')
        await writer.drain()
        await asyncio.wait_for(reader.readuntil(b"second-first"), 2)
        writer.write(b'{"input":"exit\\n"}\n')
        await writer.drain()
        assert await asyncio.wait_for(process.wait(), 3) == 0
        writer.close()
        await writer.wait_closed()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
