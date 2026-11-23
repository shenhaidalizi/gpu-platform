"""Single-card admission checks shared by the scheduler and the local runner."""


def shareable_mib(card, holders, limit, headroom):
    if (not usable(card, holders) or len(holders) >= limit
            or any(budget is None for budget in holders.values())):
        return 0
    # Start with actual device use (including driver overhead and non-compute data).
    # Charge each holder for the part of its reservation that it has not used yet.
    used_by_run = card.get("used_by_run_mib", {})
    remaining_claims = sum(max(0, budget - used_by_run.get(run, 0))
                           for run, budget in holders.items())
    return max(0, card.get("memory_total_mib", 0) - card.get("memory_used_mib", 0)
               - remaining_claims - headroom)


def accessible(card):
    return not card.get("conflict") and not card.get("external_pids") and card.get("healthy", True)


def usable(card, holders):
    if not accessible(card):
        return False
    observed = set(card.get("runs", ())) | set(card.get("observed_runs", ()))
    if observed - holders.keys() - set(card.get("jupyter_runs", ())):
        return False
    return card.get("available", True) or bool(observed)


def can_fit(card, holders, budget, limit, headroom):
    if budget is None:
        if holders or not usable(card, holders):
            return False
        if card.get("jupyter_using_gpu"):
            return card["memory_total_mib"] - card["memory_used_mib"] > headroom
        return card.get("available", True)
    if holders:
        utilization = card.get("utilization")
        if utilization is None or utilization > 80 or not card.get("metering_complete"):
            return False
        measured = card.get("used_by_run_mib", {})
        if any(claim is None or measured.get(run, 0) > claim for run, claim in holders.items()):
            return False
    return shareable_mib(card, holders, limit, headroom) >= budget
