"""Built-in directions use stable topic names in existing workspaces."""
from app.i18n import message


PRESETS = (
    (('运营', '增长', '营销', 'operations', 'marketing', 'growth'), ('用户增长', '活动复盘', '用户分层')),
    (('后端', '服务端', 'backend', 'java'), ('接口与数据一致性', '故障排查', '系统设计')),
    (('前端', 'frontend', '客户端'), ('交互状态', '页面性能', '可访问性')),
    (('产品', 'product'), ('需求优先级', '指标设计', '方案验证')),
    (('数据', '分析', 'data'), ('指标诊断', '实验设计', '数据质量')),
    (('销售', '商务', 'sales'), ('需求挖掘', '机会管理', '异议处理')),
    (('设计', 'designer'), ('设计取舍', '设计评审', '可用性测试')),
    (('教师', '老师', '讲师', '教学', 'teacher', 'educator', 'instructor'), ('教学设计', '学习反馈', '沟通协作')),
)
DEFAULT_TOPICS = ('岗位任务', '现场判断', '成果复盘')
EXAMPLE_KEYS = {
    "用户增长": "preset.userGrowth",
    "活动复盘": "preset.campaignReview",
    "用户分层": "preset.userSegmentation",
    "接口与数据一致性": "preset.apiConsistency",
    "故障排查": "preset.troubleshooting",
    "系统设计": "preset.systemDesign",
    "交互状态": "preset.interactionState",
    "页面性能": "preset.pagePerformance",
    "可访问性": "preset.accessibility",
    "需求优先级": "preset.prioritization",
    "指标设计": "preset.metricDesign",
    "方案验证": "preset.solutionValidation",
    "指标诊断": "preset.metricDiagnosis",
    "实验设计": "preset.experimentDesign",
    "数据质量": "preset.dataQuality",
    "需求挖掘": "preset.customerDiscovery",
    "机会管理": "preset.salesOpportunity",
    "异议处理": "preset.salesObjections",
    "设计取舍": "preset.designTradeoffs",
    "设计评审": "preset.designReview",
    "可用性测试": "preset.usability",
    "教学设计": "preset.lessonPlanning",
    "学习反馈": "preset.learningFeedback",
    "沟通协作": "preset.collaboration",
    "岗位任务": "preset.roleResponsibilities",
    "现场判断": "preset.judgmentUnderPressure",
    "成果复盘": "preset.resultsReview",
}


def preset_directions(role: str) -> list[dict[str, str]]:
    topics = next((topics for terms, topics in PRESETS if any(term in role.casefold() for term in terms)),
                  DEFAULT_TOPICS)
    return [{"name": topic, "example": message(EXAMPLE_KEYS[topic], "zh-CN").format(role=role),
             "origin": "preset"} for topic in topics]


def example_titles(direction: dict, role: str) -> dict[str, str]:
    original = direction["example"]
    key = EXAMPLE_KEYS.get(direction["name"]) if direction.get("origin") == "preset" else None
    if key and original == message(key, "zh-CN").format(role=role):
        return {"zh-CN": original, "en": message(key, "en").format(role=role)}
    return {"zh-CN": original, "en": original}
