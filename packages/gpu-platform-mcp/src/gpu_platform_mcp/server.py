from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from gpu_platform_client.client import Jobs, connect


server = FastMCP("gpu-platform", instructions="Use the GPU platform for CUDA commands, including agent-initiated smoke tests. Use server-side paths. Each submission creates a new job.")
Seconds = Annotated[int, Field(ge=0, le=30)]
Offset = Annotated[int, Field(ge=0)]


@server.tool()
async def submit_gpu_job(cwd: str, argv: list[str], gpu_count: Annotated[int, Field(ge=1)] = 1,
                         wait_seconds: Seconds = 0) -> dict[str, Any]:
    """Submit one GPU command with its server-side absolute cwd and literal argv. Zero wait returns its task immediately. A positive wait also returns status, exit code and one log page. Never retry an ambiguous submission."""
    async with connect() as http:
        jobs = Jobs(http)
        task = await jobs.submit(cwd, argv, gpu_count)
        if wait_seconds:
            return await jobs.wait(task["id"], wait_seconds)
        return {"task": task}


@server.tool()
async def wait_for_task(task_id: str, timeout_seconds: Seconds = 20, offset: Offset = 0) -> dict[str, Any]:
    """Wait briefly for an existing job and read one log page. A timeout leaves it queued or running. Resume with its ID and next_offset."""
    async with connect() as http:
        return await Jobs(http).wait(task_id, timeout_seconds, offset)


@server.tool()
async def get_task(task_id: str) -> dict[str, Any]:
    """Get the current state and exit code of one of your tasks."""
    async with connect() as http:
        return await Jobs(http).get(task_id)


@server.tool()
async def list_my_tasks() -> list[dict[str, Any]]:
    """List your tasks, including their IDs, commands, working directories and timestamps."""
    async with connect() as http:
        return await Jobs(http).tasks()


@server.tool()
async def read_task_logs(task_id: str, offset: Offset = 0) -> dict[str, Any]:
    """Read one page from a byte offset. Use next_offset to continue."""
    async with connect() as http:
        return await Jobs(http).logs(task_id, offset)


@server.tool()
async def get_platform_status() -> dict[str, Any]:
    """Show managed GPUs and queue positions for the current user."""
    async with connect() as http:
        return await Jobs(http).platform()


@server.tool()
async def cancel_task(task_id: str) -> dict[str, Any]:
    """Request cancellation of the specified owned task. Use get_task to verify termination."""
    async with connect() as http:
        return await Jobs(http).cancel(task_id)


def main():
    server.run(transport="stdio")
