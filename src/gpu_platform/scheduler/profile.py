"""A baseline comes from an uninterrupted, successfully completed solo run."""
from dataclasses import dataclass, field
from time import monotonic


@dataclass
class Observation:
    last_tick: float = field(default_factory=monotonic)
    last_sample: float = 0
    samples: int = 0
    peak_mib: int = 0
    peak_util: int = 0
    solo: bool = True

    def observe(self, run, card, holders, now):
        if (now - self.last_tick > 8 or not card or card.get("conflict")
                or card.get("external_pids") or not card.get("metering_complete")
                or set(holders) != {run} or set(card.get("observed_runs", ())) - {run}):
            self.solo = False
        self.last_tick = now
        if not card or not card.get("metering_complete"):
            return
        mib = card.get("used_by_run_mib", {}).get(run, 0)
        self.peak_mib = max(self.peak_mib, mib)
        if mib:
            util = card.get("utilization")
            if util is None:
                self.solo = False
            else:
                self.peak_util = max(self.peak_util, util)
            if now - self.last_sample >= 1:
                self.samples += 1
                self.last_sample = now

    def baseline(self):
        if self.solo and self.samples >= 3 and monotonic() - self.last_tick <= 8:
            return self.peak_mib, self.peak_util
        return None


def estimate(profile, limit):
    if profile is None:
        return None
    peak = profile["peak_util"]
    eligible = peak < 45 if limit == 2 else peak * limit <= 80
    if not eligible:
        return None
    return (profile["peak_mib"] * 5 + 3) // 4 + 512
