import { useState } from "react";

const EXAMPLES = [
  "I'm thinking about quitting my stable job to start a company",
  "I want to drop out of college to learn on my own",
  "I'm considering ending a 5-year relationship that feels stagnant",
  "I want to move to a new country where I don't know anyone",
  "I'm thinking about turning down a promotion to protect my free time",
];

export default function DecisionInput({ onSubmit, loading }) {
  const [decision, setDecision] = useState("");
  const [context, setContext] = useState("");
  const [showContext, setShowContext] = useState(false);

  const handleSubmit = () => {
    if (!decision.trim()) return;
    onSubmit(decision.trim(), context.trim());
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      handleSubmit();
    }
  };

  return (
    <div className="decision-input">
      <textarea
        value={decision}
        onChange={(e) => setDecision(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="What decision are you wrestling with?"
        disabled={loading}
      />

      <button
        className="context-toggle"
        onClick={() => setShowContext(!showContext)}
      >
        {showContext ? "− Hide context" : "+ Add context"}
      </button>

      {showContext && (
        <div className="context-textarea">
          <textarea
            value={context}
            onChange={(e) => setContext(e.target.value)}
            placeholder="Any background, constraints, or things the council should know..."
            disabled={loading}
          />
        </div>
      )}

      <div className="examples">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            className="example-btn"
            onClick={() => setDecision(ex)}
            disabled={loading}
          >
            {ex}
          </button>
        ))}
      </div>

      <div className="submit-row">
        <button
          className="submit-btn"
          onClick={handleSubmit}
          disabled={loading || !decision.trim()}
        >
          {loading ? "Council is deliberating..." : "Convene the council"}
        </button>
      </div>
    </div>
  );
}
