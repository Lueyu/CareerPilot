"""Create only a fresh isolated workspace with four private bank scenarios on a running local installation."""
import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select
from app.models import Workspace, Skill, Question
from app.db import SessionLocal, engine

async def main():
    slug='accept-coach-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')+'-'+uuid4().hex[:6]
    async with SessionLocal() as s:
        ws=Workspace(slug=slug, name='验收专用 · 学习闭环',is_demo=False);s.add(ws);await s.flush()
        skill=await s.scalar(select(Skill).where(Skill.key=='rag')); assert skill
        for i,(context,k,h,t) in enumerate([('工单系统',8,4,10),('医疗手册',6,3,12),('备份帮助中心',10,2,8),('报销手册',4,2,5)]):
            detail=f'precision 等于 {h}/{k}，分母为返回条数；recall 等于 {h}/{t}，分母为标注的全部相关条数。'
            s.add(Question(workspace_id=ws.id,skill_id=skill.id,source_type='USER_PRIVATE',short_code=f'QA-{slug[-6:]}-{i}',topic='rag',qtype='open',tags=['RAG','召回','precision','recall','检索计量尺'],keywords='RAG 召回 precision recall 检索计量尺',title=f'{context}的 RAG 检索计量尺：返回 {k} 条，其中 {h} 条相关，标注共有 {t} 条相关。计算 precision、recall 并解释分母。',answer_html=detail,rubric={'max_points':5,'points':[{'title':'正确计算两项指标并解释分母','detail':detail}]}))
        await s.commit();print(json.dumps({'fixture_workspace':slug,'id':ws.id,'skill_id':skill.id}))
    await engine.dispose()
if __name__=='__main__':asyncio.run(main())
