import contextlib
import io
import json
import unittest
from unittest.mock import patch

import httpx
from gpu_platform_client import cli
from gpu_platform_client.client import PlatformError

TASK_ID = "a" * 32


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, args, handler):
        with patch.object(cli, "connect", return_value=httpx.AsyncClient(
            base_url="http://test/api/", transport=httpx.MockTransport(handler)
        )), contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            code = await cli.run(cli.parser().parse_args(args))
        return code, json.loads(out.getvalue())

    async def test_logs_display_physical_devices_in_assignment_order(self):
        def handle(req):
            if req.url.path == "/api/gpus":
                return httpx.Response(200, json=[{"uuid": "gpu-a", "index": 2}, {"uuid": "gpu-b", "index": 7}])
            if req.url.path.endswith("/logs"):
                return httpx.Response(200, json={"text": "cuda:0\n", "next_offset": 7})
            return httpx.Response(200, json={"gpus": ["gpu-b", "gpu-a"]})

        with patch.object(cli, "connect", return_value=httpx.AsyncClient(
            base_url="http://test/api/", transport=httpx.MockTransport(handle)
        )), contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(await cli.run(cli.parser().parse_args(["logs", TASK_ID])), 0)
        self.assertEqual(out.getvalue(), "cuda:0\n")
        self.assertEqual(err.getvalue().splitlines(), ["物理 GPU 7（任务内 cuda:0）", "物理 GPU 2（任务内 cuda:1）"])

    async def test_failed_smoke_preserves_argv_exit_and_logs(self):
        def handle(req):
            if req.method == "POST":
                spec = json.loads(req.content)
                self.assertEqual(spec, dict(kind="batch", cwd="/server/test", argv=["python", "a b.py", "-q"], gpu_count=1))
                return httpx.Response(201, json={"id": TASK_ID})
            if req.url.path.endswith("/logs"):
                return httpx.Response(200, json={"text": "失败", "next_offset": 6})
            return httpx.Response(200, json={"id": TASK_ID, "state": "failed", "exit_code": 7})
        code, result = await self.invoke(["submit", "--cwd", "/server/test", "--wait", "--json", "--",
                                         "python", "a b.py", "-q"], handle)
        self.assertEqual(code, 7)
        self.assertTrue(result["done"])
        self.assertEqual(result["logs"]["next_offset"], 6)

    async def test_wait_timeout_preserves_existing_task_and_offset(self):
        def handle(req):
            self.assertEqual(req.method, "GET")
            if req.url.path.endswith("/logs"):
                self.assertEqual(req.url.params["offset"], "9")
                return httpx.Response(200, json={"text": ""})
            return httpx.Response(200, json={"id": TASK_ID, "state": "queued", "exit_code": None})
        code, result = await self.invoke(["--json", "wait", TASK_ID, "--timeout", "0", "--offset", "9"], handle)
        self.assertEqual(code, 124)
        self.assertFalse(result["done"])
        self.assertEqual(result["logs"]["next_offset"], 9)

    async def test_ambiguous_submission_sent_once(self):
        requests = []
        def disconnect(req):
            requests.append(req)
            raise httpx.ReadTimeout("response lost")
        with self.assertRaisesRegex(PlatformError, "核对提交结果"):
            await self.invoke(["submit", "--cwd", "/server", "--", "python"], disconnect)
        self.assertEqual(len(requests), 1)
