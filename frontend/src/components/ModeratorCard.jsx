import ReactMarkdown from "react-markdown";

export default function ModeratorCard({ content, streaming, visible }) {
  if (!visible) return null;

  return (
    <div className="moderator-card">
      <div className="moderator-header">
        <div className="moderator-dot" />
        <span className="moderator-name">The Moderator — Council Analysis</span>
      </div>
      <div className="moderator-body">
        {!content && streaming && <span className="waiting-text">Analyzing council perspectives...</span>}
        {content && <ReactMarkdown>{content}</ReactMarkdown>}
        {streaming && content && <span className="cursor" />}
      </div>
    </div>
  );
}
