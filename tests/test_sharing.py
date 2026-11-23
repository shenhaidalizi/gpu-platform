import sqlite3
from importlib.resources import files

import pytest
from pydantic import ValidationError

from gpu_platform.models import Submission
from gpu_platform.runner.server import Start
from gpu_platform.sharing import can_fit, shareable_mib
from gpu_platform.store.db import Store


def test_committed_and_measured_memory():
    card = dict(available=False, runs=["a", "b"], memory_total_mib=20000,
                memory_used_mib=8000, used_by_run_mib={"a": 6000, "b": 1000})
    holders = {"a": 4000, "b": 4000}
    assert shareable_mib(card, holders, 3, 2000) == 7000
    assert not can_fit(card, holders, 1, 2, 2000)
    assert not can_fit(card, holders, None, 3, 2000)
    assert not can_fit({**card, "external_pids": [314]}, holders, 1, 3, 2000)
    assert not can_fit({**card, "runs": ["orphan"]}, holders, 1, 3, 2000)
    assert not can_fit(card, {"a": None, "b": 4000}, 1, 3, 2000)


@pytest.mark.parametrize("count,budget", [(0, 100), (2, 100), (1, 0), (1, -1), (1, True)])
def test_invalid_requests(count, budget):
    with pytest.raises(ValidationError):
        Start(user="u", gpus=[], kind="terminal", argv=["/bin/sh"], cwd="/tmp", gpu_count=count, gpu_memory_mib=budget)


async def test_migration_preserves_active_exclusive(tmp_path):
    path = tmp_path / "db"
    with sqlite3.connect(path) as old:
        for script in ("001_initial.sql", "002_dispatch.sql"):
            old.executescript(files("gpu_platform.store").joinpath("migrations", script).read_text())
        old.execute("""INSERT INTO tasks(id,user,kind,cwd,argv,gpu_count,state,run_id,created)
                       VALUES('old','u','batch','/tmp','[]',1,'running','run-old','now')""")
        old.execute("INSERT INTO allocations VALUES('gpu7','run-old')")
    store = await Store.open(path)
    try:
        task = await store.create("u", Submission(argv=["true"], cwd="/tmp", gpu_memory_mib=128))
        with pytest.raises(sqlite3.IntegrityError):
            await store.reserve(task["id"], "new", ["gpu7"], budget=128)
        assert (await store.rows("id='old'"))[0]["gpu_memory_mib"] is None
        assert (await store.rows("id=?", (task["id"],)))[0]["state"] == "queued"
    finally:
        await store.db.close()


def test_notebook_context_is_visible_and_does_not_consume_a_slot():
    from gpu_platform.runner.systemd import describe_holders
    card = dict(available=False, memory_total_mib=20000, memory_used_mib=3000,
                observed_runs=["notebook"], used_by_run_mib={"notebook": 2900})
    holders = describe_holders(card, {"notebook": "on_demand"}, {"notebook"})
    assert holders == {} and card["jupyter_used_mib"] == 2900
    assert can_fit(card, holders, None, 2, 2048)
    assert can_fit(card, holders, 14000, 2, 2048)
    assert not can_fit(card, holders, 16000, 2, 2048)
    assert not can_fit({**card, "memory_used_mib": 19000}, holders, None, 2, 2048)
    describe_holders(card, {"notebook": "on_demand"}, set())
    assert not can_fit(card, {}, None, 2, 2048)
