-- JudgeTable Database Schema

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Knowledge base entries
-- Categories: personality, goals, values, blind_spots, context_log, relationships, challenges
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_user_category ON knowledge_entries(user_id, category);

-- Decision log with full debate transcript
CREATE TABLE IF NOT EXISTS decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    decision_text TEXT NOT NULL,
    context TEXT DEFAULT '',
    agent_responses JSONB DEFAULT '[]',
    moderator_response JSONB,
    synthesizer_response TEXT,
    dissent_flags JSONB DEFAULT '[]',
    outcome TEXT,
    outcome_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decisions_user ON decisions(user_id, created_at DESC);

-- Agent weights per user (1.0 = default, 0.0 = muted, 2.0 = double weight)
CREATE TABLE IF NOT EXISTS agent_weights (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_key TEXT NOT NULL,
    weight FLOAT DEFAULT 1.0,
    PRIMARY KEY (user_id, agent_key)
);

-- Create a default user for single-user mode
INSERT INTO users (id, name, email)
VALUES ('00000000-0000-0000-0000-000000000001', 'Default User', 'default@judgetable.local')
ON CONFLICT (id) DO NOTHING;
