CREATE TABLE IF NOT EXISTS podcast_sources (
    id BIGSERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    question_code VARCHAR(32) NOT NULL,
    title TEXT NOT NULL,
    topic VARCHAR(64) NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    answer TEXT NOT NULL,
    version VARCHAR(64) NOT NULL,
    series BOOLEAN NOT NULL,
    CONSTRAINT podcast_sources_scope UNIQUE (id, workspace_id),
    CONSTRAINT podcast_sources_version UNIQUE (workspace_id, question_id, version, series)
);

CREATE INDEX IF NOT EXISTS ix_podcast_sources_question_id ON podcast_sources(question_id);

CREATE TABLE IF NOT EXISTS podcast_episodes (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL,
    workspace_id INTEGER NOT NULL,
    part INTEGER NOT NULL,
    title TEXT NOT NULL,
    status VARCHAR(16) NOT NULL,
    script TEXT,
    audio BYTEA,
    duration_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    position_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    familiarity VARCHAR(16) NOT NULL DEFAULT 'unmarked',
    error TEXT,
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    FOREIGN KEY (source_id, workspace_id) REFERENCES podcast_sources(id, workspace_id) ON DELETE CASCADE,
    CONSTRAINT podcast_episodes_scope UNIQUE (id, workspace_id),
    CONSTRAINT podcast_episodes_part UNIQUE (source_id, part),
    CONSTRAINT podcast_episodes_positive_part CHECK (part > 0),
    CONSTRAINT podcast_episodes_status CHECK (
        status IN ('generating', 'draft', 'synthesizing', 'ready', 'script_failed', 'audio_failed')
    ),
    CONSTRAINT podcast_episodes_familiarity CHECK (familiarity IN ('unmarked', 'familiar', 'unfamiliar')),
    CONSTRAINT podcast_episodes_lease CHECK (
        (lease_token IS NULL) = (lease_until IS NULL)
        AND (lease_token IS NULL OR status IN ('generating', 'synthesizing'))
    )
);

CREATE INDEX IF NOT EXISTS ix_podcast_episodes_workspace_id ON podcast_episodes(workspace_id, id);
CREATE INDEX IF NOT EXISTS ix_podcast_episodes_queue ON podcast_episodes(status, lease_until, id);

CREATE TABLE IF NOT EXISTS podcast_playlists (
    id BIGSERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name VARCHAR(160) NOT NULL,
    CONSTRAINT podcast_playlists_scope UNIQUE (id, workspace_id)
);

CREATE INDEX IF NOT EXISTS ix_podcast_playlists_workspace_id ON podcast_playlists(workspace_id);

CREATE TABLE IF NOT EXISTS podcast_playlist_episodes (
    playlist_id BIGINT NOT NULL,
    episode_id BIGINT NOT NULL,
    workspace_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, episode_id),
    FOREIGN KEY (playlist_id, workspace_id) REFERENCES podcast_playlists(id, workspace_id) ON DELETE CASCADE,
    FOREIGN KEY (episode_id, workspace_id) REFERENCES podcast_episodes(id, workspace_id) ON DELETE CASCADE,
    CONSTRAINT podcast_playlist_episodes_position CHECK (position >= 0)
);

CREATE INDEX IF NOT EXISTS ix_podcast_playlist_episodes_episode_id
    ON podcast_playlist_episodes(episode_id, workspace_id);
