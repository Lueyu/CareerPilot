ALTER TABLE podcast_sources
    ADD COLUMN IF NOT EXISTS language VARCHAR(9) NOT NULL DEFAULT 'zh';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'podcast_sources'::regclass AND conname = 'podcast_sources_language'
    ) THEN
        ALTER TABLE podcast_sources
            ADD CONSTRAINT podcast_sources_language CHECK (language IN ('zh', 'en', 'bilingual'));
    END IF;
END $$;
