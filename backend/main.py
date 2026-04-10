import asyncio
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import AsyncGroq
from sse_starlette.sse import EventSourceResponse

from agents import AGENTS, JUDGE
from models import AgentResponse, DebateResponse, DecisionRequest

load_dotenv()

app = FastAPI(title="JudgeTable")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL = "llama-3.3-70b-versatile"


def get_client() -> AsyncGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not set")
    return AsyncGroq(api_key=api_key)


def build_user_message(decision: str, context: str = "") -> str:
    msg = f"Here is the decision I'm considering:\n\n{decision}"
    if context:
        msg += f"\n\nAdditional context:\n{context}"
    return msg


async def run_agent(client: AsyncGroq, agent: dict, decision: str, context: str) -> AgentResponse:
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": agent["system_prompt"]},
            {"role": "user", "content": build_user_message(decision, context)},
        ],
        temperature=0.7,
        max_tokens=1024,
    )
    return AgentResponse(
        name=agent["name"],
        role=agent["role"],
        color=agent["color"],
        response=response.choices[0].message.content,
    )


async def run_judge(client: AsyncGroq, decision: str, context: str, agent_responses: list[AgentResponse]) -> AgentResponse:
    council_text = "\n\n".join(
        f"**{a.name}** ({a.role}):\n{a.response}" for a in agent_responses
    )
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": JUDGE["system_prompt"]},
            {
                "role": "user",
                "content": (
                    f"Decision being considered:\n{decision}\n\n"
                    f"{'Additional context: ' + context if context else ''}\n\n"
                    f"Here are the four perspectives from the council:\n\n{council_text}"
                ),
            },
        ],
        temperature=0.7,
        max_tokens=1024,
    )
    return AgentResponse(
        name=JUDGE["name"],
        role=JUDGE["role"],
        color=JUDGE["color"],
        response=response.choices[0].message.content,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/debate", response_model=DebateResponse)
async def debate(req: DecisionRequest):
    client = get_client()
    agent_responses = await asyncio.gather(
        *[run_agent(client, agent, req.decision, req.context) for agent in AGENTS]
    )
    agent_responses = list(agent_responses)
    judge_response = await run_judge(client, req.decision, req.context, agent_responses)
    return DebateResponse(
        decision=req.decision,
        agents=agent_responses,
        judge=judge_response,
    )


@app.post("/debate/stream")
async def debate_stream(req: DecisionRequest):
    client = get_client()

    async def event_generator():
        agent_responses = []

        for agent in AGENTS:
            yield {
                "event": "agent_start",
                "data": json.dumps({
                    "name": agent["name"],
                    "key": agent["key"],
                    "role": agent["role"],
                    "color": agent["color"],
                }),
            }

            try:
                stream = await client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": agent["system_prompt"]},
                        {"role": "user", "content": build_user_message(req.decision, req.context)},
                    ],
                    temperature=0.7,
                    max_tokens=1024,
                    stream=True,
                )

                full_response = ""
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        full_response += delta
                        yield {
                            "event": "agent_chunk",
                            "data": json.dumps({
                                "key": agent["key"],
                                "chunk": delta,
                            }),
                        }

                agent_responses.append(AgentResponse(
                    name=agent["name"],
                    role=agent["role"],
                    color=agent["color"],
                    response=full_response,
                ))

                yield {
                    "event": "agent_done",
                    "data": json.dumps({"key": agent["key"]}),
                }

            except Exception as e:
                yield {
                    "event": "error",
                    "data": json.dumps({"error": str(e), "agent": agent["key"]}),
                }

        # Judge
        yield {
            "event": "judge_start",
            "data": json.dumps({
                "name": JUDGE["name"],
                "role": JUDGE["role"],
                "color": JUDGE["color"],
            }),
        }

        try:
            council_text = "\n\n".join(
                f"**{a.name}** ({a.role}):\n{a.response}" for a in agent_responses
            )

            stream = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": JUDGE["system_prompt"]},
                    {
                        "role": "user",
                        "content": (
                            f"Decision being considered:\n{req.decision}\n\n"
                            f"{'Additional context: ' + req.context if req.context else ''}\n\n"
                            f"Here are the four perspectives from the council:\n\n{council_text}"
                        ),
                    },
                ],
                temperature=0.7,
                max_tokens=1024,
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield {
                        "event": "judge_chunk",
                        "data": json.dumps({"chunk": delta}),
                    }

            yield {"event": "judge_done", "data": json.dumps({})}

        except Exception as e:
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e), "agent": "judge"}),
            }

    return EventSourceResponse(event_generator())
