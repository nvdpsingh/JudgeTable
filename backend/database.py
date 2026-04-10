import os
from contextlib import asynccontextmanager

import asyncpg

_pool: asyncpg.Pool | None = None

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            user=os.getenv("DB_USER", "judgetable"),
            password=os.getenv("DB_PASSWORD", "judgetable"),
            database=os.getenv("DB_NAME", "judgetable"),
            min_size=2,
            max_size=10,
        )
    return _pool


async def init_db():
    pool = await get_pool()
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        schema = f.read()
    async with pool.acquire() as conn:
        await conn.execute(schema)


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# --- Knowledge Base ---

async def get_knowledge(user_id: str, category: str | None = None) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if category:
            rows = await conn.fetch(
                "SELECT id, category, title, content, metadata, created_at, updated_at "
                "FROM knowledge_entries WHERE user_id = $1 AND category = $2 "
                "ORDER BY created_at",
                user_id, category,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, category, title, content, metadata, created_at, updated_at "
                "FROM knowledge_entries WHERE user_id = $1 "
                "ORDER BY category, created_at",
                user_id,
            )
    return [dict(r) for r in rows]


async def upsert_knowledge(user_id: str, category: str, title: str, content: str, metadata: dict | None = None) -> dict:
    import json
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO knowledge_entries (user_id, category, title, content, metadata) "
            "VALUES ($1, $2, $3, $4, $5) "
            "RETURNING id, category, title, content, metadata, created_at, updated_at",
            user_id, category, title, content, json.dumps(metadata or {}),
        )
    return dict(row)


async def update_knowledge(entry_id: str, title: str, content: str, metadata: dict | None = None) -> dict | None:
    import json
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE knowledge_entries SET title = $2, content = $3, metadata = $4, updated_at = NOW() "
            "WHERE id = $1 "
            "RETURNING id, category, title, content, metadata, created_at, updated_at",
            entry_id, title, content, json.dumps(metadata or {}),
        )
    return dict(row) if row else None


async def delete_knowledge(entry_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM knowledge_entries WHERE id = $1", entry_id)
    return result == "DELETE 1"


# --- Knowledge Base as context string for agents ---

async def build_kb_context(user_id: str) -> str:
    entries = await get_knowledge(user_id)
    if not entries:
        return ""

    sections = {}
    for e in entries:
        cat = e["category"]
        if cat not in sections:
            sections[cat] = []
        sections[cat].append(f"### {e['title']}\n{e['content']}")

    category_labels = {
        "personality": "Personality Profile",
        "goals": "Goals & Aspirations",
        "values": "Values & Principles",
        "blind_spots": "Known Blind Spots & Patterns",
        "context_log": "Recent Context & Events",
        "relationships": "Key Relationships",
        "challenges": "Current Challenges",
    }

    parts = []
    for cat, items in sections.items():
        label = category_labels.get(cat, cat.replace("_", " ").title())
        parts.append(f"## {label}\n" + "\n\n".join(items))

    return "# Knowledge Base — What You Know About This Person\n\n" + "\n\n".join(parts)


# --- Decisions ---

async def save_decision(user_id: str, decision_text: str, context: str,
                        agent_responses: list, moderator_response: dict | None,
                        synthesizer_response: str, dissent_flags: list) -> dict:
    import json
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO decisions (user_id, decision_text, context, agent_responses, "
            "moderator_response, synthesizer_response, dissent_flags) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id, created_at",
            user_id, decision_text, context,
            json.dumps(agent_responses),
            json.dumps(moderator_response) if moderator_response else None,
            synthesizer_response,
            json.dumps(dissent_flags),
        )
    return dict(row)


async def get_decisions(user_id: str, limit: int = 20) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, decision_text, context, synthesizer_response, dissent_flags, "
            "outcome, outcome_at, created_at "
            "FROM decisions WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
            user_id, limit,
        )
    return [dict(r) for r in rows]


async def update_decision_outcome(decision_id: str, outcome: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE decisions SET outcome = $2, outcome_at = NOW() WHERE id = $1",
            decision_id, outcome,
        )
    return result == "UPDATE 1"


# --- Agent Weights ---

async def get_agent_weights(user_id: str) -> dict[str, float]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT agent_key, weight FROM agent_weights WHERE user_id = $1",
            user_id,
        )
    return {r["agent_key"]: r["weight"] for r in rows}


async def set_agent_weight(user_id: str, agent_key: str, weight: float) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agent_weights (user_id, agent_key, weight) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id, agent_key) DO UPDATE SET weight = $3",
            user_id, agent_key, weight,
        )
