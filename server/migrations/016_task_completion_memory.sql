CREATE UNIQUE INDEX IF NOT EXISTS uq_task_completion_memory_source
ON memory_items (workspace_id, source_ref)
WHERE mtype = 'TaskCompletion' AND source_ref LIKE 'learning_task:%';

INSERT INTO memory_items (workspace_id, mtype, content, source, source_ref, status, revision, freshness, meta, created_at)
SELECT task.workspace_id, 'TaskCompletion', '已记录学习任务完成：' || task.title,
       'SYSTEM', 'learning_task:' || task.id, 'CANDIDATE', 1, 'CURRENT',
       jsonb_strip_nulls(jsonb_build_object(
           'source_task_id', task.id,
           'task_kind', task.kind,
           'completed_at', task.completed_at,
           'exam_id', CASE WHEN task.kind = 'RETEST' THEN task.content #> '{retest_result,exam_id}' END
       )), task.completed_at
FROM learning_tasks AS task
WHERE task.status = 'DONE' AND task.completed_at IS NOT NULL
ON CONFLICT DO NOTHING;

CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_plan_evidence_memory_source
ON memory_items (workspace_id, source_ref)
WHERE mtype = 'TaskCompletion' AND source_ref LIKE 'daily_plan:%';
