"""Role-scoped benchmark baseline for single-workspace native tenant servers."""

import os
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models import JobStandard, JobStandardTarget, Skill
from app.services.public_library_presets import preset_directions


def uses_tenant_standard(ws):
    return "TENANT_DISPLAY_ROLE" in os.environ and not ws.is_demo and ws.slug.startswith("seed-")


async def tenant_standard(session, ws, *, persist_seed=False):
    role = ws.target_role.strip() or "未选择岗位"
    query = (
        select(JobStandard)
        .where(JobStandard.role == role)
        .order_by(JobStandard.is_seed.asc(), JobStandard.id.desc())
        .limit(1)
    )
    existing = await session.scalar(query)
    if existing is not None:
        return existing

    names = list(dict.fromkeys(item["name"] for item in preset_directions(role)))
    keys = ["role-" + sha256(name.encode()).hexdigest()[:32] for name in names]
    await session.execute(
        insert(Skill).values([{"key": key, "name": name} for key, name in zip(keys, names)])
        .on_conflict_do_nothing(index_elements=[Skill.key])
    )
    ids = dict((await session.execute(select(Skill.key, Skill.id).where(Skill.key.in_(keys)))).all())
    standard_id = await session.scalar(
        insert(JobStandard)
        .values(role=role, level=ws.target_level, version="role-seed-v1", jd_sample_count=0, is_seed=True)
        .on_conflict_do_nothing(index_elements=[JobStandard.role, JobStandard.level, JobStandard.version])
        .returning(JobStandard.id)
    )
    if standard_id is not None:
        weight, remainder = divmod(100, len(keys))
        await session.execute(insert(JobStandardTarget).values([
            {"standard_id": standard_id, "skill_id": ids[key], "weight_pct": weight + (index < remainder),
             "target_level": 3.0}
            for index, key in enumerate(keys)
        ]))
    standard = await session.scalar(query)
    if persist_seed:
        await session.commit()
    return standard
