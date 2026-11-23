import os
from pathlib import Path
import pwd
import subprocess
from types import SimpleNamespace

from gpu_platform.runner import telemetry
from gpu_platform.runner.gpu import Device
from gpu_platform.store.db import Store
from gpu_platform.workspace.fingerprint import fingerprint, fingerprint_keys, process_fingerprint


def test_renamed_script_matches_without_merging_different_inputs(tmp_path):
    for name in ("train.py", "train_v2.py"):
        (tmp_path / name).write_text("print('same')")
    argv = ["/usr/bin/python3", "train.py", "--batch-size", "8"]
    renamed = ["/bin/bash", "-c", "/usr/bin/python3 train_v2.py --batch-size 8"]
    expected = fingerprint_keys("u", tmp_path, argv, None)
    actual = fingerprint_keys("u", tmp_path, renamed, None)
    assert expected["key"] == actual["key"] and expected["legacy"] != actual["legacy"]
    assert fingerprint("u", tmp_path, argv[:-1] + ["16"], None) != expected["key"]
    (tmp_path / "train_v2.py").write_text("print('changed')")
    assert fingerprint("u", tmp_path, renamed, None) != expected["key"]


def test_live_cpu_process_matches_a_renamed_submission(tmp_path):
    source = "import time; time.sleep(10)"
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text(source)
    user = pwd.getpwuid(os.getuid()).pw_name
    proc = subprocess.Popen(["/usr/bin/python3", "a.py"], cwd=tmp_path)
    try:
        birth = Path(f"/proc/{proc.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        key = process_fingerprint(user, proc.pid, birth, None)
        assert key and key == fingerprint(user, tmp_path, ["/usr/bin/python3", "b.py"], None)
        assert process_fingerprint(user, proc.pid, "stale", None) is None
    finally:
        proc.terminate()
        proc.wait()


async def test_all_cards_are_observed_without_assigning_shared_utilization(monkeypatch):
    cards = [Device("gpu" + str(i), i, "A", i, 48000, 1000, set(),
                    utilization=77) for i in range(8)]
    cards[0].pids.update((11, 12))
    cards[0].process_memory_mib.update({11: 200, 12: 700})
    cards[3].pids.add(13)
    monkeypatch.setattr(telemetry, "snapshot", lambda since: cards)
    monkeypatch.setattr(telemetry, "process_identity", lambda pid: (1001, "birth", "task"))
    monitor = telemetry.HostTelemetry()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(users={}))))
    await monitor.sample(req)
    cards[0].process_utilization[11] = 22
    frame = await monitor.sample(req)
    assert len(frame["cards"]) == 8
    by_pid = {p["pid"]: p for p in frame["processes"]}
    assert by_pid[11]["sm"] == 22 and by_pid[11]["solo_util"] is None
    assert by_pid[12]["sm"] is None and by_pid[12]["solo_util"] is None
    assert by_pid[13]["solo_util"] == 77
    cards[1].pids.add(13)
    frame = await monitor.sample(req)
    assert all(not p["single_gpu"] and p["solo_util"] is None for p in frame["processes"] if p["pid"] == 13)


async def test_host_history_can_raise_estimate_only_for_matching_model_and_baseline(tmp_path):
    store = await Store.open(tmp_path / "platform.db")
    try:
        a = dict(gpu="seven", idx=7, model="A", total=48000, used=0, util=0, clients=0)
        b = dict(a, gpu="zero", idx=0)
        proc = dict(pid=54, birth="123", run=None, key="script", gpu="zero",
                    mib=2500, sm=46, solo_util=None, single_gpu=True)
        frame = dict(boot="b", sampled=1234, cards=[a, b], processes=[proc])
        await store.record_usage(frame)
        assert not await store.profiles("script")
        await store.db.execute("INSERT INTO gpu_profiles VALUES('old','seven',1000,20)")
        await store.promote_profile("old", "script")
        assert dict((await store.profiles("script"))["seven"]) == dict(gpu="seven", peak_mib=2500, peak_util=46)
        frame["cards"][1] = dict(b, model="B")
        proc.update(mib=9000, sm=89)
        await store.record_usage(frame)
        assert (await store.profiles("script"))["seven"]["peak_mib"] == 2500
        proc.update(birth="124", key=None)
        await store.record_usage(frame)
        frame["sampled"] += 365 * 86400
        frame["processes"] = []
        await store.record_usage(frame)
        async with store.db.execute("SELECT COUNT(*) FROM gpu_usage_processes") as cur:
            assert (await cur.fetchone())[0] == 2
    finally:
        await store.db.close()
