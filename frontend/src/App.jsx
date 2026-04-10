import { useState } from "react";
import DecisionInput from "./components/DecisionInput";
import AgentCard from "./components/AgentCard";
import Verdict from "./components/Verdict";

const AGENT_ORDER = ["mirror", "realist", "future_self", "challenger"];

const INITIAL_AGENTS = {
  mirror: { name: "The Mirror", role: "reflects your blind spots", color: "purple", content: "", streaming: false, waiting: true },
  realist: { name: "The Realist", role: "counts the real cost", color: "amber", content: "", streaming: false, waiting: true },
  future_self: { name: "The Future Self", role: "speaks from 2 years ahead", color: "teal", content: "", streaming: false, waiting: true },
  challenger: { name: "The Challenger", role: "argues the opposite", color: "coral", content: "", streaming: false, waiting: true },
};

export default function App() {
  const [agents, setAgents] = useState(null);
  const [judge, setJudge] = useState({ content: "", streaming: false });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async (decision, context) => {
    setAgents(structuredClone(INITIAL_AGENTS));
    setJudge({ content: "", streaming: false });
    setLoading(true);
    setError(null);

    try {
      const response = await fetch("/debate/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, context }),
      });

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          let eventType = "";
          let data = "";

          for (const line of part.split("\n")) {
            if (line.startsWith("event:")) {
              eventType = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              data = line.slice(5).trim();
            }
          }

          if (!eventType || !data) continue;

          let parsed;
          try {
            parsed = JSON.parse(data);
          } catch {
            continue;
          }

          switch (eventType) {
            case "agent_start":
              setAgents((prev) => ({
                ...prev,
                [parsed.key]: {
                  ...prev[parsed.key],
                  waiting: false,
                  streaming: true,
                },
              }));
              break;

            case "agent_chunk":
              setAgents((prev) => ({
                ...prev,
                [parsed.key]: {
                  ...prev[parsed.key],
                  content: prev[parsed.key].content + parsed.chunk,
                },
              }));
              break;

            case "agent_done":
              setAgents((prev) => ({
                ...prev,
                [parsed.key]: {
                  ...prev[parsed.key],
                  streaming: false,
                },
              }));
              break;

            case "judge_start":
              setJudge({ content: "", streaming: true });
              break;

            case "judge_chunk":
              setJudge((prev) => ({
                ...prev,
                content: prev.content + parsed.chunk,
              }));
              break;

            case "judge_done":
              setJudge((prev) => ({ ...prev, streaming: false }));
              break;

            case "error":
              setError(parsed.error);
              break;
          }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        setError(err.message);
      }
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setAgents(null);
    setJudge({ content: "", streaming: false });
    setError(null);
  };

  return (
    <div className="app">
      <header className="header">
        <h1>JudgeTable</h1>
        <p>A council of perspectives to challenge your thinking</p>
      </header>

      <DecisionInput onSubmit={handleSubmit} loading={loading} />

      {error && (
        <div style={{ color: "var(--coral)", textAlign: "center", margin: "1rem 0", fontSize: "0.9rem" }}>
          {error}
        </div>
      )}

      {agents && (
        <>
          <div className="agents-grid">
            {AGENT_ORDER.map((key) => (
              <AgentCard key={key} {...agents[key]} />
            ))}
          </div>

          <Verdict content={judge.content} streaming={judge.streaming} />

          {!loading && (judge.content || Object.values(agents).some((a) => a.content)) && (
            <div style={{ textAlign: "center", marginTop: "2rem" }}>
              <button className="reset-btn" onClick={handleReset}>
                new decision &rarr;
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
