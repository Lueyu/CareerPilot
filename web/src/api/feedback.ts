export type FeedbackSubmission = { submission_id: string; message: string };
export type SendFeedback = (submission: FeedbackSubmission, signal: AbortSignal) => Promise<unknown>;

export function createFeedbackId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  // Local installations can also run over HTTP on a LAN.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
}

export function feedbackResult(status: number): string {
  switch (status) {
    case 401:
    case 403:
      return '当前会话无权发送反馈，请检查登录状态。';
    case 422:
      return '反馈未通过审核，请修改内容后重试。';
    case 429:
      return '已达到发送限额，请稍后重试。';
    case 409:
      return '暂时无法确认发送结果，请稍后用原文重试。';
    case 503:
      return '反馈服务暂不可用，请稍后重试。';
    default:
      return '无法确认反馈是否已发送，请稍后用原文重试。';
  }
}

export function feedbackWasSent(value: unknown): boolean {
  return typeof value === 'object' && value !== null && 'status' in value && value.status === 'sent';
}

const repository = 'https://github.com/Lueyu/CareerPilot';
export const feedbackForkUrl = repository + '/fork';

export function feedbackCompareUrl(owner: string, branch: string): string | undefined {
  if (!/^[a-z\d]+(?:-[a-z\d]+)*$/i.test(owner) || owner.length > 39) return;
  if (!branch || branch === '@' || branch.startsWith('-') || branch === 'HEAD' || branch.endsWith('.') ||
      /[~^:?*[\\]|\.\.|@\{/.test(branch) ||
      Array.from(branch).some(character => character <= ' ' || character === '\x7f') ||
      branch.split('/').some(part => !part || part.startsWith('.') || part.endsWith('.lock'))) return;
  try {
    const head = encodeURIComponent(branch);
    const ref = owner.toLowerCase() === 'lueyu' ? head : encodeURIComponent(owner) + ':' + head;
    return repository + '/compare/dev...' + ref + '?expand=1';
  } catch {
    return undefined;
  }
}
