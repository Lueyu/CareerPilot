"""Resolve model configuration, repository credentials and source-transfer consent."""
from app.config import get_settings
from app.models import AppSetting
from app.services import study_routing
from app.services.llm_endpoint_config import resolve_llm_config
from app.services.project_jobs import fingerprint
from app.services.repository_credentials import repository_credentials
from app.services.secret_storage import open_setting

CONSENT_VERSION='source-analysis-v1'
DEFAULT_BUDGET={'max_model_calls': 40, 'max_source_bytes': 2_000_000}
SOURCE_NOTICE='启用后，本空间已导入但尚无报告的仓库，以及以后新导入的仓库，会逐个自动分析；必要源码片段将发送给你当前配置的模型服务。不会执行仓库代码；报告自动保存，长期记忆需你确认。单次分析默认最多 40 次模型调用、读取 200 万字节，达到上限时暂停；每次模型请求还受私有模型每日货币预算约束。'

async def settings_for(session,workspace_id):
    row=await session.get(AppSetting,f'project-autopilot:{workspace_id}')
    stored = row.value if row and isinstance(row.value, dict) else {}
    budget = {**DEFAULT_BUDGET, **(stored.get('budget') or {})}
    budget = {key: value if value is not None else DEFAULT_BUDGET.get(key) for key, value in budget.items()}
    return {'enabled':False,'consent_version':None,'consented_at':None,
        'check_interval_hours':6, **stored, 'budget': budget}

async def runtime_config(session,workspace_id,require_consent=True):
    from app.services.recovery_control import require_normal
    await require_normal(session)
    consent=await settings_for(session,workspace_id)
    if require_consent and (not consent['enabled'] or consent['consent_version']!=CONSENT_VERSION):
        raise PermissionError('请先确认将此项目的必要源码片段发送给模型服务')
    env=get_settings()
    # Source-transfer consent governs model use. Repository access has its own boundary.
    key = f'llm:{workspace_id}'
    row = await session.get(AppSetting, key)
    if row is None:
        key = 'llm'
        row = await session.get(AppSetting, key)
    config = resolve_llm_config(env, open_setting(key, row.value) if row else None)
    config.pop("source")
    config['auto_profiles'] = {tier: study_routing.profile(config, tier) for tier in ('flash', 'strong')}
    credentials=await repository_credentials(session,workspace_id)
    return config,credentials,consent

def model_fingerprint(config):
    # Credentials hashed into the hash only, never stored or returned.
    fields = {k:config.get(k) for k in ('provider','base_url','model','api_key','reasoning_effort')}
    if 'auto_profiles' in config:
        fields['auto_profiles'] = config['auto_profiles']
    return fingerprint(fields)
