CREATE TABLE gpu_usage_devices (
    gpu TEXT PRIMARY KEY, model TEXT NOT NULL, idx INTEGER NOT NULL,
    total INTEGER NOT NULL, used INTEGER NOT NULL, util INTEGER, clients INTEGER NOT NULL,
    sampled INTEGER NOT NULL
);
CREATE TABLE gpu_usage_processes (
    boot TEXT NOT NULL, gpu TEXT NOT NULL, pid INTEGER NOT NULL, birth TEXT NOT NULL,
    run TEXT, profile_key TEXT, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
    peak_mib INTEGER, peak_sm INTEGER, peak_solo_util INTEGER,
    PRIMARY KEY(boot,gpu,pid,birth)
);
CREATE INDEX usage_last_seen ON gpu_usage_processes(last_seen);
CREATE TABLE gpu_usage_peaks (
    profile_key TEXT NOT NULL, model TEXT NOT NULL, total INTEGER NOT NULL,
    peak_mib INTEGER NOT NULL, peak_util INTEGER NOT NULL,
    PRIMARY KEY(profile_key,model,total)
);
PRAGMA user_version=6;
