"""Persist compact host telemetry and conservative evidence for repeat workloads."""

async def record_usage(db, frame):
    sampled = frame["sampled"]
    devices = {row["gpu"]: row for row in frame["cards"]}
    await db.executemany("""INSERT INTO gpu_usage_devices VALUES(:gpu,:model,:idx,:total,:used,:util,:clients,:sampled)
        ON CONFLICT(gpu) DO UPDATE SET model=excluded.model,idx=excluded.idx,total=excluded.total,
        used=excluded.used,util=excluded.util,clients=excluded.clients,sampled=excluded.sampled""",
        [dict(row, sampled=sampled) for row in devices.values()])
    # Platform workers can be joined using their systemd run, including subordinate processes.
    async with db.execute("""SELECT run_id, profile_key FROM tasks WHERE profile_key IS NOT NULL
            AND gpu_count=1 AND kind='batch' AND state IN ('starting','running','stopping')""") as cur:
        platform = dict(await cur.fetchall())
    processes = []
    grouped = {}
    for process in frame["processes"]:
        run = process["run"]
        key = platform.get(run) if run else process["key"]
        row = dict(process, key=key, boot=frame["boot"], sampled=sampled)
        processes.append(row)
        if not key or not row["single_gpu"]:
            continue
        card = devices[row["gpu"]]
        group = (key, card["model"], card["total"], row["gpu"], run or row["pid"])
        mib, util = grouped.get(group, (0, 0))
        grouped[group] = (mib + (row["mib"] or 0), max(util, row["sm"] or 0, row["solo_util"] or 0))
    await db.executemany("""INSERT INTO gpu_usage_processes VALUES
        (:boot,:gpu,:pid,:birth,:run,:key,:sampled,:sampled,:mib,:sm,:solo_util)
        ON CONFLICT(boot,gpu,pid,birth) DO UPDATE SET last_seen=excluded.last_seen,
        profile_key=coalesce(excluded.profile_key,profile_key),run=coalesce(excluded.run,run),
        peak_mib=nullif(max(coalesce(peak_mib,-1),coalesce(excluded.peak_mib,-1)),-1),
        peak_sm=nullif(max(coalesce(peak_sm,-1),coalesce(excluded.peak_sm,-1)),-1),
        peak_solo_util=nullif(max(coalesce(peak_solo_util,-1),coalesce(excluded.peak_solo_util,-1)),-1)
        """, processes)
    await db.executemany("""INSERT INTO gpu_usage_peaks VALUES(?,?,?,?,?)
        ON CONFLICT(profile_key,model,total) DO UPDATE SET peak_mib=max(peak_mib,excluded.peak_mib),
        peak_util=max(peak_util,excluded.peak_util)""",
        [(*group[:3], *peaks) for group, peaks in grouped.items()])
