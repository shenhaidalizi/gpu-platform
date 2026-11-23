from types import SimpleNamespace

import pytest

from gpu_platform.models import Submission
from gpu_platform.scheduler.engine import Engine
from gpu_platform.store.db import Store


class TestRunner:
    __test__ = False

    def __init__(self):
        self.units = {}
        self.conflict = False
        self.starts = []
        self.samples = {}

    async def call(self, method, path, **kw):
        if path == "/workspace/profile":
            return {"key": kw["json"]["user"]}
        if path in ("/gpus", "/reconcile"):
            return [{"uuid": u, "conflict": self.conflict, "memory_total_mib": 12000, "metering_complete": True,
                     "utilization": 20, "used_by_run_mib": self.samples, "observed_runs": list(self.samples)} for u in ("gpu-a", "gpu-b")]
        run = path.split("/")[2]
        if path.endswith("/start"):
            self.starts.append((run, kw["json"]["gpus"]))
            self.units[run] = {"state": "running", "empty": False}
            return {}
        if path.endswith("/stop"):
            self.units[run] = {"state": "failed", "empty": True}
            return {}
        return self.units.get(run, {"state": "missing", "empty": True})


@pytest.fixture
async def system(tmp_path):
    cfg = SimpleNamespace(gpu_uuids=("gpu-a", "gpu-b"), shared_limit=2, shared_headroom_mib=2048)
    store = await Store.open(tmp_path / "db")
    runner = TestRunner()
    engine = Engine(cfg, store, runner)
    await engine._tick()
    try:
        yield engine, store, runner
    finally:
        await engine.close()
        await store.db.close()


def spec(count=1):
    return Submission(argv=["/bin/true"], cwd="/tmp", gpu_count=count)


async def settle(engine):
    import asyncio
    await engine._tick()
    await asyncio.gather(*list(engine.pending.values()))


async def test_fifo_atomic_multi_gpu_and_recovery(system):
    engine, store, runner = system
    first = await engine.submit("alice", spec())
    second = await engine.submit("bob", spec(2))
    third = await engine.submit("bob", spec())
    await settle(engine)
    assert len(runner.starts) == 1
    assert (await engine.get_owned(third["id"], "bob"))["state"] == "queued"

    recovered = Engine(engine.settings, store, runner)
    await settle(recovered)
    assert len(runner.starts) == 1
    run = (await recovered.get_owned(first["id"], "alice"))["run_id"]
    runner.units[run] = {"state": "succeeded", "empty": True, "exit_code": 0}
    await settle(recovered)
    assert len(runner.starts) == 2
    allocated = await recovered.get_owned(second["id"], "bob")
    assert set(allocated["gpus"]) == {"gpu-a", "gpu-b"}
    recovered._release(run)
    assert all(holders == {allocated["run_id"]: None} for holders in recovered.owners.values())


async def test_cancel_and_external_conflict(system):
    engine, store, runner = system
    runner.conflict = True
    a = await engine.submit("alice", spec())
    b = await engine.submit("alice", spec())
    await settle(engine)
    assert not runner.starts
    await engine.cancel(a["id"], "alice")
    runner.conflict = False
    await settle(engine)
    assert len(runner.starts) == 1
    await engine.cancel(b["id"], "alice")
    await settle(engine)
    await settle(engine)
    assert not engine.owners
    assert (await engine.get_owned(b["id"], "alice"))["state"] == "cancelled"


async def test_no_implicit_retry_when_run_disappears(system):
    engine, store, runner = system
    item = await engine.submit("alice", spec())
    await settle(engine)
    runner.units.clear()
    engine.ready = False
    await settle(engine)
    assert (await engine.get_owned(item["id"], "alice"))["state"] == "failed"
    assert len(runner.starts) == 1


async def test_recover_proven_undispatched_reservation(system):
    engine, store, runner = system
    item = await engine.submit("alice", spec())
    await store.reserve(item["id"], "interrupted", ["gpu-a"])
    engine.ready = False
    await settle(engine)
    actual = await engine.get_owned(item["id"], "alice")
    assert actual["run_id"] != "interrupted"
    assert actual["state"] == "running"
    assert len(runner.starts) == 1


async def test_shared_single_card_fifo_and_restart(system):
    engine, store, runner = system
    engine.settings.gpu_uuids = ("gpu-a",)
    share = Submission(argv=["python", "train.py"], cwd="/tmp")
    cold = await engine.submit("alice", share)
    await settle(engine)
    first = await engine.get_owned(cold["id"], "alice")
    assert first["gpu_memory_mib"] is None
    run = first["run_id"]
    runner.samples[run] = 2400
    for _ in range(3):
        engine.observations[run].last_sample = 0
        await settle(engine)
    runner.samples.clear()
    runner.units[run] = {"state": "succeeded", "empty": True, "exit_code": 0}
    await settle(engine)
    runner.starts.clear()
    pair = [await engine.submit("alice", share) for _ in range(2)]
    exclusive = await engine.submit("bob", spec())
    later = await engine.submit("alice", share)
    await settle(engine)
    assert len(runner.starts) == 2
    assert list(engine.owners["gpu-a"].values()) == [3512, 3512]
    recovered = Engine(engine.settings, store, runner)
    await settle(recovered)
    assert recovered.owners == engine.owners
    await recovered.cancel(pair[0]["id"], "alice")
    await settle(recovered)
    await settle(recovered)
    assert len(recovered.owners["gpu-a"]) == 1
    assert (await recovered.get_owned(later["id"], "alice"))["state"] == "queued"
    await recovered.cancel(pair[1]["id"], "alice")
    await settle(recovered)
    await settle(recovered)
    assert (await recovered.get_owned(exclusive["id"], "bob"))["state"] == "running"
    assert (await recovered.get_owned(later["id"], "alice"))["state"] == "queued"


async def test_shared_budget_admission(system):
    engine, _, runner = system
    engine.settings.gpu_uuids = ("gpu-a",)
    # Old agents can send an arbitrary small reservation, but both unknown jobs run alone.
    share = Submission(argv=["python", "train.py"], cwd="/tmp", gpu_memory_mib=1)
    await engine.submit("alice", share)
    await engine.submit("bob", share)
    await settle(engine)
    assert len(runner.starts) == 1


async def test_jupyter_ignores_training_fifo_and_recovers_without_reserving(system):
    engine, store, runner = system
    engine.settings.gpu_uuids = ("gpu-a",)
    batch = await engine.submit("alice", spec())
    waiting = await engine.submit("bob", spec())
    notebook = await engine.submit("bob", Submission(kind="jupyter", argv=["jupyter-lab"], cwd="/tmp"))
    await settle(engine)
    assert (await engine.get_owned(waiting["id"], "bob"))["state"] == "queued"
    nb = await engine.get_owned(notebook["id"], "bob")
    assert nb["state"] == "running" and nb["gpus"] == ["gpu-a"]
    assert len(runner.starts) == 2
    engine.ready = False
    await settle(engine)
    assert nb["run_id"] not in engine.owners["gpu-a"]
    async with store.db.execute("SELECT COUNT(*) FROM allocations WHERE run_id=?", (nb["run_id"],)) as cur:
        assert (await cur.fetchone())[0] == 0
    await engine.cancel(batch["id"], "alice")
    await settle(engine)
    await settle(engine)
    assert (await engine.get_owned(waiting["id"], "bob"))["state"] == "running"
    assert (await engine.get_owned(notebook["id"], "bob"))["state"] == "running"
