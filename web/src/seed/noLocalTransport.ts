export const workspaceLogin = false;

function unavailable(): never {
  throw new Error('Registered build requires a bound session');
}

export const localFetch: (path: string, options: RequestInit) => Promise<Response> = unavailable;
export const selectLocalWorkspace: (slug: string) => void = unavailable;
export const localWorkspace: () => string = unavailable;
