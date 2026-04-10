import asyncio
import json
import os
import re

from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import AsyncGroq
from sse_starlette.sse import EventSourceResponse

from agents import AGENTS, MODERATOR, SYNTHESIZER
from database import (
    DEFAULT_USER_ID, init_db, close_db, build_kb_context,
    get_knowledge, upsert_knowledge, update_knowledge, delete_knowledge,
    save_decision, get_decisions, update_decision_outcome,
    get_agent_weights, set_agent_weight,
)
from models import (
    AgentResponse, DebateResponse, DecisionRequest, DissentFlag,
    KnowledgeEntryCreate, KnowledgeEntryUpdate, DecisionOutcome, AgentWeightUpdate,
)

load_dotenv()

MODEL = "llama-3.3-70b-versatile"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
    except Exception as e:
        print(f"[WARN] Database not available: {e}. Running without persistence.")
        app.state.db_available = False
    else:
        app.state.db_available = True
    yield
    try:
        await close_db()
    except Exception:
        pass


app = FastAPI(title="JudgeTable", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_client() -> AsyncGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not set")
    return AsyncGroq(api_key=api_key)


def build_user_message(decision: str, context: str = "", kb_context: str = "") -> str:
    parts = []
    if kb_context:
        parts.append(kb_context)
    parts.append(f"## Decision Being Considered\n\n{decision}")
    if context:
        parts.append(f"## Additional Context\n\n{context}")
    return "\n\n---\n\n".join(parts)


def build_moderator_message(decision: str, context: str, agent_responses: list[AgentResponse], weights: dict[str, float]) -> str:
    council_parts = []
    for a in agent_responses:
        w = weights.get(a.key, 1.0)
        weight_note = f" [weight: {w}x]" if w != 1.0 else ""
        council_parts.append(f"**{a.name}** ({a.role}){weight_note}:\n{a.response}")
    council_text = "\n\n---\n\n".join(council_parts)
    return (
        f"## Decision\n{decision}\n\n"
        f"{'## Context\\n' + context if context else ''}\n\n"
        f"## Council Perspectives\n\n{council_text}"
    )


def build_synthesizer_message(decision: str, agent_responses: list[AgentResponse], moderator_text: str) -> str:
    council_parts = [f"**{a.name}**: {a.response}" for a in agent_responses]
    return (
        f"## Decision\n{decision}\n\n"
        f"## Council Perspectives (summary)\n\n" + "\n\n".join(council_parts) + "\n\n"
        f"## Moderator Analysis\n\n{moderator_text}"
    )


def parse_dissent_flags(moderator_text: str) -> list[DissentFlag]:
    flags = []
    for match in re.finditer(r"DISSENT:\s*\[?([^\]\—\-]+?)\]?\s*[\—\-]+\s*(.+)", moderator_text):
        agent_name = match.group(1).strip()
        concern = match.group(2).strip()
        agent_key = agent_name.lower().replace("the ", "").replace(" ", "_").replace("'", "")
        flags.append(DissentFlag(agent_name=agent_name, agent_key=agent_key, concern=concern))
    return flags


# ── Agent execution ──

async def run_agent(client: AsyncGroq, agent: dict, user_message: str) -> AgentResponse:
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": agent["system_prompt"]},
            {"role": "user", "content": user_message},
        ],
        temperature=0.7,
        max_tokens=1024,
    )
    return AgentResponse(
        name=agent["name"],
        key=agent["key"],
        role=agent["role"],
        color=agent["color"],
        response=response.choices[0].message.content,
    )


# ── Endpoints ──

@app.get("/health")
async def health():
    return {"status": "ok", "db": getattr(app.state, "db_available", False)}


@app.get("/agents")
async def list_agents():
    agents = [{"name": a["name"], "key": a["key"], "role": a["role"], "color": a["color"]} for a in AGENTS]
    return {"agents": agents, "moderator": {"name": MODERATOR["name"], "key": MODERATOR["key"], "role": MODERATOR["role"], "color": MODERATOR["color"]}, "synthesizer": {"name": SYNTHESIZER["name"], "key": SYNTHESIZER["key"], "role": SYNTHESIZER["role"], "color": SYNTHESIZER["color"]}}


# ── Full debate (non-streaming) ──

@app.post("/debate", response_model=DebateResponse)
async def debate(req: DecisionRequest):
    client = get_client()

    kb_context = ""
    weights = {}
    if getattr(app.state, "db_available", False):
        kb_context = await build_kb_context(req.user_id)
        weights = await get_agent_weights(req.user_id)

    user_message = build_user_message(req.decision, req.context, kb_context)

    # Run all 8 agents in parallel
    agent_responses = list(await asyncio.gather(
        *[run_agent(client, agent, user_message) for agent in AGENTS]
    ))

    # Moderator
    mod_msg = build_moderator_message(req.decision, req.context, agent_responses, weights)
    mod_response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": MODERATOR["system_prompt"]},
            {"role": "user", "content": mod_msg},
        ],
        temperature=0.5,
        max_tokens=1024,
    )
    moderator_text = mod_response.choices[0].message.content
    moderator = AgentResponse(name=MODERATOR["name"], key=MODERATOR["key"], role=MODERATOR["role"], color=MODERATOR["color"], response=moderator_text)

    dissent_flags = parse_dissent_flags(moderator_text)

    # Synthesizer
    synth_msg = build_synthesizer_message(req.decision, agent_responses, moderator_text)
    synth_response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYNTHESIZER["system_prompt"]},
            {"role": "user", "content": synth_msg},
        ],
        temperature=0.6,
        max_tokens=1024,
    )
    synthesizer_text = synth_response.choices[0].message.content
    synthesizer = AgentResponse(name=SYNTHESIZER["name"], key=SYNTHESIZER["key"], role=SYNTHESIZER["role"], color=SYNTHESIZER["color"], response=synthesizer_text)

    # Save to DB
    if getattr(app.state, "db_available", False):
        await save_decision(
            req.user_id, req.decision, req.context,
            [a.model_dump() for a in agent_responses],
            moderator.model_dump(), synthesizer_text,
            [d.model_dump() for d in dissent_flags],
        )

    return DebateResponse(
        decision=req.decision,
        agents=agent_responses,
        moderator=moderator,
        synthesizer=synthesizer,
        dissent_flags=dissent_flags,
    )


# ── SSE streaming debate ──

@app.post("/debate/stream")
async def debate_stream(req: DecisionRequest):
    client = get_client()

    async def event_generator():
        kb_context = ""
        weights = {}
        if getattr(app.state, "db_available", False):
            kb_context = await build_kb_context(req.user_id)
            weights = await get_agent_weights(req.user_id)

        user_message = build_user_message(req.decision, req.context, kb_context)

        # Stream all 8 agents in parallel
        queue = asyncio.Queue()
        agent_results = {}

        async def stream_single_agent(agent: dict):
            key = agent["key"]
            await queue.put(("agent_start", key, {
                "name": agent["name"], "key": key,
                "role": agent["role"], "color": agent["color"],
            }))
            try:
                stream = await client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": agent["system_prompt"]},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.7,
                    max_tokens=1024,
                    stream=True,
                )
                full = ""
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        full += delta
                        await queue.put(("agent_chunk", key, delta))

                agent_results[key] = AgentResponse(
                    name=agent["name"], key=key,
                    role=agent["role"], color=agent["color"],
                    response=full,
                )
                await queue.put(("agent_done", key, None))
            except Exception as e:
                await queue.put(("error", key, str(e)))

        tasks = [asyncio.create_task(stream_single_agent(a)) for a in AGENTS]

        done_count = 0
        total = len(AGENTS)
        while done_count < total:
            event_type, key, data = await queue.get()
            if event_type == "agent_start":
                yield {"event": "agent_start", "data": json.dumps(data)}
            elif event_type == "agent_chunk":
                yield {"event": "agent_chunk", "data": json.dumps({"key": key, "chunk": data})}
            elif event_type == "agent_done":
                done_count += 1
                yield {"event": "agent_done", "data": json.dumps({"key": key})}
            elif event_type == "error":
                yield {"event": "error", "data": json.dumps({"error": data, "agent": key})}
                done_count += 1

        # Ensure all tasks finished
        await asyncio.gather(*tasks, return_exceptions=True)

        agent_responses = [agent_results[a["key"]] for a in AGENTS if a["key"] in agent_results]

        # ── Moderator (streamed) ──
        yield {"event": "moderator_start", "data": json.dumps({
            "name": MODERATOR["name"], "role": MODERATOR["role"], "color": MODERATOR["color"],
        })}

        mod_msg = build_moderator_message(req.decision, req.context, agent_responses, weights)
        moderator_full = ""
        try:
            stream = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": MODERATOR["system_prompt"]},
                    {"role": "user", "content": mod_msg},
                ],
                temperature=0.5,
                max_tokens=1024,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    moderator_full += delta
                    yield {"event": "moderator_chunk", "data": json.dumps({"chunk": delta})}
            yield {"event": "moderator_done", "data": json.dumps({})}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e), "agent": "moderator"})}

        dissent_flags = parse_dissent_flags(moderator_full)
        if dissent_flags:
            yield {"event": "dissent", "data": json.dumps([d.model_dump() for d in dissent_flags])}

        # ── Synthesizer (streamed) ──
        yield {"event": "synthesizer_start", "data": json.dumps({
            "name": SYNTHESIZER["name"], "role": SYNTHESIZER["role"], "color": SYNTHESIZER["color"],
        })}

        synth_msg = build_synthesizer_message(req.decision, agent_responses, moderator_full)
        synthesizer_full = ""
        try:
            stream = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYNTHESIZER["system_prompt"]},
                    {"role": "user", "content": synth_msg},
                ],
                temperature=0.6,
                max_tokens=1024,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    synthesizer_full += delta
                    yield {"event": "synthesizer_chunk", "data": json.dumps({"chunk": delta})}
            yield {"event": "synthesizer_done", "data": json.dumps({})}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e), "agent": "synthesizer"})}

        # Save to DB
        if getattr(app.state, "db_available", False):
            try:
                await save_decision(
                    req.user_id, req.decision, req.context,
                    [a.model_dump() for a in agent_responses],
                    {"response": moderator_full},
                    synthesizer_full,
                    [d.model_dump() for d in dissent_flags],
                )
            except Exception:
                pass

    return EventSourceResponse(event_generator())


# ── Knowledge Base CRUD ──

@app.get("/knowledge")
async def get_kb(user_id: str = DEFAULT_USER_ID, category: str | None = None):
    if not getattr(app.state, "db_available", False):
        return {"entries": []}
    entries = await get_knowledge(user_id, category)
    for e in entries:
        e["id"] = str(e["id"])
        e["created_at"] = e["created_at"].isoformat()
        e["updated_at"] = e["updated_at"].isoformat()
        if isinstance(e.get("metadata"), str):
            e["metadata"] = json.loads(e["metadata"])
    return {"entries": entries}


@app.post("/knowledge")
async def create_kb(entry: KnowledgeEntryCreate, user_id: str = DEFAULT_USER_ID):
    if not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="Database not available")
    result = await upsert_knowledge(user_id, entry.category, entry.title, entry.content, entry.metadata)
    result["id"] = str(result["id"])
    result["created_at"] = result["created_at"].isoformat()
    result["updated_at"] = result["updated_at"].isoformat()
    return result


@app.put("/knowledge/{entry_id}")
async def update_kb(entry_id: str, entry: KnowledgeEntryUpdate):
    if not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="Database not available")
    result = await update_knowledge(entry_id, entry.title, entry.content, entry.metadata)
    if not result:
        raise HTTPException(status_code=404, detail="Entry not found")
    result["id"] = str(result["id"])
    result["created_at"] = result["created_at"].isoformat()
    result["updated_at"] = result["updated_at"].isoformat()
    return result


@app.delete("/knowledge/{entry_id}")
async def delete_kb(entry_id: str):
    if not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="Database not available")
    deleted = await delete_knowledge(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


# ── Decision Log ──

@app.get("/decisions")
async def list_decisions(user_id: str = DEFAULT_USER_ID, limit: int = 20):
    if not getattr(app.state, "db_available", False):
        return {"decisions": []}
    rows = await get_decisions(user_id, limit)
    for r in rows:
        r["id"] = str(r["id"])
        r["created_at"] = r["created_at"].isoformat()
        if r.get("outcome_at"):
            r["outcome_at"] = r["outcome_at"].isoformat()
        if isinstance(r.get("dissent_flags"), str):
            r["dissent_flags"] = json.loads(r["dissent_flags"])
    return {"decisions": rows}


@app.put("/decisions/{decision_id}/outcome")
async def record_outcome(decision_id: str, body: DecisionOutcome):
    if not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="Database not available")
    updated = await update_decision_outcome(decision_id, body.outcome)
    if not updated:
        raise HTTPException(status_code=404, detail="Decision not found")
    return {"ok": True}


# ── Agent Weights ──

@app.get("/weights")
async def get_weights(user_id: str = DEFAULT_USER_ID):
    if not getattr(app.state, "db_available", False):
        return {"weights": {a["key"]: 1.0 for a in AGENTS}}
    weights = await get_agent_weights(user_id)
    # Fill defaults
    for a in AGENTS:
        if a["key"] not in weights:
            weights[a["key"]] = 1.0
    return {"weights": weights}


@app.put("/weights")
async def update_weight(body: AgentWeightUpdate, user_id: str = DEFAULT_USER_ID):
    if not getattr(app.state, "db_available", False):
        raise HTTPException(status_code=503, detail="Database not available")
    await set_agent_weight(user_id, body.agent_key, body.weight)
    return {"ok": True}
