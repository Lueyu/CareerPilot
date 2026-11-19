CREATE UNIQUE INDEX IF NOT EXISTS uq_exam_completion_memory_source
ON memory_items (workspace_id, source_ref)
WHERE mtype = 'TaskCompletion' AND source_ref LIKE 'exam_completion:%';

CREATE OR REPLACE FUNCTION capture_exam_completion_memory(target_workspace_id integer DEFAULT NULL)
RETURNS void LANGUAGE sql AS $function$
INSERT INTO memory_items (workspace_id, mtype, content, source, source_ref, status, revision, freshness, meta, created_at)
SELECT exam.workspace_id, 'TaskCompletion', '已记录考试完成：' || exam.title,
       'SYSTEM', 'exam_completion:' || exam.id, 'CANDIDATE', 1, 'CURRENT',
       jsonb_build_object('exam_id', exam.id, 'exam_kind', exam.kind, 'completed_at', exam.completed_at),
       exam.completed_at
FROM exams AS exam
WHERE (target_workspace_id IS NULL OR exam.workspace_id = target_workspace_id)
  AND exam.status = 'COMPLETED' AND exam.completed_at IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM exam_questions AS question JOIN exam_answers AS answer ON answer.exam_question_id = question.id
      WHERE question.exam_id = exam.id
        AND (answer.choice_index >= 0
             OR answer.text_answer ~ '[^[:space:]　]'
             OR answer.transcript ~ '[^[:space:]　]')
  )
  AND NOT EXISTS (
      SELECT 1 FROM memory_items AS item
      WHERE item.workspace_id = exam.workspace_id AND item.mtype = 'TaskCompletion'
        AND item.source_ref = 'exam_completion:' || exam.id
  )
ON CONFLICT DO NOTHING;
$function$;

SELECT capture_exam_completion_memory();
