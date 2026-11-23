ALTER TABLE tasks ADD COLUMN dispatched TEXT;
-- Earlier versions could already have sent a launch for any reserved run.
UPDATE tasks SET dispatched=reserved WHERE run_id IS NOT NULL;
PRAGMA user_version=2;
