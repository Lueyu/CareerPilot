import type { ImportReview, SourceReview } from '../api/client';
import { useT } from '../i18n';

export function importSourceText(meta: ImportReview['meta']): string {
  return meta?.extracted_source_text ?? meta?.pasted_text ?? '';
}

const reasonKeys: Record<SourceReview['flags'][number]['reason'], string> = {
  instruction_override: 'bank.sourceInstructionOverride',
  role_spoofing: 'bank.sourceRoleSpoofing',
  delimiter_closure: 'bank.sourceDelimiterClosure',
  workspace_or_approval: 'bank.sourceWorkspaceOrApproval',
  secret_request: 'bank.sourceSecretRequest',
};

export default function ImportSourceReview({ review }: { review?: SourceReview | null }) {
  const t = useT();
  if (!review?.flags.length) return null;
  return (
    <aside role="note">
      <p>{t('bank.sourceReviewWarning')}</p>
      <ul>
        {review.flags.map((flag, index) => (
          <li key={index}>
            {t('bank.sourceReviewFinding', { source: flag.source, reason: t(reasonKeys[flag.reason]) })}
          </li>
        ))}
      </ul>
      {review.truncated && <p>{t('bank.sourceReviewTruncated')}</p>}
    </aside>
  );
}
