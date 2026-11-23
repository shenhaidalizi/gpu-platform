import asyncio
import json
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sys


class StdioTests(unittest.TestCase):
    def test_discovery_authenticated_submit_and_wait(self):
        ident = "b" * 32
        calls = Mock()

        class Handler(BaseHTTPRequestHandler):
            def reply(self, obj):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(obj).encode())

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.submit(self.path, self.headers["Authorization"], payload)
                self.reply({"id": ident})

            def do_GET(self):
                if self.path == f"/api/tasks/{ident}/logs?offset=0":
                    self.reply({"text": "CUDA OK", "next_offset": 7})
                elif self.path == f"/api/tasks/{ident}":
                    self.reply({"id": ident, "state": "succeeded", "exit_code": 0})
                else:
                    self.send_error(404)

            def log_message(self, fmt, *args):
                pass

        async def verify(port, token_file):
            params = StdioServerParameters(command=str(Path(sys.executable).with_name("gpu-platform-mcp")),
                env={"GPU_PLATFORM_URL": f"http://127.0.0.1:{port}", "GPU_PLATFORM_TOKEN_FILE": str(token_file)})
            async with stdio_client(params) as pipes, ClientSession(*pipes) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                self.assertEqual(tools, {"submit_gpu_job", "wait_for_task", "get_task", "list_my_tasks",
                                         "read_task_logs", "get_platform_status", "cancel_task"})
                args = {"cwd": "/srv/work", "argv": ["python", "-m", "pytest"], "wait_seconds": 1}
                result = await session.call_tool("submit_gpu_job", args)
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["task"]["exit_code"], 0)
                self.assertEqual(result.structuredContent["logs"]["text"], "CUDA OK")
                self.assertTrue(result.structuredContent["done"])

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as api, tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "test.token"
            token.write_text("example-test-token")
            thread = Thread(target=api.serve_forever, daemon=True)
            thread.start()
            try:
                asyncio.run(verify(api.server_port, token))
            finally:
                api.shutdown()
                thread.join()
        calls.submit.assert_called_once_with("/api/tasks", "Bearer example-test-token",
            {"kind": "batch", "cwd": "/srv/work", "argv": ["python", "-m", "pytest"], "gpu_count": 1})
