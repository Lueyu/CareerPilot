import { useEffect, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { podcastApi } from '../api/podcasts';
import { PodcastPlaybackProvider } from './PodcastPlayback';
import { currentPodcastIdentity, downloadPlaylist, saveOffline } from './podcastOffline';
import { identityIsCurrent } from './podcastOfflineIdentity';

export { usePodcastPlayer } from './PodcastPlayback';

export function WorkspacePodcastPlayer({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const onPodcasts = /^\/(?:library\/)?podcasts\/?$/.test(pathname);
  const [opened, setOpened] = useState(onPodcasts);
  useEffect(() => {
    if (onPodcasts) setOpened(true);
  }, [onPodcasts]);
  if (!opened && !onPodcasts) return <>{children}</>;
  return <PodcastPlayerProvider>{children}</PodcastPlayerProvider>;
}

export function PodcastPlayerProvider({ children }: { children: ReactNode }) {
  const identity = currentPodcastIdentity();
  return <PodcastPlaybackProvider identity={identity} access={{
    isCurrent: () => identityIsCurrent(identity) && currentPodcastIdentity().workspace === identity.workspace,
    library: podcastApi.library,
    script: podcastApi.script,
    audio: podcastApi.audio,
    update: podcastApi.update,
    save: saveOffline,
    export: downloadPlaylist,
  }}>{children}</PodcastPlaybackProvider>;
}
