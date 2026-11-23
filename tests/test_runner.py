"""Host-independent checks on GPU ownership, release safety and state reporting."""
import os
from types import SimpleNamespace

import pytest

from gpu_platform.runner import gpu, systemd


def card(uuid, index, pids):
    return gpu.Device(uuid, index, "test", index, 100, 10, set(pids))


def test_classify_mapped_cross_card_and_orphan_units(monkeypatch):
    rows = [card("GPU-A", 0, [10, 11, 12, 13]), card("GPU-B", 1, [11])]
    owners = {10: "active", 11: None, 12: "orphan", 13: None}
    monkeypatch.setattr(gpu, "process_identity", lambda pid: (1001, "41", owners[pid]))
    inventory = gpu.GPUInventory(["GPU-A"])
    inventory.known = {"active"}
    result = inventory.view(rows)[0]
    assert result["external_pids"] == [11, 12, 13]
    assert not result["available"] and "11" in result["conflict"] and "12" in result["conflict"]


def test_cleanup_only_exclusive_external_and_revalidate_placement(monkeypatch):
    rows = [card("GPU-A", 0, [101, 102, 103, 104]), card("GPU-B", 1, [102])]
    owners = {101: None, 102: None, 103: "unknown-unit", 104: None}
    monkeypatch.setattr(gpu, "snapshot", lambda: rows)
    monkeypatch.setattr(gpu, "process_identity",
                        lambda pid: (0 if pid == 104 else 1001, "4", owners[pid]))
    monkeypatch.setattr(os, "pidfd_open", lambda pid: os.open("/dev/null", os.O_RDONLY))
    signals = []
    monkeypatch.setattr(gpu.signal, "pidfd_send_signal", lambda fd, sig: signals.append(sig))
    inventory = gpu.GPUInventory(["GPU-A"])
    inventory.reconcile([])
    inventory.reconcile([])
    assert signals == [gpu.signal.SIGTERM]
    assert list(inventory.terminating) == [(101, "4")]
    rows[1].pids.add(101)
    inventory.reconcile([])
    assert not inventory.terminating
    assert len(signals) == 1


async def test_unit_success_must_wait_for_empty_group(tmp_path, monkeypatch):
    units = systemd.Units(SimpleNamespace(system_mode=False, state_dir=tmp_path))
    path = units.files("abc")
    path.mkdir()
    (path / "result").write_text('{"SERVICE_RESULT":"success","EXIT_CODE":"exited","EXIT_STATUS":"0"}')
    async def stopping(*args):
        return 0, "ActiveState=deactivating\n", ""
    monkeypatch.setattr(systemd, "command", stopping)
    assert not (await units.inspect("abc"))["empty"]
    async def inactive(*args):
        return 0, "ActiveState=inactive\n", ""
    monkeypatch.setattr(systemd, "command", inactive)
    assert (await units.inspect("abc")) == dict(state="succeeded", empty=True, exit_code=0, error=None)


async def test_systemd_error_never_means_free(tmp_path, monkeypatch):
    units = systemd.Units(SimpleNamespace(system_mode=False, state_dir=tmp_path))
    async def unavailable(*args):
        return 1, "", "bus unavailable"
    monkeypatch.setattr(systemd, "command", unavailable)
    with pytest.raises(RuntimeError, match="bus unavailable"):
        await units.inspect("abc")


def test_validate_path_and_privilege(tmp_path):
    units = systemd.Units(SimpleNamespace(system_mode=False, state_dir=tmp_path,
                                          users={"root": "unused"}, gpu_uuids=[]))
    with pytest.raises(PermissionError):
        units.validate(SimpleNamespace(user="root", gpus=[], gpu_count=0))
    with pytest.raises(ValueError):
        units.files("../other")


async def test_idle_notebook_keeps_allocated_cards_and_unknown_unit_blocks(tmp_path, monkeypatch):
    units = systemd.Units(SimpleNamespace(system_mode=False, state_dir=tmp_path,
                                          gpu_uuids=("A", "B")))
    async def inventory(*args):
        return 0, "gpu-run-live.service loaded active running notebook\n", ""
    monkeypatch.setattr(systemd, "command", inventory)
    assert await units.active_allocations() == {"A": {"live": None}, "B": {"live": None}}
    units.files("live").mkdir()
    (units.files("live") / "allocation.json").write_text('{"gpus":["B"]}')
    assert await units.active_allocations() == {"A": {}, "B": {"live": None}}


def test_graphics_on_second_gpu_protects_external_training(monkeypatch):
    first = card("GPU-A", 0, [201])
    second = card("GPU-B", 1, [])
    second.graphics = {201}
    monkeypatch.setattr(gpu, "snapshot", lambda: [first, second])
    monkeypatch.setattr(gpu, "process_identity", lambda pid: (1001, "41", None))
    monkeypatch.setattr(os, "pidfd_open", lambda pid: pytest.fail("cross-card process targeted"))
    result = gpu.GPUInventory(["GPU-A"]).reconcile([])
    assert result[0]["external_pids"] == [201] and result[0]["conflict"]
