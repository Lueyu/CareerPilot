CREATE TABLE IF NOT EXISTS study_notes (
	id SERIAL NOT NULL,
	workspace_id INTEGER NOT NULL,
	origin_session_id INTEGER NOT NULL,
	title VARCHAR(160) NOT NULL,
	current_version_id INTEGER,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	UNIQUE (origin_session_id),
	FOREIGN KEY(origin_session_id) REFERENCES study_sessions (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_study_notes_workspace_id ON study_notes (workspace_id);

CREATE TABLE IF NOT EXISTS study_note_versions (
	id SERIAL NOT NULL,
	note_id INTEGER NOT NULL,
	base_version_id INTEGER,
	source_version_id INTEGER,
	title VARCHAR(160) NOT NULL,
	body TEXT NOT NULL,
	author VARCHAR(20) NOT NULL,
	model VARCHAR(180),
	sources JSONB NOT NULL,
	session_ids JSONB NOT NULL,
	coverage JSONB NOT NULL,
	conflict BOOLEAN NOT NULL,
	cards_generated BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(note_id) REFERENCES study_notes (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_study_note_versions_note_id ON study_note_versions (note_id);

CREATE TABLE IF NOT EXISTS study_note_jobs (
	id SERIAL NOT NULL,
	workspace_id INTEGER NOT NULL,
	session_id INTEGER NOT NULL,
	note_id INTEGER NOT NULL,
	request_id VARCHAR(100) NOT NULL,
	request_aliases JSONB NOT NULL,
	base_version_id INTEGER,
	version_id INTEGER,
	snapshot JSONB NOT NULL,
	conflict BOOLEAN NOT NULL,
	status VARCHAR(20) NOT NULL,
	generation INTEGER NOT NULL,
	lease_until TIMESTAMP WITH TIME ZONE,
	error VARCHAR(250),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (session_id, request_id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	FOREIGN KEY(session_id) REFERENCES study_sessions (id) ON DELETE CASCADE,
	FOREIGN KEY(note_id) REFERENCES study_notes (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_study_note_jobs_session_id ON study_note_jobs (session_id);
CREATE INDEX IF NOT EXISTS ix_study_note_jobs_status ON study_note_jobs (status);

CREATE TABLE IF NOT EXISTS study_cards (
	id SERIAL NOT NULL,
	workspace_id INTEGER NOT NULL,
	note_id INTEGER NOT NULL,
	version_id INTEGER NOT NULL,
	question TEXT NOT NULL,
	answer TEXT NOT NULL,
	knowledge_point VARCHAR(180) NOT NULL,
	status VARCHAR(16) NOT NULL,
	next_review_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	FOREIGN KEY(note_id) REFERENCES study_notes (id) ON DELETE CASCADE,
	FOREIGN KEY(version_id) REFERENCES study_note_versions (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_study_cards_workspace_id ON study_cards (workspace_id);

CREATE TABLE IF NOT EXISTS study_card_events (
	id SERIAL NOT NULL,
	card_id INTEGER NOT NULL,
	kind VARCHAR(30) NOT NULL,
	rating VARCHAR(12),
	request_id VARCHAR(100),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (card_id, request_id),
	FOREIGN KEY(card_id) REFERENCES study_cards (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_study_card_events_card_id ON study_card_events (card_id);
