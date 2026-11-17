"""Workspace consent gates access to this local installation's provider config."""
from sqlalchemy import select
from app.models import AppSetting
from app.config import get_settings
from app.services.project_jobs import fingerprint

CONSENT_VERSION='source-analysis-v1'
DEFAULT_BUDGET={'max_model_calls':32,'max_source_bytes':8388608}
SOURCE_NOTICE='启用后，本空间已导入但尚无报告的仓库，以及以后新导入的仓库，会逐个自动分析；必要源码片段将发送给你当前配置的模型服务。不会执行仓库代码；报告自动保存，长期记忆需你确认。预算按每个项目的单次分析计算，达到上限时暂停。'

async def settings_for(session,workspace_id):
    row=await session.get(AppSetting,f'project-autopilot:{workspace_id}')
    return {'enabled':False,'consent_version':None,'consented_at':None,'budget':dict(DEFAULT_BUDGET),
        'check_interval_hours':6,**(row.value if row else {})}

async def runtime_config(session,workspace_id,require_consent=True):
    consent=await settings_for(session,workspace_id)
    if require_consent and (not consent['enabled'] or consent['consent_version']!=CONSENT_VERSION):
        raise PermissionError('尚未确认自动分析的源码传输范围与预算')
    env=get_settings()
    # Existing local-first installation has one owner's global provider. Workspace
    # consent is required before borrowing it; no other workspace settings/cache
    # are queried. A scoped override is supported without changing other tools.
    row=await session.get(AppSetting,f'llm:{workspace_id}') or await session.get(AppSetting,'llm')
    config={'provider':env.llm_provider or '', 'api_key':env.llm_api_key or '',
        'base_url':env.llm_base_url or '', 'model':env.llm_model or ''}
    if row:config.update({k:v for k,v in row.value.items() if k in config and v})
    credentials={}
    for provider in ('github','gitcode'):
        token_row=await session.get(AppSetting,f'{provider}:{workspace_id}') or await session.get(AppSetting,provider)
        credentials[provider+'_token']=(token_row.value.get('token') if token_row else None) or getattr(env,provider+'_token','') or ''
    return config,credentials,consent

def model_fingerprint(config):
    # Credentials hashed into the hash only, never stored or returned.
    return fingerprint({k:config.get(k) for k in ('provider','base_url','model','api_key','reasoning_effort')})
