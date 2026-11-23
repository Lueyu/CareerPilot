"""Resolve model configuration, repository credentials and source-transfer consent."""
from app.config import get_settings
from app.models import AppSetting
from app.services import study_routing
from app.services.project_jobs import fingerprint
from app.services.repository_credentials import repository_credentials

CONSENT_VERSION='source-analysis-v1'
DEFAULT_BUDGET={}
SOURCE_NOTICE='启用后，本空间已导入但尚无报告的仓库，以及以后新导入的仓库，会逐个自动分析；必要源码片段将发送给你当前配置的模型服务。不会执行仓库代码；报告自动保存，长期记忆需你确认。默认不限累计调用次数和读取字节；如设置单次分析预算，达到上限时暂停。'

async def settings_for(session,workspace_id):
    row=await session.get(AppSetting,f'project-autopilot:{workspace_id}')
    return {'enabled':False,'consent_version':None,'consented_at':None,'budget':dict(DEFAULT_BUDGET),
        'check_interval_hours':6,**(row.value if row else {})}

async def runtime_config(session,workspace_id,require_consent=True):
    consent=await settings_for(session,workspace_id)
    if require_consent and (not consent['enabled'] or consent['consent_version']!=CONSENT_VERSION):
        raise PermissionError('请先确认将此项目的必要源码片段发送给模型服务')
    env=get_settings()
    # Source-transfer consent governs model use. Repository access has its own boundary.
    row=await session.get(AppSetting,f'llm:{workspace_id}') or await session.get(AppSetting,'llm')
    config={'provider':env.llm_provider or '', 'api_key':env.llm_api_key or '',
        'base_url':env.llm_base_url or '', 'model':env.llm_model or ''}
    if row:config.update({k:v for k,v in row.value.items() if k in config and v})
    config['auto_profiles'] = {tier: study_routing.profile(config, tier) for tier in ('flash', 'strong')}
    credentials=await repository_credentials(session,workspace_id)
    return config,credentials,consent

def model_fingerprint(config):
    # Credentials hashed into the hash only, never stored or returned.
    fields = {k:config.get(k) for k in ('provider','base_url','model','api_key','reasoning_effort')}
    if 'auto_profiles' in config:
        fields['auto_profiles'] = config['auto_profiles']
    return fingerprint(fields)
