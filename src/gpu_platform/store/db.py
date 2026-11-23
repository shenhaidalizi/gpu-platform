import asyncio
import json
from datetime import datetime, timezone
from importlib.resources import files
from uuid import uuid4

import aiosqlite

from .usage import record_usage


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, db):
        self.db = db
        self.lock = asyncio.Lock()

    @classmethod
    async def open(cls, path):
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        async with db.execute("PRAGMA user_version") as cursor:
            version = (await cursor.fetchone())[0]
        migrations = ("001_initial.sql", "002_dispatch.sql", "003_shared_gpu.sql", "004_jupyter_access.sql",
                      "005_auto_share.sql", "006_host_usage.sql")
        if version > len(migrations):
            await db.close()
            raise RuntimeError("数据库版本高于当前服务")
        for migration in migrations[version:]:
            sql = files("gpu_platform.store").joinpath("migrations", migration).read_text()
            await db.executescript("BEGIN IMMEDIATE;\n" + sql + "\nCOMMIT;")
        return cls(db)

    async def rows(self, where="1", args=()):
        async with self.lock:
            async with self.db.execute(f"SELECT * FROM tasks WHERE {where} ORDER BY sequence", args) as cur:
                rows = await cur.fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("argv", "gpus"):
                item[key] = json.loads(item[key])
            result.append(item)
        return result

    async def create(self, user, spec):
        ident = uuid4().hex
        async with self.lock:
            await self.db.execute(
                "INSERT INTO tasks(id,user,kind,cwd,argv,gpu_count,gpu_memory_mib,gpu_on_demand,state,created) VALUES(?,?,?,?,?,?,?,?,'queued',?)",
                (ident, user, spec.kind, spec.cwd, json.dumps(spec.argv), spec.gpu_count, None, spec.kind == "jupyter", now()))
            await self.db.commit()
        return (await self.rows("id=?", (ident,)))[0]

    async def reserve(self, ident, run, gpus, budget=None, profile_key=None):
        async with self.lock:
            try:
                cur = await self.db.execute(
                    "UPDATE tasks SET state='starting',run_id=?,gpus=?,reserved=?,gpu_memory_mib=?,profile_key=? WHERE id=? AND state='queued'",
                    (run, json.dumps(gpus), now(), budget, profile_key, ident))
                if cur.rowcount:
                    await self.db.executemany("INSERT INTO allocations(gpu,run_id,gpu_memory_mib) SELECT ?,run_id,gpu_memory_mib FROM tasks WHERE run_id=? AND gpu_on_demand=0",
                                              [(gpu, run) for gpu in gpus])
                await self.db.commit()
                return bool(cur.rowcount)
            except BaseException:
                await self.db.rollback()
                raise

    async def dispatch(self, run):
        async with self.lock:
            cur = await self.db.execute(
                "UPDATE tasks SET dispatched=? WHERE run_id=? AND state='starting' AND dispatched IS NULL",
                (now(), run))
            await self.db.commit()
            return bool(cur.rowcount)

    async def started(self, run):
        async with self.lock:
            await self.db.execute("UPDATE tasks SET state='running',started=? WHERE run_id=? AND state='starting'",
                                  (now(), run))
            await self.db.commit()

    async def cancel(self, ident):
        async with self.lock:
            await self.db.execute(
                """UPDATE tasks SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'stopping' END,
                finished=CASE WHEN state='queued' THEN ? ELSE NULL END
                WHERE id=? AND state IN ('queued','starting','running')""", (now(), ident))
            await self.db.commit()

    async def record_usage(self, frame):
        async with self.lock:
            try:
                await record_usage(self.db, frame)
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def promote_profile(self, old, new):
        # Proof of the legacy digest requires reading the exact original invocation.
        async with self.lock:
            async with self.db.execute("SELECT 1 FROM gpu_profiles WHERE profile_key=? LIMIT 1", (old,)) as cur:
                if not await cur.fetchone():
                    return
            try:
                await self.db.execute("""INSERT INTO gpu_profiles SELECT ?,gpu,peak_mib,peak_util
                    FROM gpu_profiles WHERE profile_key=? ON CONFLICT(profile_key,gpu) DO UPDATE SET
                    peak_mib=max(peak_mib,excluded.peak_mib), peak_util=max(peak_util,excluded.peak_util)""", (new, old))
                await self.db.execute("DELETE FROM gpu_profiles WHERE profile_key=?", (old,))
                await self.db.execute("UPDATE tasks SET profile_key=? WHERE profile_key=?", (new, old))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def profiles(self, key):
        async with self.lock:
            async with self.db.execute("""SELECT p.gpu,
                    max(p.peak_mib,coalesce(h.peak_mib,0)) AS peak_mib,
                    max(p.peak_util,coalesce(h.peak_util,0)) AS peak_util
                    FROM gpu_profiles p LEFT JOIN gpu_usage_devices d ON d.gpu=p.gpu
                    LEFT JOIN gpu_usage_peaks h ON h.profile_key=p.profile_key AND h.model=d.model AND h.total=d.total
                    WHERE p.profile_key=?""", (key,)) as cur:
                return {row["gpu"]: row for row in await cur.fetchall()}

    async def finish(self, run, state, message=None, exit_code=None, sample=None, over_budget=False):
        async with self.lock:
            try:
                await self.db.execute(
                    """UPDATE tasks SET state=CASE WHEN state='stopping' THEN 'cancelled' ELSE ? END,
                    error=?,exit_code=?,finished=? WHERE run_id=? AND state IN ('starting','running','stopping')""",
                    (state, message, exit_code, now(), run))
                async with self.db.execute("SELECT state,profile_key,gpus,gpu_memory_mib FROM tasks WHERE run_id=?", (run,)) as cur:
                    task = await cur.fetchone()
                if task and task["profile_key"]:
                    key = (task["profile_key"], json.loads(task["gpus"])[0])
                    if task["state"] == "succeeded" and sample:
                        await self.db.execute("""INSERT INTO gpu_profiles VALUES(?,?,?,?)
                            ON CONFLICT(profile_key,gpu) DO UPDATE SET peak_mib=max(peak_mib,excluded.peak_mib),
                            peak_util=max(peak_util,excluded.peak_util)""", (*key, *sample))
                    elif over_budget or (task["state"] == "failed" and task["gpu_memory_mib"]):
                        await self.db.execute("DELETE FROM gpu_profiles WHERE profile_key=? AND gpu=?", key)
                await self.db.execute("DELETE FROM allocations WHERE run_id=?", (run,))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def unreserve(self, run):
        """Only a confirmed admission rejection may return an unstarted task to FIFO."""
        async with self.lock:
            try:
                await self.db.execute("DELETE FROM allocations WHERE run_id=?", (run,))
                await self.db.execute(
                    """UPDATE tasks SET state=CASE WHEN state='stopping' THEN 'cancelled' ELSE 'queued' END,
                    run_id=NULL,gpus='[]',gpu_memory_mib=NULL,profile_key=NULL,reserved=NULL,dispatched=NULL WHERE run_id=? AND state IN ('starting','stopping')""", (run,))
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def rebuild_allocations(self, tasks):
        async with self.lock:
            try:
                await self.db.execute("DELETE FROM allocations")
                await self.db.executemany("INSERT INTO allocations(gpu,run_id,gpu_memory_mib) SELECT ?,run_id,gpu_memory_mib FROM tasks WHERE run_id=? AND gpu_on_demand=0",
                                          [(gpu, t["run_id"]) for t in tasks for gpu in t["gpus"]])
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise
