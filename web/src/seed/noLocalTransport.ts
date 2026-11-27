import { t } from '../i18n';

export const workspaceLogin = false;
export const requiresLocalLogin = false;

function unavailable(): never {
  throw new Error(t('registered.workspaceSessionRequired'));
}

export const localFetch: (path: string, options: RequestInit) => Promise<Response> = unavailable;
export const selectLocalWorkspace: (slug: string) => void = unavailable;
export const localWorkspace: () => string = unavailable;
