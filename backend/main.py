import asyncio
import json

from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agents import AGENTS
from database import (
    DEFAULT_USER_ID, init_db, close_db,
    get_knowledge, upsert_knowledge, update_knowledge, delete_knowledge,
    save_decision, get_decisions, update_decision_outcome,
    get_agent_weights, set_agent_weight,
)
from graph import build_graph
from models import (
    DecisionRequest, KnowledgeEntryCreate, KnowledgeEntryUpdate,
    DecisionOutcome, AgentWeightUpdate,
)

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        app.state.db_available = True
    except Exception as e:
        print(f"[WARN] Database not available: {e}. Running without persistence.")
        app.state.db_available = False
    app.state.graph = build_graph()
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


# ── Health & Info ──

@app.get("/health")
async def health():
    return {"status": "ok", "db": getattr(app.state, "db_available", False)}


@app.get("/agents")
async def list_agents():
    return {"agents": [
        {"name": a["name"], "key": a["key"], "role": a["role"], "color": a["color"]}
        for a in AGENTS
    ]}


# ── SSE Streaming Debate ──

@app.post("/debate/stream")
async def debate_stream(req: DecisionRequest):
    graph = app.state.graph
    db_available = getattr(app.state, "db_available", False)

    weights = {}
    if db_available:
        weights = await get_agent_weights(req.user_id)

    queue: asyncio.Queue = asyncio.Queue()

    input_state = {
        "decision": req.decision,
        "context": req.context,
        "user_id": req.user_id,
        "enriched_prompt": "",
        "fetched_categories": [],
        "agent_responses": [],
        "moderator_response": "",
        "moderator_decision": "",
        "cross_pollination": "",
        "dissent_flags": [],
        "synthesizer_response": "",
        "round_number": 0,
    }

    config = {
        "configurable": {
            "sse_queue": queue,
            "db_available": db_available,
            "agent_weights": weights,
        },
    }

    async def run_graph():
        try:
            result = await graph.ainvoke(input_state, config=config)
            # Save to DB after completion
            if db_available:
                try:
                    await save_decision(
                        req.user_id, req.decision, req.context,
                        result.get("agent_responses", []),
                        {"response": result.get("moderator_response", "")},
                        result.get("synthesizer_response", ""),
                        result.get("dissent_flags", []),
                    )
                except Exception:
                    pass
        except Exception as e:
            await queue.put({"event": "error", "data": {"error": str(e)}})
        finally:
            await queue.put(None)  # sentinel

    async def event_generator():
        task = asyncio.create_task(run_graph())
        while True:
            event = await queue.get()
            if event is None:
                break
            yield {
                "event": event["event"],
                "data": json.dumps(event["data"]),
            }
        await task

    return EventSourceResponse(event_generator())


# ── Non-streaming Debate ──

@app.post("/debate")
async def debate(req: DecisionRequest):
    graph = app.state.graph
    db_available = getattr(app.state, "db_available", False)

    weights = {}
    if db_available:
        weights = await get_agent_weights(req.user_id)

    input_state = {
        "decision": req.decision,
        "context": req.context,
        "user_id": req.user_id,
        "enriched_prompt": "",
        "fetched_categories": [],
        "agent_responses": [],
        "moderator_response": "",
        "moderator_decision": "",
        "cross_pollination": "",
        "dissent_flags": [],
        "synthesizer_response": "",
        "round_number": 0,
    }

    config = {
        "configurable": {
            "db_available": db_available,
            "agent_weights": weights,
        },
    }

    result = await graph.ainvoke(input_state, config=config)

    if db_available:
        try:
            await save_decision(
                req.user_id, req.decision, req.context,
                result.get("agent_responses", []),
                {"response": result.get("moderator_response", "")},
                result.get("synthesizer_response", ""),
                result.get("dissent_flags", []),
            )
        except Exception:
            pass

    return {
        "decision": req.decision,
        "agents": result.get("agent_responses", []),
        "moderator_response": result.get("moderator_response", ""),
        "synthesizer_response": result.get("synthesizer_response", ""),
        "dissent_flags": result.get("dissent_flags", []),
        "rounds": result.get("round_number", 1),
    }


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
