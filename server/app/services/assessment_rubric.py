"""Versioned rubric contracts and deterministic, evidence-bound score validation.

Legacy compilation preserves the original criteria; it does not manufacture a
human-calibrated standard or change a historical score.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

RUBRIC_SCHEMA_VERSION = 2
JUDGE_VERSION = 'judge-v2'


class AssessmentJudgeError(ValueError):
    safe_for_user = True

    def __init__(self, category, message, *, raw_response=None, usage=None, request_meta=None):
        super().__init__(message)
        self.category = category
        # Private attempt storage only. Neither str/repr nor public errors expose it.
        self.raw_response = raw_response
        self.usage = usage or {'known': False}
        self.request_meta = request_meta or {}


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Anchor(Strict):
    anchor_id: str = Field(min_length=1, max_length=80)
    score: int = Field(ge=0, le=4)
    description: str = Field(min_length=1, max_length=2000)


class Criterion(Strict):
    criterion_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    dimension: str = Field(min_length=1, max_length=80)
    weight: int = Field(ge=1, le=100)
    score_anchors: list[Anchor] = Field(min_length=5, max_length=5)
    required_evidence: list[str] = Field(min_length=1, max_length=20)
    acceptable_alternatives: list[str] = Field(max_length=20)
    common_misconceptions: list[str] = Field(max_length=20)


class CriticalError(Strict):
    error_id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=2000)
    criterion_id: str | None = None
    cap_score: int | None = Field(default=None, ge=0, le=4)
    cap_overall: int | None = Field(default=None, ge=0, le=100)


class Rubric(Strict):
    schema_version: Literal[2]
    question_id: int
    question_version: str = Field(min_length=1, max_length=100)
    rubric_version: str = Field(min_length=1, max_length=100)
    target_skill_ids: list[int] = Field(max_length=20)
    assessed_behaviors: list[str] = Field(min_length=1, max_length=30)
    role_level: str = Field(min_length=1, max_length=160)
    role_standard_version: str = Field(min_length=1, max_length=100)
    question_type: Literal['explanation', 'system_design', 'coding_debugging', 'behavioral', 'legacy_open']
    max_points: float = Field(gt=0, le=1000, allow_inf_nan=False)
    criteria: list[Criterion] = Field(min_length=1, max_length=20)
    critical_errors: list[CriticalError] = Field(max_length=30)
    calibration_status: Literal['LEGACY_UNCALIBRATED', 'AI_DRAFT', 'HUMAN_REVIEWED']
    coverage_gaps: list[str] = Field(max_length=30)
    reference_standard: dict


class CriterionScore(Strict):
    criterion_id: str
    score: int = Field(ge=0, le=4)
    max_score: Literal[4]
    answer_quotes: list[str] = Field(max_length=20)
    satisfied_anchors: list[str] = Field(min_length=1, max_length=5)
    missing_points: list[str] = Field(max_length=20)
    reasoning_summary: str = Field(min_length=1, max_length=2000)
    why_not_higher: str = Field(min_length=1, max_length=2000)


class DetectedError(Strict):
    error_id: str
    answer_quotes: list[str] = Field(min_length=1, max_length=20)
    reasoning_summary: str = Field(min_length=1, max_length=2000)


class Judgement(Strict):
    question_id: int
    answer_revision_id: int | str
    rubric_version: str
    judge_version: str
    criterion_scores: list[CriterionScore] = Field(min_length=1, max_length=20)
    critical_errors: list[DetectedError] = Field(max_length=30)
    strengths: list[str] = Field(max_length=30)
    improvements: list[str] = Field(max_length=30)


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _validated(model, value, category):
    try:
        return model.model_validate(value).model_dump()
    except (ValidationError, TypeError, ValueError):
        raise AssessmentJudgeError(category, '评分标准或评分结果结构不完整、字段类型不合法；未生成有效成绩') from None


def rubric_version(rubric):
    value = _validated(Rubric, {**rubric, 'rubric_version': 'pending'}, 'INVALID_RUBRIC')
    value.pop('rubric_version')
    return 'rubric-v2:' + _hash(value)[:32]


def validate_rubric(rubric):
    value = _validated(Rubric, rubric, 'INVALID_RUBRIC')
    ids = [c['criterion_id'] for c in value['criteria']]
    titles = [c['title'].strip() for c in value['criteria']]
    valid = (len(ids) == len(set(ids)) and len(titles) == len(set(titles)) and all(titles)
             and sum(c['weight'] for c in value['criteria']) == 100)
    for c in value['criteria']:
        valid = valid and [a['score'] for a in c['score_anchors']] == [0, 1, 2, 3, 4]
        valid = valid and [a['anchor_id'] for a in c['score_anchors']] == [f"{c['criterion_id']}:{n}" for n in range(5)]
        valid = valid and all(e.strip() for e in c['required_evidence'])
    error_ids = [e['error_id'] for e in value['critical_errors']]
    valid = valid and len(error_ids) == len(set(error_ids))
    for error in value['critical_errors']:
        valid = valid and (error['cap_score'] is not None or error['cap_overall'] is not None)
        valid = valid and (error['criterion_id'] is None or error['criterion_id'] in ids)
        valid = valid and (error['cap_score'] is None or error['criterion_id'] in ids)
    sections = value['reference_standard'].get('sections', [])
    if not isinstance(sections, list):
        raise AssessmentJudgeError('INVALID_RUBRIC', '参考答案章节必须是可关联评分项的列表')
    for section in sections:
        valid = valid and isinstance(section, dict) and bool(section.get('text'))
        if isinstance(section, dict):
            references = section.get('criterion_ids')
            valid = valid and isinstance(references, list) and bool(references)
            valid = valid and all(isinstance(cid, str) and cid in ids for cid in references)
    if not valid or value['rubric_version'] != rubric_version(value):
        raise AssessmentJudgeError('INVALID_RUBRIC', '评分标准版本、权重、锚点、预设上限或参考答案关联无效')
    return value


def compile_rubric(rubric, *, question_id, title, skill_ids=None, role_level=None, reference_answer=None):
    if not isinstance(rubric, dict):
        raise AssessmentJudgeError('INVALID_RUBRIC', '缺少明确评分标准，不能临时使用通用标准')
    if rubric.get('schema_version') == 2:
        value = validate_rubric(rubric)
        if value['question_id'] != question_id:
            raise AssessmentJudgeError('INVALID_RUBRIC', '评分标准不属于本题')
        return value
    original = deepcopy(rubric)
    points = original.get('points')
    maximum = original.get('max_points', 5.0)
    if (not isinstance(points, list) or not 1 <= len(points) <= 20 or isinstance(maximum, bool)
            or not isinstance(maximum, (float, int)) or not math.isfinite(maximum) or maximum <= 0):
        raise AssessmentJudgeError('INVALID_RUBRIC', '旧题没有可明确映射的评分项，需先补充评分标准')
    criteria = []
    for index, point in enumerate(points, 1):
        label = point.get('title') if isinstance(point, dict) else point
        detail = point.get('detail') or '' if isinstance(point, dict) else ''
        if not isinstance(label, str) or not label.strip() or not isinstance(detail, str):
            raise AssessmentJudgeError('INVALID_RUBRIC', '旧评分项缺少明确标题或说明')
        label, detail = label.strip(), detail.strip()
        objective = detail or f'围绕题目“{title}”解释“{label}”的含义与作用；原评分项未给出更具体要求'
        descriptions = [f'未回答“{label}”，或明确描述与该概念相反的机制。',
            f'仅提及“{label}”术语或结论，未解释具体机制。',
            f'对“{label}”有部分正确说明，但尚未完整交代：{objective}',
            f'正确且清晰地交代：{objective}；不能用术语出现代替解释。',
            f'满足3档，并就“{label}”说明适用边界、替代方案或可验证的检查方法。']
        cid = f'c{index}'
        criteria.append({'criterion_id': cid, 'title': label, 'dimension': 'legacy_criterion',
            'weight': 100 // len(points) + (1 if index <= 100 % len(points) else 0),
            'score_anchors': [{'anchor_id': f'{cid}:{n}', 'score': n, 'description': d} for n, d in enumerate(descriptions)],
            'required_evidence': [objective], 'acceptable_alternatives': ['逻辑成立、满足相同目标的等价方案；不强制特定产品或术语'],
            'common_misconceptions': ['仅说出关键词不等于解释了机制']})
    value = {'schema_version': 2, 'question_id': question_id,
        'question_version': 'question:' + _hash({'id': question_id, 'title': title, 'rubric': original, 'reference': reference_answer})[:32],
        'rubric_version': 'pending', 'target_skill_ids': list(skill_ids or []),
        'assessed_behaviors': [c['required_evidence'][0] for c in criteria],
        'role_level': role_level or '未提供岗位层级', 'role_standard_version': 'legacy-unspecified',
        'question_type': 'legacy_open', 'max_points': float(maximum), 'criteria': criteria, 'critical_errors': [],
        'calibration_status': 'LEGACY_UNCALIBRATED',
        'coverage_gaps': ['旧评分项确定性转为等权逐项锚点，未人工审定题目覆盖、岗位层级或档位。不能据此声称测评已校准。'],
        'reference_standard': {'legacy_reference': reference_answer or original.get('answer_html') or '',
                               'alignment_status': 'NOT_REVIEWED', 'sections': []}}
    value['rubric_version'] = rubric_version(value)
    return validate_rubric(value)


def _quotes(values, text, *, required=False):
    if required and not values:
        raise AssessmentJudgeError('INVALID_QUOTE', '得分或关键错误缺少原答直接引用')
    if any(not quote.strip() or quote not in text for quote in values):
        raise AssessmentJudgeError('INVALID_QUOTE', '评分引用不属于本次原始答案，未生成有效成绩')


def validate_judgement(raw, *, rubric, text, question_id, answer_revision_id):
    rubric = validate_rubric(rubric)
    value = _validated(Judgement, raw, 'INVALID_SCHEMA')
    if (value['question_id'] != question_id or rubric['question_id'] != question_id
            or value['answer_revision_id'] != answer_revision_id or value['rubric_version'] != rubric['rubric_version']
            or value['judge_version'] != JUDGE_VERSION):
        raise AssessmentJudgeError('INVALID_SCHEMA', '评分结果不匹配本次题目、答案或评分标准版本')
    criteria = {c['criterion_id']: c for c in rubric['criteria']}
    rows = value['criterion_scores']
    ids = [r['criterion_id'] for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(criteria):
        raise AssessmentJudgeError('INVALID_SCHEMA', '评分项遗漏、重复或超出冻结标准')
    for row in rows:
        _quotes(row['answer_quotes'], text, required=row['score'] > 0)
        expected_anchor = f"{row['criterion_id']}:{row['score']}"
        if row['satisfied_anchors'] != [expected_anchor]:
            raise AssessmentJudgeError('INVALID_SCHEMA', '评分档位与引用的冻结锚点不一致')
        if row['score'] < 4 and not row['missing_points']:
            raise AssessmentJudgeError('INVALID_SCHEMA', '未满分的评分项必须说明具体缺口')
        row['judge_score'] = row['score']
    presets = {e['error_id']: e for e in rubric['critical_errors']}
    detected_ids = [e['error_id'] for e in value['critical_errors']]
    if len(detected_ids) != len(set(detected_ids)) or not set(detected_ids) <= set(presets):
        raise AssessmentJudgeError('INVALID_SCHEMA', '关键错误未在冻结标准中预设或重复报告')
    total_cap = Decimal(100)
    for detected in value['critical_errors']:
        _quotes(detected['answer_quotes'], text, required=True)
        preset = presets[detected['error_id']]
        if preset['cap_score'] is not None:
            row = next(r for r in rows if r['criterion_id'] == preset['criterion_id'])
            row['score'] = min(row['score'], preset['cap_score'])
        if preset['cap_overall'] is not None:
            total_cap = min(total_cap, Decimal(preset['cap_overall']))
        detected['applied_cap'] = {k: preset[k] for k in ('criterion_id', 'cap_score', 'cap_overall')}
    percent = min(total_cap, sum(Decimal(r['score']) * Decimal(criteria[r['criterion_id']]['weight']) / 4 for r in rows))
    maximum = Decimal(str(rubric['max_points']))
    # Keep the calculation precise. Rounding is a display concern and must not
    # raise a score above a preset cap or change the percentage used by Evidence.
    points = float(percent * maximum / 100)
    reasoning = '\n'.join(f"{criteria[r['criterion_id']]['title']}：{r['reasoning_summary']} {r['why_not_higher']}" for r in rows)
    return {**value, 'question_version': rubric['question_version'], 'points': points, 'max_points': float(maximum),
        'overall_score': float(percent), 'verdict': 'CORRECT' if percent >= 80 else 'PARTIAL' if percent >= 40 else 'WRONG',
        'hit': [criteria[r['criterion_id']]['title'] for r in rows if r['score'] >= 3],
        'missing': [criteria[r['criterion_id']]['title'] for r in rows if r['score'] < 3], 'reasoning': reasoning,
        'confidence_status': 'UNCALIBRATED', 'needs_human_review': rubric['calibration_status'] != 'HUMAN_REVIEWED',
        'validation_status': 'VALID', 'calibration_status': rubric['calibration_status']}
