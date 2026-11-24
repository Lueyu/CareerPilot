"""模型汇总导入: 保证 Base.metadata 收集所有表。"""
from app.db import Base  # noqa: F401
from app.models.app_setting import AppSetting  # noqa: F401
from app.models.assessment import AnswerRevision, ScoringRun, ScoringStep  # noqa: F401
from app.models.competency import CompetencySnapshot  # noqa: F401
from app.models.evidence import Evidence  # noqa: F401
from app.models.evolution import EvolutionCandidate, EvolutionRun  # noqa: F401
from app.models.exam import Exam, ExamAnswer, ExamQuestion  # noqa: F401
from app.models.jd import JdSource  # noqa: F401
from app.models.learning import LearningTask  # noqa: F401
from app.models.learning_feedback import LearningActivity, LearningGoal, LearningSubmission  # noqa: F401
from app.models.learning_project import LearningProjectSnapshot  # noqa: F401
from app.models.memory import MemoryItem  # noqa: F401
from app.models.project import (  # noqa: F401
    AssetUpdateRun,
    CareerAsset,
    Project,
    ProjectFinding,
    ProjectScan,
)
from app.models.product_feedback import FeedbackAttempt, FeedbackSubmission  # noqa: F401
from app.models.project_analysis import AnalysisStep, ProjectAnalysis, WorkerHeartbeat  # noqa: F401
from app.models.project_resume import ProjectResumeDraft
from app.models.public_content import (  # noqa: F401
    ContentPublication,
    ContentPublicationRule,
    ContentReleaseAudit,
    PublicContentDraft,
    PublicContentSource,
)
from app.models.question import ImportItem, ImportSession, Question  # noqa: F401
from app.models.question_generation import QuestionGeneration  # noqa: F401
from app.models.retest_mapping import RetestCriterionMapping  # noqa: F401
from app.models.skill import JobStandard, JobStandardTarget, Skill, SkillState  # noqa: F401
from app.models.study_chat import StudyInterviewQuestion, StudyMessage, StudySession
from app.models.study_notes import StudyCard, StudyCardEvent, StudyNote, StudyNoteJob, StudyNoteVersion
from app.models.study_practice import StudyPractice
from app.models.trace import DecisionRecord, Event, Trace, TraceSpan  # noqa: F401
from app.models.workspace import User, Workspace, WorkspaceMember  # noqa: F401

__all__ = [
    "Base", "User", "Workspace", "WorkspaceMember", "AppSetting",
    "Skill", "JobStandard", "JobStandardTarget", "SkillState",
    "Question", "ImportSession", "ImportItem", "RetestCriterionMapping",
    "Evidence", "Exam", "ExamQuestion", "ExamAnswer",
    "LearningTask", "MemoryItem",
    "Trace", "TraceSpan", "DecisionRecord", "Event",
    "PublicContentSource", "PublicContentDraft", "ContentPublication",
    "ContentPublicationRule", "ContentReleaseAudit",
    "Project", "ProjectScan", "ProjectFinding", "ProjectAnalysis", "ProjectResumeDraft", "CareerAsset", "AssetUpdateRun",
    "EvolutionCandidate", "EvolutionRun", "CompetencySnapshot", "JdSource",
]
