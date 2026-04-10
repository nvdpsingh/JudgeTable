export default function AmplifierCard({ status, categories, summary, visible }) {
  if (!visible) return null;

  return (
    <div className="amplifier-card">
      <div className="amplifier-header">
        <div className="amplifier-dot" />
        <span className="amplifier-name">Amplifier Agent</span>
        <span className="amplifier-status">{status}</span>
      </div>
      {categories && categories.length > 0 && (
        <div className="amplifier-categories">
          {categories.map((cat) => (
            <span key={cat} className="amplifier-tag">{cat.replace("_", " ")}</span>
          ))}
        </div>
      )}
      {summary && <div className="amplifier-summary">{summary}</div>}
    </div>
  );
}
