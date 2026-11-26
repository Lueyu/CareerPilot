from app.models import Question
from app.models.question import STUDY_CHAT_REVIEW_META

PRIVATE_LIBRARY_ORIGINS = ('library_import', 'library_example', 'library_starter')


def private_library_question_clause(workspace_id):
    return (Question.workspace_id == workspace_id) & (
        Question.generation_meta['assessment_use'].as_string() == 'PRACTICE_ONLY'
    ) & Question.generation_meta['origin'].as_string().in_(PRIVATE_LIBRARY_ORIGINS)


def is_private_library_question(question, workspace_id):
    meta = question.generation_meta or {}
    return (question.workspace_id == workspace_id
            and meta.get('assessment_use') == 'PRACTICE_ONLY'
            and meta.get('origin') in PRIVATE_LIBRARY_ORIGINS)


def is_bank_practice_snapshot(snapshot):
    return any(snapshot.get(key) is True for key in ('experienced_bank_practice', 'bank_practice'))


def study_review_question_clause(workspace_id):
    return (Question.workspace_id == workspace_id) & Question.generation_meta.contains(STUDY_CHAT_REVIEW_META)


def bank_list_question_clause(workspace_id, *, mine=False):
    workspace_filter = Question.workspace_id == workspace_id
    if not mine:
        workspace_filter |= Question.workspace_id.is_(None)
    return workspace_filter & Question.canonical_question_id.is_(None) & (
        Question.generation_meta["assessment_use"].as_string().is_distinct_from("PRACTICE_ONLY")
        | study_review_question_clause(workspace_id)
        | private_library_question_clause(workspace_id)
    )
