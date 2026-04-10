import { useState, useEffect } from "react";

const CATEGORIES = [
  { key: "personality", label: "Personality" },
  { key: "goals", label: "Goals & Aspirations" },
  { key: "values", label: "Values & Principles" },
  { key: "blind_spots", label: "Blind Spots & Patterns" },
  { key: "context_log", label: "Context Log" },
  { key: "relationships", label: "Key Relationships" },
  { key: "challenges", label: "Current Challenges" },
];

export default function KnowledgeBase() {
  const [entries, setEntries] = useState([]);
  const [category, setCategory] = useState("personality");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchEntries = async () => {
    try {
      const res = await fetch("/knowledge");
      const data = await res.json();
      setEntries(data.entries || []);
    } catch {
      // DB might not be available
    }
  };

  useEffect(() => {
    fetchEntries();
  }, []);

  const handleSubmit = async () => {
    if (!title.trim() || !content.trim()) return;
    setLoading(true);

    try {
      if (editing) {
        await fetch(`/knowledge/${editing}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: title.trim(), content: content.trim() }),
        });
      } else {
        await fetch("/knowledge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ category, title: title.trim(), content: content.trim() }),
        });
      }
      setTitle("");
      setContent("");
      setEditing(null);
      await fetchEntries();
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const handleEdit = (entry) => {
    setEditing(entry.id);
    setCategory(entry.category);
    setTitle(entry.title);
    setContent(entry.content);
  };

  const handleDelete = async (id) => {
    try {
      await fetch(`/knowledge/${id}`, { method: "DELETE" });
      await fetchEntries();
    } catch {
      // ignore
    }
  };

  const grouped = {};
  for (const e of entries) {
    if (!grouped[e.category]) grouped[e.category] = [];
    grouped[e.category].push(e);
  }

  return (
    <div className="kb-page">
      <div className="kb-add-form">
        <h3>{editing ? "Edit Entry" : "Add to Knowledge Base"}</h3>
        <div className="kb-form-row">
          <select value={category} onChange={(e) => setCategory(e.target.value)} disabled={!!editing}>
            {CATEGORIES.map((c) => (
              <option key={c.key} value={c.key}>{c.label}</option>
            ))}
          </select>
          <input
            type="text"
            placeholder="Title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <textarea
          placeholder="Content — the more specific and honest, the sharper your council becomes..."
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <div className="submit-row">
          {editing && (
            <button className="reset-btn" onClick={() => { setEditing(null); setTitle(""); setContent(""); }}>
              Cancel
            </button>
          )}
          <button className="submit-btn" onClick={handleSubmit} disabled={loading || !title.trim() || !content.trim()}>
            {editing ? "Update" : "Add entry"}
          </button>
        </div>
      </div>

      {CATEGORIES.map((cat) => {
        const items = grouped[cat.key];
        if (!items || items.length === 0) return null;
        return (
          <div key={cat.key} className="kb-category">
            <h3>{cat.label}</h3>
            {items.map((entry) => (
              <div key={entry.id} className="kb-entry">
                <div className="kb-entry-header">
                  <span className="kb-entry-title">{entry.title}</span>
                  <div className="kb-entry-actions">
                    <button onClick={() => handleEdit(entry)}>edit</button>
                    <button className="delete" onClick={() => handleDelete(entry.id)}>delete</button>
                  </div>
                </div>
                <div className="kb-entry-content">{entry.content}</div>
              </div>
            ))}
          </div>
        );
      })}

      {entries.length === 0 && (
        <p style={{ textAlign: "center", color: "var(--text-muted)", marginTop: "2rem" }}>
          Your knowledge base is empty. Add entries above to give your council context about who you are.
        </p>
      )}
    </div>
  );
}
