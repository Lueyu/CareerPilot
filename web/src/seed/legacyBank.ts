import { api, hasOriginalBank } from '../api/client';

export function loadLegacyBank(): Promise<{ questions: { id: string; html?: string }[] }> {
  return hasOriginalBank() ? api.get('/bank/legacy-data') : Promise.resolve({ questions: [] });
}
