ALTER TABLE tasks ADD COLUMN gpu_memory_mib INTEGER
    CHECK (gpu_memory_mib IS NULL OR (gpu_memory_mib > 0 AND gpu_count = 1));
ALTER TABLE allocations RENAME TO allocations_v2;
CREATE TABLE allocations (
    gpu TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES tasks(run_id),
    gpu_memory_mib INTEGER CHECK (gpu_memory_mib IS NULL OR gpu_memory_mib > 0),
    PRIMARY KEY(gpu, run_id)
);
INSERT INTO allocations(gpu, run_id) SELECT gpu, run_id FROM allocations_v2;
DROP TABLE allocations_v2;
CREATE TRIGGER guard_exclusive_gpu BEFORE INSERT ON allocations
WHEN EXISTS (SELECT 1 FROM allocations WHERE gpu = NEW.gpu
    AND (gpu_memory_mib IS NULL OR NEW.gpu_memory_mib IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'exclusive GPU allocation conflict');
END;
PRAGMA user_version=3;
