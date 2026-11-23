-- Old running units keep the allocation they were launched with.
ALTER TABLE tasks ADD COLUMN gpu_on_demand INTEGER NOT NULL DEFAULT 0 CHECK(gpu_on_demand IN (0,1));
UPDATE tasks SET gpu_on_demand=1, gpu_memory_mib=NULL WHERE kind='jupyter' AND state='queued';
PRAGMA user_version=4;
