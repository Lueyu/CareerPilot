import type { PodcastEpisode, PodcastPlaylist } from '../api/podcasts';

export type PlaybackScope =
  | { kind: 'all' }
  | { kind: 'unfamiliar' }
  | { kind: 'topic'; topic: string }
  | { kind: 'playlist'; id: number | string }
  | { kind: 'selection'; source: 'search' | 'production'; episodeIds: number[] };

export type PlaybackLibrary = {
  episodes: PodcastEpisode[];
  playlists: (Omit<PodcastPlaylist, 'id'> & { id: number | string })[];
};

export type StartPodcastPlayback = (episode: PodcastEpisode, queue: PodcastEpisode[], scope: PlaybackScope) => void;

export function playbackScopeKey(scope: PlaybackScope) {
  switch (scope.kind) {
    case 'topic': return 'topic:' + scope.topic;
    case 'playlist': return 'playlist:' + typeof scope.id + ':' + scope.id;
    case 'selection': return 'selection:' + scope.source;
    default: return scope.kind;
  }
}

export function scopeEpisodes(library: PlaybackLibrary, scope: PlaybackScope): PodcastEpisode[] {
  const ready = library.episodes.filter(episode => episode.status === 'ready');
  switch (scope.kind) {
    case 'all': return ready;
    case 'unfamiliar': return ready.filter(episode => episode.familiarity === 'unfamiliar');
    case 'topic': return ready.filter(episode => episode.topic === scope.topic);
    case 'playlist':
    case 'selection': {
      const byId = new Map(ready.map(episode => [episode.id, episode]));
      const ids = scope.kind === 'selection' ? scope.episodeIds
        : library.playlists.find(playlist => playlist.id === scope.id)?.episode_ids ?? [];
      return [...new Set(ids)].flatMap(id => {
        const episode = byId.get(id);
        return episode ? [episode] : [];
      });
    }
  }
}

export function shuffleRound(ids: number[], currentId?: number, random = Math.random) {
  const result = [...new Set(ids)].filter(id => id !== currentId);
  for (let index = result.length - 1; index > 0; index--) {
    const other = Math.floor(random() * (index + 1));
    [result[index], result[other]] = [result[other], result[index]];
  }
  if (currentId !== undefined && ids.includes(currentId)) result.unshift(currentId);
  return result;
}

export function adjacentEpisode(
  order: number[], eligible: PodcastEpisode[], currentId: number | undefined, direction: -1 | 1,
) {
  const byId = new Map(eligible.map(episode => [episode.id, episode]));
  const current = currentId === undefined ? -1 : order.indexOf(currentId);
  for (let index = current + direction; index >= 0 && index < order.length; index += direction) {
    const episode = byId.get(order[index]);
    if (episode) return episode;
  }
  return undefined;
}
