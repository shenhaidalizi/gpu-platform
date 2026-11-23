import argparse
import asyncio
import json
import sys

import httpx

from .client import Jobs, PlatformError, connect


def parser():
    app = argparse.ArgumentParser(prog="gpu-jobs", description="通过 GPU 平台提交和查询任务")
    fmt = argparse.ArgumentParser(add_help=False)
    fmt.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="输出 JSON")
    app.add_argument("--json", action="store_true", help="输出 JSON")
    commands = app.add_subparsers(dest="action", required=True)
    submit = commands.add_parser("submit", parents=[fmt], help="提交服务器上的命令")
    submit.add_argument("--cwd", required=True, help="服务器上的绝对工作目录")
    submit.add_argument("--gpus", type=int, default=1)
    submit.add_argument("--wait", action="store_true", help="等待短测试并返回退出码和一页日志")
    submit.add_argument("argv", nargs=argparse.REMAINDER, help="-- 可执行文件及其参数")
    commands.add_parser("platform", parents=[fmt], help="查询 GPU 和队列")
    commands.add_parser("list", parents=[fmt], help="列出自己的任务")
    wait = commands.add_parser("wait", parents=[fmt], help="等待已有任务并读取日志")
    wait.add_argument("task_id")
    logs = commands.add_parser("logs", parents=[fmt], help="读取一页日志")
    logs.add_argument("task_id")
    for command in (logs, wait):
        command.add_argument("--offset", type=int, default=0, help="日志字节偏移")
    for name in ("status", "cancel"):
        command = commands.add_parser(name, parents=[fmt])
        command.add_argument("task_id")
    for command in (submit, wait):
        command.add_argument("--timeout", type=int, default=30, help="等待秒数（0–3600），超时后保留任务")
    return app


def show(result, as_json):
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif isinstance(result, list):
        for row in result:
            print(row["id"], row["state"], row["cwd"])
    elif "logs" in result:
        task = result["task"]
        print(task["id"], task["state"], f"exit_code={task['exit_code']}", file=sys.stderr)
        if task.get("error"):
            print(task["error"], file=sys.stderr)
        print(result["logs"]["text"], end="")
    elif "text" in result:
        print(result["text"], end="")
    elif "id" in result:
        print(result["id"], result["state"])
        if result.get("error"):
            print(result["error"], file=sys.stderr)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


async def show_devices(jobs, task):
    assigned = task.get("gpus") or []
    if not assigned:
        return
    cards = await jobs.request("GET", "gpus")
    indexes = {card["uuid"]: card["index"] for card in cards}
    for ordinal, uuid in enumerate(assigned):
        index = indexes.get(uuid)
        label = f"物理 GPU {index}" if index is not None else f"GPU {uuid}（物理编号未知）"
        print(f"{label}（任务内 cuda:{ordinal}）", file=sys.stderr)


async def run(args):
    async with connect() as http:
        jobs = Jobs(http)
        if args.action in {"submit", "wait"} and not 0 <= args.timeout <= 3600:
            raise PlatformError("--timeout 范围为 0 到 3600 秒。")
        if args.action == "submit":
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            result = await jobs.submit(args.cwd, argv, args.gpus)
            if args.wait:
                print(f"已提交 {result['id']}", file=sys.stderr, flush=True)
                result = await jobs.wait(result["id"], args.timeout)
        elif args.action == "wait":
            result = await jobs.wait(args.task_id, args.timeout, args.offset)
        elif args.action == "list":
            result = await jobs.tasks()
        elif args.action == "platform":
            result = await jobs.platform()
        elif args.action == "logs":
            result = await jobs.logs(args.task_id, args.offset)
        elif args.action == "status":
            result = await jobs.get(args.task_id)
        else:
            result = await jobs.cancel(args.task_id)
        if not args.json and (args.action in {"logs", "status", "wait"} or
                              (args.action == "submit" and args.wait)):
            try:
                task = await jobs.get(args.task_id) if args.action == "logs" else result.get("task", result)
                await show_devices(jobs, task)
            except PlatformError as exc:
                print(f"无法查询物理 GPU 编号：{exc}", file=sys.stderr)
        show(result, args.json)
        if isinstance(result, dict) and "done" in result:
            if not result["done"]:
                return 124
            task = result["task"]
            if task["state"] != "succeeded":
                code = task.get("exit_code")
                return code if isinstance(code, int) and 1 <= code <= 255 else 1
        return 0


def main():
    args = parser().parse_args()
    try:
        return asyncio.run(run(args))
    except (PlatformError, httpx.InvalidURL) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
