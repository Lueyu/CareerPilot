import type { PodcastEpisode } from '../api/podcasts';
import { identityIsCurrent, podcastStorageScope, type PodcastIdentity } from './podcastOfflineIdentity';

export type EpisodePatch = Partial<Pick<PodcastEpisode, 'familiarity' | 'position_seconds'>>;
export type SavedMedia = { episode: PodcastEpisode; audio: Blob; script: string };
export type OfflinePlaylist = { id: string; name: string; episodeIds: number[] };
export type Resume = { id: number; position: number };
type ResumeCheckpoint = Resume & { updatedAt: number; sendPosition: boolean };
type PendingPatch = { version: string; patch: EpisodePatch };
type WorkspaceRecord = {
  packs: OfflinePlaylist[];
  pending: Record<string, PendingPatch>;
  overrides: Record<string, EpisodePatch>;
  resume?: ResumeCheckpoint;
};

let dbPromise: Promise<IDBDatabase> | undefined;
function database() {
  if (!dbPromise) {
    dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('careerpilot-podcasts', 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore('workspaces');
        request.result.createObjectStore('media');
      };
      request.onsuccess = () => {
        request.result.onversionchange = () => { request.result.close(); dbPromise = undefined; };
        resolve(request.result);
      };
      request.onerror = () => reject(request.error);
    }).catch(error => { dbPromise = undefined; throw error; });
  }
  return dbPromise;
}

function assertCurrent(identity: PodcastIdentity) {
  if (!identityIsCurrent(identity)) throw new Error('工作区已切换');
}

function resumeKey(identity: PodcastIdentity) {
  return 'careerpilot-podcast-resume:' + podcastStorageScope(identity);
}

export function readPodcastResume(identity: PodcastIdentity): ResumeCheckpoint | undefined {
  try {
    assertCurrent(identity);
    const stored: unknown = JSON.parse(localStorage.getItem(resumeKey(identity)) ?? 'null');
    if (!stored || typeof stored !== 'object' || !('id' in stored) || typeof stored.id !== 'number' ||
        !Number.isSafeInteger(stored.id) || !('position' in stored) || typeof stored.position !== 'number' ||
        !Number.isFinite(stored.position) || stored.position < 0 || !('updatedAt' in stored) ||
        typeof stored.updatedAt !== 'number' || !Number.isFinite(stored.updatedAt) || !('sendPosition' in stored) ||
        typeof stored.sendPosition !== 'boolean') return undefined;
    return { id: stored.id, position: stored.position, updatedAt: stored.updatedAt, sendPosition: stored.sendPosition };
  } catch {
    return undefined;
  }
}

export function rememberPodcastResume(identity: PodcastIdentity, resume: Resume & { sendPosition: boolean }) {
  const previous = readPodcastResume(identity)?.updatedAt ?? 0;
  const checkpoint = { ...resume, updatedAt: Math.max(Date.now(), previous + 1) };
  try {
    assertCurrent(identity);
    localStorage.setItem(resumeKey(identity), JSON.stringify(checkpoint));
  } catch { /* IndexedDB still records the checkpoint where supported. */ }
  return checkpoint;
}

export function clearPodcastResume(identity: PodcastIdentity, id: number) {
  try {
    if (readPodcastResume(identity)?.id === id) localStorage.removeItem(resumeKey(identity));
  } catch { /* Still clear the IndexedDB checkpoint when local storage is unavailable. */ }
  return updateWorkspace(identity, record => {
    if (record.resume?.id === id) delete record.resume;
  });
}

function emptyWorkspace(): WorkspaceRecord {
  return { packs: [], pending: {}, overrides: {} };
}

function read<T>(store: IDBObjectStore, key: IDBValidKey) {
  return new Promise<T | undefined>((resolve, reject) => {
    const request = store.get(key);
    request.onsuccess = () => resolve(request.result as T | undefined);
    request.onerror = () => reject(request.error);
  });
}

async function updateWorkspace(
  identity: PodcastIdentity,
  mutate: (record: WorkspaceRecord, transaction: IDBTransaction, scope: string) => void,
  withMedia = false,
) {
  const db = await database();
  assertCurrent(identity);
  const scope = podcastStorageScope(identity);
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(withMedia ? ['workspaces', 'media'] : 'workspaces', 'readwrite');
    const store = transaction.objectStore('workspaces');
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error ?? new Error('无法保存播客数据'));
    const request = store.get(scope);
    request.onsuccess = () => {
      try {
        assertCurrent(identity);
        const record: WorkspaceRecord = request.result ?? emptyWorkspace();
        mutate(record, transaction, scope);
        store.put(record, scope);
      } catch (error) {
        transaction.abort();
        reject(error);
      }
    };
  });
  assertCurrent(identity);
}

export async function readPodcastWorkspace(identity: PodcastIdentity) {
  assertCurrent(identity);
  const db = await database();
  const workspace = await read<WorkspaceRecord>(
    db.transaction('workspaces').objectStore('workspaces'), podcastStorageScope(identity),
  );
  assertCurrent(identity);
  return workspace ?? emptyWorkspace();
}

export async function readSavedMedia(identity: PodcastIdentity, id: number) {
  assertCurrent(identity);
  const db = await database();
  const media = await read<SavedMedia>(
    db.transaction('media').objectStore('media'), [podcastStorageScope(identity), id],
  );
  assertCurrent(identity);
  return media;
}

export async function savePodcastPack(identity: PodcastIdentity, playlist: OfflinePlaylist, media: SavedMedia[]) {
  await updateWorkspace(identity, (record, transaction, scope) => {
    const store = transaction.objectStore('media');
    const replaced = record.packs.find(pack => pack.id === playlist.id);
    record.packs = [...record.packs.filter(pack => pack.id !== playlist.id), playlist];
    const used = new Set(record.packs.flatMap(pack => pack.episodeIds));
    replaced?.episodeIds.filter(id => !used.has(id)).forEach(id => store.delete([scope, id]));
    media.forEach(item => store.put(item, [scope, item.episode.id]));
  }, true);
}

export async function removePodcastPack(identity: PodcastIdentity, id: string) {
  await updateWorkspace(identity, (record, transaction, scope) => {
    const pack = record.packs.find(item => item.id === id);
    if (!pack) return;
    record.packs = record.packs.filter(item => item.id !== id);
    const used = new Set(record.packs.flatMap(item => item.episodeIds));
    const store = transaction.objectStore('media');
    pack.episodeIds.filter(episodeId => !used.has(episodeId)).forEach(episodeId => store.delete([scope, episodeId]));
  }, true);
}

export async function savePodcastChange(identity: PodcastIdentity, id: number, patch: EpisodePatch, resume?: ResumeCheckpoint) {
  await updateWorkspace(identity, record => {
    if (resume && resume.updatedAt >= (record.resume?.updatedAt ?? 0)) record.resume = resume;
    if (!Object.keys(patch).length) return;
    record.pending[id] = { version: crypto.randomUUID(), patch: { ...record.pending[id]?.patch, ...patch } };
    record.overrides[id] = { ...record.overrides[id], ...patch };
  });
}

export async function flushPodcastChanges(
  identity: PodcastIdentity,
  signal: AbortSignal,
  send: (id: number, patch: EpisodePatch, signal: AbortSignal) => Promise<unknown>,
) {
  const sync = async () => {
    let sent = 0;
    let pending = (await readPodcastWorkspace(identity)).pending;
    while (Object.keys(pending).length) {
      for (const [id, { version, patch }] of Object.entries(pending)) {
        signal.throwIfAborted();
        assertCurrent(identity);
        try {
          await send(Number(id), patch, signal);
          sent++;
        } catch (error) {
          if (!(error instanceof Error && 'status' in error && error.status === 404)) throw error;
        }
        signal.throwIfAborted();
        await updateWorkspace(identity, record => {
          if (record.pending[id]?.version === version) delete record.pending[id];
        });
      }
      pending = (await readPodcastWorkspace(identity)).pending;
    }
    return sent;
  };
  // Keep the writes ordered even when two authenticated tabs come online together.
  if (navigator.locks) {
    return navigator.locks.request('careerpilot-podcast-sync:' + podcastStorageScope(identity), { signal }, sync);
  }
  return sync();
}
