import { useState } from "react";
import DecisionInput from "./components/DecisionInput";
import AgentCard from "./components/AgentCard";
import AmplifierCard from "./components/AmplifierCard";
import ModeratorCard from "./components/ModeratorCard";
import DissentBanner from "./components/DissentBanner";
import Verdict from "./components/Verdict";
import KnowledgeBase from "./components/KnowledgeBase";

const AGENT_KEYS = [
  "strategist", "devils_advocate", "realist", "inner_critic",
  "systems_thinker", "accountability", "empathy", "domain_expert",
];

const AGENT_DEFAULTS = {
  strategist:      { name: "The Strategist",          role: "long-term alignment & trade-offs",        color: "blue",   content: "", streaming: false, waiting: true },
  devils_advocate: { name: "The Devil's Advocate",    role: "challenges every assumption",              color: "red",    content: "", streaming: false, waiting: true },
  realist:         { name: "The Realist",             role: "feasibility, energy, resources & timing",  color: "amber",  content: "", streaming: false, waiting: true },
  inner_critic:    { name: "The Inner Critic",        role: "self-sabotage, fear, ego & blind spots",   color: "purple", content: "", streaming: false, waiting: true },
  systems_thinker: { name: "The Systems Thinker",     role: "2nd-order effects, ripples & loops",       color: "teal",   content: "", streaming: false, waiting: true },
  accountability:  { name: "The Accountability Agent", role: "past commitments & consistency check",    color: "green",  content: "", streaming: false, waiting: true },
  empathy:         { name: "The Empathy Agent",       role: "emotional state, wellbeing & burnout",     color: "pink",   content: "", streaming: false, waiting: true },
  domain_expert:   { name: "The Domain Expert",       role: "field-specific insight, benchmarks & norms", color: "gray", content: "", streaming: false, waiting: true },
};

export default function App() {
  const [tab, setTab] = useState("council");

  // Amplifier
  const [amplifier, setAmplifier] = useState({ visible: false, status: "", categories: [], summary: "" });

  // Agents
  const [agents, setAgents] = useState(null);
  const [currentRound, setCurrentRound] = useState(0);

  // Moderator
  const [moderator, setModerator] = useState({ content: "", streaming: false, visible: false });
  const [moderatorDecision, setModeratorDecision] = useState(null);
  const [dissent, setDissent] = useState([]);

  // Synthesizer
  const [synthesizer, setSynthesizer] = useState({ content: "", streaming: false, visible: false });

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async (decision, context) => {
    // Reset all state
    setAmplifier({ visible: true, status: "Starting...", categories: [], summary: "" });
    setAgents(structuredClone(AGENT_DEFAULTS));
    setCurrentRound(0);
    setModerator({ content: "", streaming: false, visible: false });
    setModeratorDecision(null);
    setDissent([]);
    setSynthesizer({ content: "", streaming: false, visible: false });
    setLoading(true);
    setError(null);

    try {
      const response = await fetch("/debate/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, context }),
      });

      if (!response.ok) throw new Error(`Server error: ${response.status}`);

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
            if (line.startsWith("event:")) eventType = line.slice(6).trim();
            else if (line.startsWith("data:")) data = line.slice(5).trim();
          }
          if (!eventType || !data) continue;

          let parsed;
          try { parsed = JSON.parse(data); } catch { continue; }

          switch (eventType) {
            // ── Amplifier ──
            case "amplifier_start":
              setAmplifier((prev) => ({ ...prev, status: parsed.status || "Gathering context..." }));
              break;
            case "amplifier_fetch":
              setAmplifier((prev) => ({
                ...prev,
                categories: [...prev.categories, ...(parsed.categories || [])],
                status: `Fetching ${(parsed.categories || []).join(", ")}...`,
              }));
              break;
            case "amplifier_done":
              setAmplifier((prev) => ({
                ...prev,
                status: "Done",
                summary: parsed.context_summary || "",
                categories: parsed.fetched_categories || prev.categories,
              }));
              break;

            // ── Agents ──
            case "agent_start":
              setAgents((prev) => ({
                ...prev,
                [parsed.key]: { ...prev[parsed.key], waiting: false, streaming: true, content: "" },
              }));
              break;
            case "agent_chunk":
              setAgents((prev) => ({
                ...prev,
                [parsed.key]: { ...prev[parsed.key], content: prev[parsed.key].content + parsed.chunk },
              }));
              break;
            case "agent_done":
              setAgents((prev) => ({
                ...prev,
                [parsed.key]: { ...prev[parsed.key], streaming: false },
              }));
              break;

            // ── Moderator ──
            case "moderator_start":
              setModerator({ content: "", streaming: true, visible: true });
              break;
            case "moderator_chunk":
              setModerator((prev) => ({ ...prev, content: prev.content + parsed.chunk }));
              break;
            case "moderator_done":
              setModerator((prev) => ({ ...prev, streaming: false }));
              break;
            case "moderator_decision":
              setModeratorDecision(parsed);
              if (parsed.decision === "CONTINUE") {
                // Reset agent cards for next round
                setCurrentRound(parsed.round + 1);
                setAgents(structuredClone(AGENT_DEFAULTS));
                setModerator({ content: "", streaming: false, visible: false });
              }
              break;

            // ── Dissent ──
            case "dissent":
              setDissent(Array.isArray(parsed) ? parsed : []);
              break;

            // ── Synthesizer ──
            case "synthesizer_start":
              setSynthesizer({ content: "", streaming: true, visible: true });
              break;
            case "synthesizer_chunk":
              setSynthesizer((prev) => ({ ...prev, content: prev.content + parsed.chunk }));
              break;
            case "synthesizer_done":
              setSynthesizer((prev) => ({ ...prev, streaming: false }));
              break;

            // ── Error ──
            case "error":
              setError(parsed.error || "Unknown error");
              break;
          }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setAmplifier({ visible: false, status: "", categories: [], summary: "" });
    setAgents(null);
    setCurrentRound(0);
    setModerator({ content: "", streaming: false, visible: false });
    setModeratorDecision(null);
    setDissent([]);
    setSynthesizer({ content: "", streaming: false, visible: false });
    setError(null);
  };

  const hasContent = agents && Object.values(agents).some((a) => a.content);

  return (
    <div className="app">
      <header className="header">
        <h1>JudgeTable</h1>
        <p>A council of 8 perspectives to challenge your thinking</p>
      </header>

      <div className="tabs">
        <button className={`tab${tab === "council" ? " active" : ""}`} onClick={() => setTab("council")}>
          Council
        </button>
        <button className={`tab${tab === "knowledge" ? " active" : ""}`} onClick={() => setTab("knowledge")}>
          Knowledge Base
        </button>
      </div>

      {tab === "council" && (
        <>
          <DecisionInput onSubmit={handleSubmit} loading={loading} />

          {error && (
            <div style={{ color: "var(--red)", textAlign: "center", margin: "1rem 0", fontSize: "0.9rem" }}>
              {error}
            </div>
          )}

          <AmplifierCard {...amplifier} />

          {agents && (
            <>
              {currentRound > 0 && (
                <p className="section-label">
                  Council — Round {currentRound}
                  {moderatorDecision?.decision === "CONTINUE" && " (moderator requested deeper analysis)"}
                </p>
              )}

              <div className="agents-grid">
                {AGENT_KEYS.map((key) => (
                  <AgentCard key={key} {...agents[key]} />
                ))}
              </div>

              <ModeratorCard
                content={moderator.content}
                streaming={moderator.streaming}
                visible={moderator.visible}
              />

              <DissentBanner flags={dissent} />

              <Verdict
                content={synthesizer.content}
                streaming={synthesizer.streaming}
                visible={synthesizer.visible}
              />

              {!loading && (synthesizer.content || hasContent) && (
                <div style={{ textAlign: "center", marginTop: "2rem" }}>
                  <button className="reset-btn" onClick={handleReset}>
                    new decision &rarr;
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}

      {tab === "knowledge" && <KnowledgeBase />}
    </div>
  );
}
