-- Memoria PostgreSQL Schema
-- Replaces all flat-file storage: JSONL, JSON, Markdown, SQLite FTS5
-- Target database: memoria (created separately)

-- ═════════════════════════════════════════════════════════════════
--  Extensions
-- ═════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ═════════════════════════════════════════════════════════════════
--  Chitchat — Chat messages
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS chitchat_messages (
    id          TEXT PRIMARY KEY,
    room        TEXT NOT NULL DEFAULT 'general',
    from_name   TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT '',
    topic       TEXT NOT NULL DEFAULT '',
    ts          TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'message',
    ingested_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);

CREATE INDEX IF NOT EXISTS idx_chitchat_room ON chitchat_messages(room);
CREATE INDEX IF NOT EXISTS idx_chitchat_ingested ON chitchat_messages(ingested_at);
CREATE INDEX IF NOT EXISTS idx_chitchat_text_trgm ON chitchat_messages USING gin (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_chitchat_from_trgm ON chitchat_messages USING gin (from_name gin_trgm_ops);

-- ═════════════════════════════════════════════════════════════════
--  Sessions — opencode session records
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    project     TEXT NOT NULL DEFAULT '',
    directory   TEXT NOT NULL DEFAULT '',
    created     DOUBLE PRECISION NOT NULL DEFAULT 0,
    task        TEXT NOT NULL DEFAULT '',
    outcome     TEXT NOT NULL DEFAULT '',
    tools       TEXT[] NOT NULL DEFAULT '{}',
    tool_count  INTEGER NOT NULL DEFAULT 0,
    summary     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created);
CREATE INDEX IF NOT EXISTS idx_sessions_title_trgm ON sessions USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sessions_summary_trgm ON sessions USING gin (summary gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sessions_task_trgm ON sessions USING gin (task gin_trgm_ops);

-- ═════════════════════════════════════════════════════════════════
--  Agents — agent registry
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS agents (
    id               TEXT PRIMARY KEY,
    project          TEXT NOT NULL DEFAULT '',
    task             TEXT NOT NULL DEFAULT '',
    files            TEXT[] NOT NULL DEFAULT '{}',
    pid              INTEGER NOT NULL DEFAULT 0,
    started_at       DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    last_heartbeat   DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    status           TEXT NOT NULL DEFAULT 'active',
    activity         TEXT NOT NULL DEFAULT '',
    commit_log       TEXT[] NOT NULL DEFAULT '{}',
    chitchat_name    TEXT NOT NULL DEFAULT '',
    conflicts_warned TEXT[] NOT NULL DEFAULT '{}',
    capabilities     TEXT[] NOT NULL DEFAULT '{general}'
);

CREATE INDEX IF NOT EXISTS idx_agents_project ON agents(project);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agents_heartbeat ON agents(last_heartbeat);

-- ═════════════════════════════════════════════════════════════════
--  Tasks — task board
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    project         TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    assigned_to     TEXT NOT NULL DEFAULT '',
    depends_on      TEXT[] NOT NULL DEFAULT '{}',
    created_at      DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    assigned_at     DOUBLE PRECISION,
    result          TEXT NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT '',
    rollback_commit TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);

-- ═════════════════════════════════════════════════════════════════
--  Topics — named topic containers with facts
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS topics (
    name       TEXT PRIMARY KEY,
    facts      TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_topics_facts_trgm ON topics USING gin (facts);

-- ═════════════════════════════════════════════════════════════════
--  Proposals — proposed facts awaiting acceptance
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS proposals (
    id          TEXT PRIMARY KEY,
    text        TEXT NOT NULL DEFAULT '',
    topic       TEXT NOT NULL DEFAULT '',
    proposed_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    source      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_proposals_topic ON proposals(topic);

-- ═════════════════════════════════════════════════════════════════
--  Project Memory — per-project MEMORY.md entries
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS project_memory (
    id         BIGSERIAL PRIMARY KEY,
    project    TEXT NOT NULL,
    entry      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_project ON project_memory(project);
CREATE INDEX IF NOT EXISTS idx_memory_entry_trgm ON project_memory USING gin (entry gin_trgm_ops);

-- ═════════════════════════════════════════════════════════════════
--  Cortex — PFC-BG Gating (Go/NoGo Q-learning)
--  One row per project, JSONB for nested dicts
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cortex_gating (
    project        TEXT PRIMARY KEY,
    go             JSONB NOT NULL DEFAULT '{}',
    nogo           JSONB NOT NULL DEFAULT '{}',
    n              JSONB NOT NULL DEFAULT '{}',
    reward_mean    JSONB NOT NULL DEFAULT '{}',
    reward_var     JSONB NOT NULL DEFAULT '{}',
    rpe_history    DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    raw_rewards    DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    alpha_surprise DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    beta_surprise  DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    epsilon        DOUBLE PRECISION NOT NULL DEFAULT 0.3,
    alpha          DOUBLE PRECISION NOT NULL DEFAULT 0.15,
    gamma          DOUBLE PRECISION NOT NULL DEFAULT 0.9,
    alpha_decay    DOUBLE PRECISION NOT NULL DEFAULT 0.995,
    min_epsilon    DOUBLE PRECISION NOT NULL DEFAULT 0.05,
    beta_g         JSONB NOT NULL DEFAULT '{}',
    beta_n         JSONB NOT NULL DEFAULT '{}',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═════════════════════════════════════════════════════════════════
--  Cortex — Hippocampal Episodic Memory
--  One row per project, episodes as JSONB array
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cortex_hippocampus (
    project   TEXT PRIMARY KEY,
    episodes  JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═════════════════════════════════════════════════════════════════
--  Cortex — Hippocampal Replay Buffer
--  One row per project, buffer as JSONB array
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cortex_replay (
    project    TEXT PRIMARY KEY,
    buffer     JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═════════════════════════════════════════════════════════════════
--  Cortex — Auction Coordinator
--  One row per project, JSONB for reputation/ensemble state
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cortex_auction (
    project        TEXT PRIMARY KEY,
    reputation     JSONB NOT NULL DEFAULT '{}',
    responsiveness JSONB NOT NULL DEFAULT '{}',
    pliancy        JSONB NOT NULL DEFAULT '{}',
    choice         JSONB NOT NULL DEFAULT '{}',
    bid_history    JSONB NOT NULL DEFAULT '{}',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═════════════════════════════════════════════════════════════════
--  Cortex — PFC Working Memory (subgoal stacks)
--  One row per project
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cortex_working_memory (
    project       TEXT PRIMARY KEY,
    subgoal_stack JSONB NOT NULL DEFAULT '[]',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═════════════════════════════════════════════════════════════════
--  Cortex — Socratic Decomposer (assumptions + dialectics)
--  One row per project
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cortex_socratic (
    project           TEXT PRIMARY KEY,
    assumptions       JSONB NOT NULL DEFAULT '[]',
    dialectic_records JSONB NOT NULL DEFAULT '[]',
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═════════════════════════════════════════════════════════════════
--  Cortex — Efferent Feedback Log
--  Append-only log, filtered by project
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cortex_efferent (
    id          BIGSERIAL PRIMARY KEY,
    project     TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL DEFAULT '',
    rpe         DOUBLE PRECISION,
    go_after    DOUBLE PRECISION,
    nogo_after  DOUBLE PRECISION,
    timestamp   DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);

CREATE INDEX IF NOT EXISTS idx_efferent_project ON cortex_efferent(project);
CREATE INDEX IF NOT EXISTS idx_efferent_ts ON cortex_efferent(timestamp);

-- ═════════════════════════════════════════════════════════════════
--  Culture — Lessons
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS lessons (
    lesson_id         TEXT PRIMARY KEY,
    title             TEXT NOT NULL DEFAULT '',
    topic             TEXT NOT NULL DEFAULT '',
    prerequisites     TEXT[] NOT NULL DEFAULT '{}',
    facts             TEXT[] NOT NULL DEFAULT '{}',
    examples          TEXT[] NOT NULL DEFAULT '{}',
    exercises         TEXT[] NOT NULL DEFAULT '{}',
    teacher_agent     TEXT NOT NULL DEFAULT '',
    creator_project   TEXT NOT NULL DEFAULT '',
    generation        INTEGER NOT NULL DEFAULT 0,
    parent_id         TEXT,
    score             DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    n_students        INTEGER NOT NULL DEFAULT 0,
    created_at        DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);

CREATE INDEX IF NOT EXISTS idx_lessons_topic ON lessons(topic);
CREATE INDEX IF NOT EXISTS idx_lessons_project ON lessons(creator_project);
CREATE INDEX IF NOT EXISTS idx_lessons_score ON lessons(score);

-- ═════════════════════════════════════════════════════════════════
--  Culture — Student Outcomes
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS student_outcomes (
    id             BIGSERIAL PRIMARY KEY,
    lesson_id      TEXT NOT NULL REFERENCES lessons(lesson_id) ON DELETE CASCADE,
    student_agent  TEXT NOT NULL,
    success        BOOLEAN,
    outcome        DOUBLE PRECISION,
    score_after    DOUBLE PRECISION,
    timestamp      DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);

CREATE INDEX IF NOT EXISTS idx_outcomes_lesson ON student_outcomes(lesson_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_student ON student_outcomes(student_agent);

-- ═════════════════════════════════════════════════════════════════
--  Culture — Cultural Memory (cross-generational facts)
--  One row per project
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cultural_memory (
    project    TEXT PRIMARY KEY,
    facts      JSONB NOT NULL DEFAULT '[]',
    generation INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═════════════════════════════════════════════════════════════════
--  Federation — Peers
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS federation_peers (
    name       TEXT PRIMARY KEY,
    url        TEXT NOT NULL DEFAULT '',
    api_key    TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);

-- ═════════════════════════════════════════════════════════════════
--  Federation — Sync State
--  Singleton row (name = 'singleton'), state as JSONB
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS federation_sync_state (
    name       TEXT PRIMARY KEY DEFAULT 'singleton',
    state      JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═════════════════════════════════════════════════════════════════
--  Safety — Git Snapshots
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS safety_snapshots (
    id           TEXT PRIMARY KEY,
    project      TEXT NOT NULL DEFAULT '',
    project_dir  TEXT NOT NULL DEFAULT '',
    commit_hash  TEXT NOT NULL DEFAULT '',
    message      TEXT NOT NULL DEFAULT '',
    agent_id     TEXT NOT NULL DEFAULT '',
    created_at   DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);

CREATE INDEX IF NOT EXISTS idx_snapshots_project ON safety_snapshots(project);
CREATE INDEX IF NOT EXISTS idx_snapshots_created ON safety_snapshots(created_at);

-- ═════════════════════════════════════════════════════════════════
--  Time Tracking — Sessions (moved from bloom_terminal)
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS time_sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project             TEXT NOT NULL,
    task_id             TEXT NOT NULL DEFAULT '',
    tags                TEXT[] NOT NULL DEFAULT '{}',
    notes               TEXT NOT NULL DEFAULT '',
    started             DOUBLE PRECISION NOT NULL,
    ended               DOUBLE PRECISION,
    ai_wall_seconds     DOUBLE PRECISION NOT NULL DEFAULT 0,
    human_estimate_minutes DOUBLE PRECISION NOT NULL DEFAULT 0,
    ops_count           INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_time_sessions_project ON time_sessions(project);
CREATE INDEX IF NOT EXISTS idx_time_sessions_started ON time_sessions(started);

-- ═════════════════════════════════════════════════════════════════
--  Time Tracking — Operations (moved from bloom_terminal)
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS time_operations (
    id                      SERIAL PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES time_sessions(id) ON DELETE CASCADE,
    op_type                 TEXT NOT NULL,
    count                   INTEGER NOT NULL DEFAULT 0,
    unit_cost_minutes       DOUBLE PRECISION NOT NULL DEFAULT 2.0,
    total_estimate_minutes  DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_time_operations_session ON time_operations(session_id);
CREATE INDEX IF NOT EXISTS idx_time_operations_type ON time_operations(op_type);

-- ═════════════════════════════════════════════════════════════════
--  Time Tracking — Estimates (moved from bloom_terminal)
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS time_estimates (
    op_type         TEXT PRIMARY KEY,
    minutes_per_unit DOUBLE PRECISION NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═════════════════════════════════════════════════════════════════
--  Stats — LLM Usage Log (for time tracking + audit)
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS stats_llm_usage (
    id              BIGSERIAL PRIMARY KEY,
    provider        TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    endpoint        TEXT NOT NULL DEFAULT '',
    agent_id        TEXT NOT NULL DEFAULT '',
    project         TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_provider ON stats_llm_usage(provider);
CREATE INDEX IF NOT EXISTS idx_llm_usage_project ON stats_llm_usage(project);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON stats_llm_usage(created_at);

-- ═════════════════════════════════════════════════════════════════
--  Enrichment Queue — async LLM enrichment for search vocabulary
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS enrichment_queue (
    id          BIGSERIAL PRIMARY KEY,
    surface     TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    processed_at DOUBLE PRECISION,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_enrich_queue_status ON enrichment_queue(status);
CREATE INDEX IF NOT EXISTS idx_enrich_queue_surface ON enrichment_queue(surface, status);

-- ═════════════════════════════════════════════════════════════════
--  Papers — research paper index for /context search
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS papers (
    id              BIGSERIAL PRIMARY KEY,
    filename        TEXT NOT NULL,
    folder          TEXT NOT NULL DEFAULT 'papers',
    title           TEXT NOT NULL DEFAULT '',
    text            TEXT NOT NULL DEFAULT '',
    enriched_text   TEXT NOT NULL DEFAULT '',
    file_mtime      DOUBLE PRECISION NOT NULL DEFAULT 0,
    indexed_at      DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    UNIQUE (filename, folder)
);

CREATE INDEX IF NOT EXISTS idx_papers_filename ON papers(filename);
CREATE INDEX IF NOT EXISTS idx_papers_folder ON papers(folder);
CREATE INDEX IF NOT EXISTS idx_papers_text_trgm ON papers USING gin (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_papers_enriched_trgm ON papers USING gin (enriched_text gin_trgm_ops);

-- Migration: add folder column to existing papers table
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'papers' AND column_name = 'folder') THEN
        ALTER TABLE papers ADD COLUMN folder TEXT NOT NULL DEFAULT 'papers';
        ALTER TABLE papers DROP CONSTRAINT IF EXISTS papers_filename_key;
        ALTER TABLE papers ADD CONSTRAINT papers_filename_folder_unique UNIQUE (filename, folder);
        CREATE INDEX IF NOT EXISTS idx_papers_folder ON papers(folder);
    END IF;
END $$;

-- ═════════════════════════════════════════════════════════════════
--  Schema Migrations — search enrichment columns
-- ═════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'sessions' AND column_name = 'search_enrichments') THEN
        ALTER TABLE sessions ADD COLUMN search_enrichments TEXT[] NOT NULL DEFAULT '{}';
        CREATE INDEX IF NOT EXISTS idx_sessions_enrich ON sessions USING gin (search_enrichments);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'chitchat_messages' AND column_name = 'search_enrichments') THEN
        ALTER TABLE chitchat_messages ADD COLUMN search_enrichments TEXT[] NOT NULL DEFAULT '{}';
        CREATE INDEX IF NOT EXISTS idx_chitchat_enrich ON chitchat_messages USING gin (search_enrichments);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'topics' AND column_name = 'search_enrichments') THEN
        ALTER TABLE topics ADD COLUMN search_enrichments TEXT[] NOT NULL DEFAULT '{}';
        CREATE INDEX IF NOT EXISTS idx_topics_enrich ON topics USING gin (search_enrichments);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'project_memory' AND column_name = 'search_enrichments') THEN
        ALTER TABLE project_memory ADD COLUMN search_enrichments TEXT[] NOT NULL DEFAULT '{}';
        CREATE INDEX IF NOT EXISTS idx_memory_enrich ON project_memory USING gin (search_enrichments);
    END IF;
END $$;

-- Deduplication partial unique index
CREATE UNIQUE INDEX IF NOT EXISTS idx_enrich_queue_dedup
    ON enrichment_queue (surface, record_id)
    WHERE status IN ('pending', 'processing');

-- ═════════════════════════════════════════════════════════════════
--  Paper Brain — Red Ink (priority + memory_type + strength)
-- ═════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'project_memory' AND column_name = 'priority') THEN
        ALTER TABLE project_memory ADD COLUMN priority VARCHAR(10) NOT NULL DEFAULT 'normal';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'project_memory' AND column_name = 'memory_type') THEN
        ALTER TABLE project_memory ADD COLUMN memory_type VARCHAR(20) NOT NULL DEFAULT 'temporal';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'project_memory' AND column_name = 'strength') THEN
        ALTER TABLE project_memory ADD COLUMN strength DOUBLE PRECISION NOT NULL DEFAULT 1.0;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'project_memory' AND column_name = 'last_accessed') THEN
        ALTER TABLE project_memory ADD COLUMN last_accessed DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now());
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_memory_priority ON project_memory(priority);
CREATE INDEX IF NOT EXISTS idx_memory_type ON project_memory(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_strength ON project_memory(strength);
CREATE INDEX IF NOT EXISTS idx_memory_last_accessed ON project_memory(last_accessed);
CREATE INDEX IF NOT EXISTS idx_memory_project_priority ON project_memory(project, priority);
CREATE INDEX IF NOT EXISTS idx_memory_project_type ON project_memory(project, memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_project_type_created ON project_memory(project, memory_type, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_project_created ON sessions(project, created);

DO $$ BEGIN
    ALTER TABLE project_memory ADD CONSTRAINT chk_priority
        CHECK (priority IN ('critical', 'important', 'normal'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE project_memory ADD CONSTRAINT chk_memory_type
        CHECK (memory_type IN ('red', 'concept', 'procedural', 'temporal', 'relation'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ═════════════════════════════════════════════════════════════════
--  Paper Brain — Procedural Memory (basal ganglia)
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS procedural_memory (
    id SERIAL PRIMARY KEY,
    project VARCHAR(200) NOT NULL,
    task_pattern VARCHAR(500) NOT NULL,
    task_type VARCHAR(100),
    steps JSONB NOT NULL DEFAULT '[]',
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    reinforcement_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    last_success_at DOUBLE PRECISION,
    proven_by TEXT[] NOT NULL DEFAULT '{}',
    created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    retired BOOLEAN NOT NULL DEFAULT FALSE,
    search_enrichments TEXT[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_proc_project ON procedural_memory(project);
CREATE INDEX IF NOT EXISTS idx_proc_pattern ON procedural_memory USING gin (task_pattern gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_proc_score ON procedural_memory(reinforcement_score DESC);
CREATE INDEX IF NOT EXISTS idx_proc_retired ON procedural_memory(retired);

DO $$ BEGIN
    ALTER TABLE procedural_memory ADD CONSTRAINT chk_proc_score_range
        CHECK (reinforcement_score >= 0 AND reinforcement_score <= 1);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE procedural_memory ADD CONSTRAINT chk_proc_counts_nonneg
        CHECK (success_count >= 0 AND fail_count >= 0);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ═════════════════════════════════════════════════════════════════
--  Paper Brain — Consolidated Memory (3-tier cortex)
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS consolidated_memory (
    id SERIAL PRIMARY KEY,
    project VARCHAR(200) NOT NULL,
    tier VARCHAR(20) NOT NULL DEFAULT 'consolidated',
    content TEXT NOT NULL,
    source_sessions TEXT[] NOT NULL DEFAULT '{}',
    source_episode_ids TEXT[] NOT NULL DEFAULT '{}',
    memory_type VARCHAR(20) NOT NULL DEFAULT 'concept',
    priority VARCHAR(10) NOT NULL DEFAULT 'normal',
    strength DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    last_accessed DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    search_enrichments TEXT[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_consolidated_project_tier ON consolidated_memory(project, tier);
CREATE INDEX IF NOT EXISTS idx_consolidated_priority ON consolidated_memory(priority);
CREATE INDEX IF NOT EXISTS idx_consolidated_type ON consolidated_memory(memory_type);
CREATE INDEX IF NOT EXISTS idx_consolidated_memory_last_accessed ON consolidated_memory(last_accessed);
CREATE INDEX IF NOT EXISTS idx_consolidated_memory_strength ON consolidated_memory(strength);

DO $$ BEGIN
    ALTER TABLE consolidated_memory ADD CONSTRAINT chk_consolidated_priority
        CHECK (priority IN ('critical', 'important', 'normal'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE consolidated_memory ADD CONSTRAINT chk_consolidated_memory_type
        CHECK (memory_type IN ('red', 'concept', 'procedural', 'temporal', 'relation'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE consolidated_memory DROP CONSTRAINT IF EXISTS chk_consolidated_tier;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE consolidated_memory ADD CONSTRAINT chk_consolidated_tier
        CHECK (tier IN ('immediate', 'consolidated', 'timeless'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Migrate existing rows from old tier values
UPDATE consolidated_memory SET tier = 'immediate' WHERE tier = 'short_term';
UPDATE consolidated_memory SET tier = 'timeless' WHERE tier = 'archived';

-- ═════════════════════════════════════════════════════════════════
--  Paper Brain — Memory Archive (forgotten but searchable)
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS memory_archive (
    id BIGSERIAL PRIMARY KEY,
    original_id BIGINT NOT NULL,
    project TEXT NOT NULL,
    entry TEXT NOT NULL,
    priority VARCHAR(10) NOT NULL DEFAULT 'normal',
    memory_type VARCHAR(20) NOT NULL DEFAULT 'temporal',
    strength DOUBLE PRECISION NOT NULL DEFAULT 0.1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    search_enrichments TEXT[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_archive_project ON memory_archive(project);
CREATE INDEX IF NOT EXISTS idx_archive_type ON memory_archive(memory_type);

-- ═════════════════════════════════════════════════════════════════
--  Paper Brain — Memory Cost Analytics
-- ═════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS memory_costs (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(100),
    project VARCHAR(200) NOT NULL,
    tokens_injected INTEGER NOT NULL DEFAULT 0,
    tokens_saved_injection INTEGER NOT NULL DEFAULT 0,
    tokens_saved_forgetting INTEGER NOT NULL DEFAULT 0,
    context_type VARCHAR(20) NOT NULL DEFAULT 'full',
    task_outcome VARCHAR(20),
    created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    breakdown JSONB
);

CREATE INDEX IF NOT EXISTS idx_costs_project ON memory_costs(project);
CREATE INDEX IF NOT EXISTS idx_costs_created ON memory_costs(created_at);

DO $$ BEGIN
    ALTER TABLE memory_costs ADD COLUMN IF NOT EXISTS breakdown JSONB;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
