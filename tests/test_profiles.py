from time import monotonic

from gpu_platform.models import Submission
from gpu_platform.scheduler.profile import Observation, estimate
from gpu_platform.workspace.fingerprint import fingerprint


def test_fingerprint_tracks_edited_training_inputs(tmp_path):
    script = tmp_path / "train.py"
    script.write_text("print(99)")
    config = tmp_path / "model.json"
    config.write_text("{}")
    args = ["/bin/bash", "-c", "/usr/bin/python3 train.py --config=model.json"]

    def key():
        return fingerprint("u", str(tmp_path), args, None)

    original = key()
    assert original and key() == original
    config.write_text('{"batch_size": 128}')
    changed = key()
    assert changed and changed != original
    script.write_text("print(11)")
    assert key() != changed
    args[-1] += " && ./other.sh"
    assert key() is None


def test_profile_requires_isolated_and_complete_samples():
    card = dict(utilization=15, used_by_run_mib={"r": 400}, observed_runs=["r"], metering_complete=True)
    obs = Observation()
    start = monotonic()
    obs.observe("r", card, {"r": None}, start)
    obs.observe("r", card, {"r": None}, start + 2)
    assert obs.baseline() is None
    obs.observe("r", card, {"r": None}, start + 4)
    assert obs.baseline() == (400, 15)
    obs.observe("r", {**card, "observed_runs": ["r", "notebook"]}, {"r": None}, start + 6)
    assert obs.baseline() is None


def test_two_job_sharing_requires_peak_below_45_percent():
    assert estimate({"peak_mib": 400, "peak_util": 44}, 2) == 1012
    assert estimate({"peak_mib": 400, "peak_util": 45}, 2) is None
    assert estimate({"peak_mib": 400, "peak_util": 46}, 2) is None


def test_api_budget_is_only_a_dispatch_result():
    task = Submission(cwd="/tmp", argv=["python", "train.py"], gpu_memory_mib=1)
    assert "gpu_memory_mib" not in task.model_dump()
    assert "gpu_memory_mib" not in Submission.model_json_schema()["properties"]
