import { api, downloadNativeBlob } from './client';

export type Familiarity = 'unmarked' | 'familiar' | 'unfamiliar';
export type EpisodeStatus = 'generating' | 'draft' | 'synthesizing' | 'ready' | 'script_failed' | 'audio_failed';

export interface PodcastSource {
  id: number;
  question_code: string;
  title: string;
  topic: string;
}

export interface PodcastEpisode extends PodcastSource {
  question_id: number;
  part: number;
  status: EpisodeStatus;
  duration_seconds: number;
  position_seconds: number;
  familiarity: Familiarity;
  error: string | null;
}

export interface PodcastPlaylist {
  id: number;
  name: string;
  episode_ids: number[];
}

export interface PodcastLibrary {
  episodes: PodcastEpisode[];
  playlists: PodcastPlaylist[];
}

const root = '/podcasts';
const episode = (id: number) => root + '/episodes/' + id;
const playlist = (id: number) => root + '/playlists/' + id;

export const podcastApi = {
  library: () => api.get<PodcastLibrary>(root),
  sources(q = '', offset = 0, limit = 40) {
    const params = new URLSearchParams({ q, offset: String(offset), limit: String(limit) });
    return api.get<{ items: PodcastSource[]; total: number }>(root + '/sources?' + params);
  },
  importQuestions(question_ids: number[], series: boolean, playlist_id?: number) {
    return api.post<{ episode_ids: number[]; skipped_question_ids?: number[] }>(
      root + '/import', { question_ids, series, playlist_id },
    );
  },
  script: (id: number) => api.get<{ title: string; script: string }>(episode(id) + '/script'),
  synthesize: (id: number) => api.post(episode(id) + '/synthesize'),
  retry: (id: number) => api.post(episode(id) + '/retry'),
  update: (id: number, changes: Partial<Pick<PodcastEpisode, 'familiarity' | 'position_seconds'>>, signal?: AbortSignal) =>
    api.patch(episode(id), changes, signal),
  remove: (id: number) => api.del(episode(id)),
  createPlaylist: (name: string, episode_ids: number[] = []) =>
    api.post<PodcastPlaylist>(root + '/playlists', { name, episode_ids }),
  updatePlaylist: (id: number, changes: Partial<Pick<PodcastPlaylist, 'name' | 'episode_ids'>>) =>
    api.patch<PodcastPlaylist>(playlist(id), changes),
  removePlaylist: (id: number) => api.del(playlist(id)),
  audio: (id: number, signal?: AbortSignal) => downloadNativeBlob(episode(id) + '/audio', signal),
};
