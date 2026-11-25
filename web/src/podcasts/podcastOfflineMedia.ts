import type { PodcastEpisode } from '../api/podcasts';
import { identityIsCurrent, type PodcastIdentity } from './podcastOfflineIdentity';
import type { PlaybackLibrary } from './podcastQueue';
import { readPodcastWorkspace, readSavedMedia } from './podcastStore';
import { safeFileName, zipMp3Files } from './podcastZip';

export const offlinePodcastUrl = '/podcast-offline.html';
export const podcastPacksChanged = 'careerpilot-podcast-packs-changed';
export const podcastLibraryChanged = 'careerpilot-podcast-library-changed';

export function readyEpisodes(episodes: PodcastEpisode[]) {
  return [...new Map(episodes.filter(episode => episode.status === 'ready').map(episode => [episode.id, episode])).values()];
}

export async function offlineLibrary(identity: PodcastIdentity): Promise<PlaybackLibrary> {
  const { packs, overrides } = await readPodcastWorkspace(identity);
  const episodes = new Map<number, PodcastEpisode>();
  for (const id of new Set(packs.flatMap(pack => pack.episodeIds))) {
    const media = await readSavedMedia(identity, id);
    if (media?.audio.size) episodes.set(id, { ...media.episode, ...overrides[id] });
  }
  return {
    episodes: [...episodes.values()],
    playlists: packs.flatMap(pack => pack.episodeIds.every(id => episodes.has(id))
      ? [{ id: pack.id, name: pack.name, episode_ids: pack.episodeIds }] : []),
  };
}

export async function exportOfflinePlaylist(identity: PodcastIdentity, episodes: PodcastEpisode[], name: string) {
  await exportPodcastMedia(episodes, name, async id => {
    const media = await readSavedMedia(identity, id);
    if (!media) throw new Error('未找到已保存的节目');
    return media.audio;
  }, () => {
    if (!identityIsCurrent(identity)) throw new Error('工作区已切换');
  });
}

export async function exportPodcastMedia(
  episodes: PodcastEpisode[], name: string, loadAudio: (id: number) => Promise<Blob>, assertCurrent: () => void,
) {
  const ready = readyEpisodes(episodes);
  if (!ready.length) throw new Error('没有可导出的 MP3');
  const files: { name: string; audio: Blob }[] = [];
  const digits = Math.max(2, String(ready.length).length);
  for (const episode of ready) {
    assertCurrent();
    const audio = await loadAudio(episode.id);
    if (!audio.size) throw new Error('节目音频为空，请重试');
    const prefix = ready.length > 1 ? String(files.length + 1).padStart(digits, '0') + ' - ' : '';
    files.push({ name: prefix + safeFileName(episode.title) + '.mp3', audio });
  }
  const audio = files.length === 1 ? files[0].audio : await zipMp3Files(files);
  assertCurrent();
  const url = URL.createObjectURL(audio);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = files.length === 1 ? files[0].name : safeFileName(name) + '.zip';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30000);
}
