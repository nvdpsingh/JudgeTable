import ReactMarkdown from "react-markdown";

export default function Verdict({ content, streaming, visible }) {
  if (!visible) return null;

  return (
    <div className="verdict-card">
      <div className="verdict-header">
        <span className="verdict-icon">&#9878;</span>
        <span className="verdict-name">The Synthesizer — Final Verdict</span>
      </div>
      <div className="verdict-body">
        {!content && streaming && <span className="waiting-text">Crafting your verdict...</span>}
        {content && <ReactMarkdown>{content}</ReactMarkdown>}
        {streaming && content && <span className="cursor" />}
      </div>
    </div>
  );
}
