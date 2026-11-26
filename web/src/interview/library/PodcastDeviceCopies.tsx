import { useEffect, useState } from "react";
import { EpisodeList } from "../../podcasts/library/EpisodeList";
import { PodcastPlaybackProvider, usePodcastPlayer } from "../../podcasts/PodcastPlayback";
import { currentPodcastIdentity } from "../../podcasts/podcastOffline";
import { offlineLibrary, podcastPacksChanged } from "../../podcasts/podcastOfflineMedia";
import { scopeEpisodes, type PlaybackLibrary } from "../../podcasts/podcastQueue";
import { errorText, QueryState } from "./ui";

type Identity = ReturnType<typeof currentPodcastIdentity>;

function Contents({ identity, onBack }: { identity: Identity; onBack: () => void }) {
  const [library, setLibrary] = useState<PlaybackLibrary>();
  const [error, setError] = useState("");
  const player = usePodcastPlayer();
  useEffect(() => {
    let active = true;
    let request = 0;
    function refresh() {
      const next = ++request;
      void offlineLibrary(identity).then(
        (result) => {
          if (active && next === request) {
            setLibrary(result);
            setError("");
          }
        },
        (failure) => {
          if (active && next === request) setError(errorText(failure));
        },
      );
    }
    refresh();
    window.addEventListener(podcastPacksChanged, refresh);
    return () => {
      active = false;
      window.removeEventListener(podcastPacksChanged, refresh);
    };
  }, [identity]);

  return (
    <section className="ial-panel">
      <header className="ial-toolbar">
        <h2>此设备上保存的播客</h2>
        <span className="ial-spacer" />
        <button className="ial-button" onClick={onBack}>
          返回节目库
        </button>
        <button className="ial-button" onClick={player.open}>
          打开播放器
        </button>
      </header>
      <QueryState
        error={error}
        loading={!library}
        empty={library && !library.episodes.length ? "此设备暂无已保存的播客。" : undefined}
      />
      {library?.playlists.map((pack) => {
        const scope = { kind: "playlist", id: pack.id } as const;
        const episodes = scopeEpisodes(library, scope);
        return (
          <section key={pack.id} className="ial-inset">
            <h3>{pack.name}</h3>
            <EpisodeList
              episodes={episodes}
              onPlay={(episode) => player.play(episode, episodes, scope)}
            />
          </section>
        );
      })}
    </section>
  );
}

export default function PodcastDeviceCopies({
  identity,
  onBack,
}: {
  identity: Identity;
  onBack: () => void;
}) {
  return (
    <PodcastPlaybackProvider identity={identity}>
      <Contents identity={identity} onBack={onBack} />
    </PodcastPlaybackProvider>
  );
}
