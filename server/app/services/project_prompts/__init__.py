"""Versioned runtime prompts for the recoverable project pipeline."""
from app.generation_i18n import generation_message, with_output_language
from app.i18n import Locale

PROMPT_BUNDLE_VERSION = 'project-autopilot-prompts-v3'


def system_prompt(task: str, language: Locale = 'zh-CN') -> str:
    system = generation_message('project.prompt.system', language)
    instruction = generation_message(f'project.prompt.{task}', language)
    return with_output_language(f'{system}\n{instruction}', language)
