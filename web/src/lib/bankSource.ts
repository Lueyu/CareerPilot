import { t } from '../i18n';
export const PRIVATE_COMPANY_FALLBACK = '个人题库';

/** Presentation and edit affordances follow the persisted source, not company text. */
export function bankSourcePresentation(question:{source_type:string;company?:string|null}) {
  const isPrivate=question.source_type==='USER_PRIVATE' || question.source_type==='GENERATED';
  return {
    company:question.company?.trim() || (isPrivate?t('bank.personalBank'):t('bank.publicBank')),
    isPrivate,
    editable:isPrivate,
    label:question.source_type==='GENERATED'?t('bank.modelGenerated'):isPrivate?t('bank.personalImport'):t('bank.sharedReadOnly'),
  };
}
