import ReactMarkdown from "react-markdown";

export default function AgentCard({ name, role, color, content, streaming, waiting }) {
  return (
    <div className={`agent-card ${color}${waiting ? " loading" : ""}`}>
      <div className="agent-header">
        <div className={`agent-dot ${color}`} />
        <div>
          <div className="agent-name">{name}</div>
          <div className="agent-role">{role}</div>
        </div>
      </div>
      <div className="agent-body">
        {waiting && <span className="waiting-text">Waiting...</span>}
        {content && (
          <ReactMarkdown>{content}</ReactMarkdown>
        )}
        {streaming && <span className="cursor" />}
      </div>
    </div>
  );
}
