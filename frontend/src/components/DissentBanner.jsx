export default function DissentBanner({ flags }) {
  if (!flags || flags.length === 0) return null;

  return (
    <div className="dissent-banner">
      <h4>Minority Report</h4>
      {flags.map((f, i) => (
        <div key={i} className="dissent-item">
          <strong>{f.agent_name}</strong> — {f.concern}
        </div>
      ))}
    </div>
  );
}
