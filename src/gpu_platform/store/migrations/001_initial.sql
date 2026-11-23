CREATE TABLE IF NOT EXISTS tasks (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    user TEXT NOT NULL, kind TEXT NOT NULL, cwd TEXT NOT NULL, argv TEXT NOT NULL,
    gpu_count INTEGER NOT NULL CHECK(gpu_count>=0), state TEXT NOT NULL,
    run_id TEXT UNIQUE, gpus TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL,
    reserved TEXT, started TEXT, finished TEXT, error TEXT, exit_code INTEGER
);
CREATE INDEX IF NOT EXISTS queue_order ON tasks(state, sequence);
CREATE TABLE IF NOT EXISTS allocations (
    gpu TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES tasks(run_id)
);
PRAGMA user_version=1;
