import ReactMarkdown from "react-markdown";

export default function Verdict({ content, streaming }) {
  if (!content && !streaming) return null;

  return (
    <div className="verdict-card">
      <div className="verdict-header">
        <span className="verdict-icon">&#9878;</span>
        <span className="verdict-name">The Judge — Final Verdict</span>
      </div>
      <div className="verdict-body">
        <ReactMarkdown>{content || ""}</ReactMarkdown>
        {streaming && <span className="cursor" />}
      </div>
    </div>
  );
}
