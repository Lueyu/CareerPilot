from datetime import date

from app.services.agent_sprint_curriculum import sprint_blocks
from app.services.daily_learning_plan import PlanItem, build_plan as schedule_plan

DEFAULT_DEADLINE = date(2027, 2, 1)

MODULES = (
    ('baseline', '起点与目标', 'Agent、工作流与普通对话', '给同一个客服请求画三种处理流程，指出哪一步需要模型决策'),
    ('python', '基础', 'Python、HTTP 与 JSON', '写一个读入 JSON 并请求模拟服务的小程序，展示正常和错误响应'),
    ('llm', '基础', '消息结构与结构化输出', '写出系统、用户、工具三类消息，验证缺字段与类型错误会被拒绝'),
    ('stream', '基础', '流式输出与取消', '演示收到首个响应片段、用户主动取消和正常结束三种情况，记录各自的界面状态'),
    ('tools', '基础', '工具调用与执行循环', '实现工具调用循环，展示成功、工具失败和达到步数上限的记录'),
    ('workflow', '动手构建', '状态、分支与人工确认', '画出状态迁移图，演示流程暂停后恢复且副作用只执行一次'),
    ('rag', '动手构建', '检索与引用', '做一个文档问答流程，展示有来源、无来源和来源冲突三个用例'),
    ('context', '动手构建', '上下文与记忆', '制定上下文保留规则，用对话回放演示旧信息失效后如何处理'),
    ('persist', '动手构建', '运行状态与持久化', '记录一次执行的输入、步骤和结果，在模拟中断后从正确的步骤恢复'),
    ('eval', '工程进阶', '评估与可观测', '准备五个测试用例，记录成功率、失败类别和调整前后的对比'),
    ('async', '工程进阶', '异步任务与并发限制', '用两种负载运行模拟任务队列，记录并发上限、积压和任务超时行为'),
    ('reliability', '工程进阶', '超时、重试与幂等', '模拟超时或重复提交，展示操作不会重复落库并记录重试上限'),
    ('safety', '工程进阶', '权限与运行边界', '写一份权限表，演示拒绝、人工确认和通过三个分支'),
    ('cost', '工程进阶', '模型选型、延迟与成本', '对两个模型配置制定相同的测试集，写出质量、延迟和预算的取舍依据'),
    ('portfolio', '面试演练', '项目演示与可复现证据', '整理一个可运行的最小演示，记录运行命令、预期输出及一个已知限制'),
    ('design', '面试演练', '端到端 Agent 系统设计', '在白板上给出执行、数据、评估和故障恢复流程，解释两项取舍'),
    ('incident', '面试演练', '故障复盘与深挖追问', '把一次模拟故障讲成可验证的复盘，说明现象、证据、修复和预防指标'),
    ('interview', '面试演练', '项目讲述与模拟面试', '完成一次限时模拟面试，复盘两项失分并写出改进答案'),
)


def curriculum() -> list[PlanItem]:
    result = []
    for key, stage, topic, artifact in MODULES:
        drills = (
            ('explain', '理解', 20, f'用自己的话解释「{topic}」，给出一个适用及一个不适用的场景。'),
            ('practice', '实践', 35, f'{artifact}。保存代码、流程图或答题记录。'),
            ('transfer', '迁移', 25, f'换一个业务场景独立应用「{topic}」，说明一项权衡和一条验证方法。'),
            ('review', '回顾', 20, f'不看资料，用 2 分钟回答「{topic}」的面试提问并记录一条待改进之处。'),
        )
        for suffix, label, minutes, criteria in drills:
            result.append(PlanItem(f'{key}:{suffix}', f'{label}：{topic}', stage, 'curriculum', minutes, criteria))
    return result


def build_plan(state: dict, today: date, existing: list[PlanItem], *, reschedule: bool = False,
               capture_baseline: bool = False) -> dict:
    return schedule_plan(state, today, existing, reschedule=reschedule, capture_baseline=capture_baseline,
                         curriculum=curriculum(), sprint=sprint_blocks())
