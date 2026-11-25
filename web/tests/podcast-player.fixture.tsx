import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { bindRegisteredNativeApi } from '../src/api/client';
import { podcastApi, type PodcastLibrary } from '../src/api/podcasts';
import { PodcastPlayerProvider, usePodcastPlayer } from '../src/podcasts/PodcastPlayer';
import { PodcastLibraryView } from '../src/podcasts/library/PodcastLibraryView';
import { scopeEpisodes, type PlaybackScope } from '../src/podcasts/podcastQueue';
import { createWorkspaceApi } from '../src/seed/api';

const account = Number(new URLSearchParams(window.location.search).get('registered'));
if (account) {
  const api = createWorkspaceApi(account, () => {});
  bindRegisteredNativeApi({ workspaceId: account, personalOwner: false, response: api.response, signal: api.signal });
}

function Library() {
  const [library, setLibrary] = useState<PodcastLibrary>();
  const player = usePodcastPlayer();
  useEffect(() => { void podcastApi.library().then(setLibrary); }, []);
  return <main>
    <h1>播客节目库</h1>
    <button type="button" onClick={player.open}>打开播放器</button>
    {library && <>
      <button type="button" onClick={() => {
        const scope = { kind: 'topic', topic: 'Agent' } as const;
        const queue = scopeEpisodes(library, scope);
        player.play(queue[0], queue, scope);
      }}>从主题播放</button>
      <button type="button" onClick={() => {
        const scope: PlaybackScope = { kind: 'selection', source: 'production', episodeIds: [3, 1] };
        const queue = scopeEpisodes(library, scope);
        player.play(queue[0], queue, scope);
      }}>从制作批次播放</button>
      {library.episodes.map(episode => <button key={episode.id} type="button" onClick={() =>
        player.play(episode, library.episodes, { kind: 'playlist', id: library.playlists[0].id })
      }>{episode.title}</button>)}
      <PodcastLibraryView library={library} loading={false} onPlay={player.play} onOpenPlayer={player.open}
        onImport={() => {}} onProduction={() => {}} onNew={() => {}} onPlaylist={() => {}} onTopic={() => {}}
        onAdd={() => {}} onDelete={() => {}} />
    </>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<PodcastPlayerProvider><Library /></PodcastPlayerProvider>);
