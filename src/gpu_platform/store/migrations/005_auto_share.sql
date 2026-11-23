ALTER TABLE tasks ADD COLUMN profile_key TEXT;
UPDATE tasks SET gpu_memory_mib=NULL WHERE state='queued';
CREATE TABLE gpu_profiles (
    profile_key TEXT NOT NULL, gpu TEXT NOT NULL, peak_mib INTEGER NOT NULL,
    peak_util INTEGER NOT NULL, PRIMARY KEY(profile_key,gpu)
);
PRAGMA user_version=5;
