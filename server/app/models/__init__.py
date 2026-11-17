"""模型汇总导入: 保证 Base.metadata 收集所有表。"""
from app.db import Base  # noqa: F401
from app.models.workspace import User, Workspace, WorkspaceMember  # noqa: F401
from app.models.app_setting import AppSetting  # noqa: F401
from app.models.skill import Skill, JobStandard, JobStandardTarget, SkillState  # noqa: F401
from app.models.question import Question, ImportSession, ImportItem  # noqa: F401
from app.models.question_generation import QuestionGeneration  # noqa: F401
from app.models.project_analysis import ProjectAnalysis, AnalysisStep, WorkerHeartbeat  # noqa: F401
from app.models.evidence import Evidence  # noqa: F401
from app.models.exam import Exam, ExamQuestion, ExamAnswer  # noqa: F401
from app.models.assessment import AnswerRevision, ScoringRun, ScoringStep  # noqa: F401
from app.models.learning import LearningTask  # noqa: F401
from app.models.learning_feedback import LearningGoal, LearningActivity, LearningSubmission  # noqa: F401
from app.models.learning_project import LearningProjectSnapshot  # noqa: F401
from app.models.memory import MemoryItem  # noqa: F401
from app.models.trace import Trace, TraceSpan, DecisionRecord, Event  # noqa: F401
from app.models.public_content import (  # noqa: F401
    PublicContentSource,
    PublicContentDraft,
    ContentPublication,
    ContentPublicationRule,
    ContentReleaseAudit,
)
from app.models.project import (  # noqa: F401
    Project,
    ProjectScan,
    ProjectFinding,
    CareerAsset,
    AssetUpdateRun,
)
from app.models.evolution import EvolutionCandidate, EvolutionRun  # noqa: F401
from app.models.competency import CompetencySnapshot  # noqa: F401
from app.models.jd import JdSource  # noqa: F401

__all__ = [
    "Base", "User", "Workspace", "WorkspaceMember", "AppSetting",
    "Skill", "JobStandard", "JobStandardTarget", "SkillState",
    "Question", "ImportSession", "ImportItem",
    "Evidence", "Exam", "ExamQuestion", "ExamAnswer",
    "LearningTask", "MemoryItem",
    "Trace", "TraceSpan", "DecisionRecord", "Event",
    "PublicContentSource", "PublicContentDraft", "ContentPublication",
    "ContentPublicationRule", "ContentReleaseAudit",
    "Project", "ProjectScan", "ProjectFinding", "ProjectAnalysis", "CareerAsset", "AssetUpdateRun",
    "EvolutionCandidate", "EvolutionRun", "CompetencySnapshot", "JdSource",
]
