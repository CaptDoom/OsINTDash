import React, { useState, useEffect } from 'react';

interface Note {
  id: string;
  content: string;
  author: string;
  created_at: string;
}

interface SharedNotesProps {
  isDarkMode: boolean;
}

export default function SharedNotes({ isDarkMode }: SharedNotesProps) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [newNote, setNewNote] = useState('');
  const [customAuthor, setCustomAuthor] = useState('');
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  // Update expiry timers every 30 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      setTick((t) => t + 1);
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchNotes = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/notes');
      if (res.ok) {
        const data = await res.json();
        setNotes(data);
      } else {
        const errText = await res.text();
        setError(`Failed to fetch notes: ${errText || res.statusText}`);
      }
    } catch (err) {
      setError(`Network error fetching notes: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchNotes();
  }, []);

  const handleAddNote = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newNote.trim()) return;

    setPosting(true);
    setError(null);
    try {
      const res = await fetch('/api/notes', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          content: newNote.trim(),
          author: customAuthor.trim() || undefined,
        }),
      });

      if (res.ok) {
        const note = await res.json();
        setNotes((prev) => [note, ...prev]);
        setNewNote('');
        setCustomAuthor('');
      } else {
        const errData = await res.json();
        setError(errData.detail || 'Failed to post note.');
      }
    } catch (err) {
      setError(`Network error posting note: ${String(err)}`);
    } finally {
      setPosting(false);
    }
  };

  const handleDeleteNote = async (id: string) => {
    try {
      const res = await fetch(`/api/notes/${id}`, { method: 'DELETE' });
      if (res.ok) {
        setNotes((prev) => prev.filter((n) => n.id !== id));
      } else {
        setError('Failed to delete note.');
      }
    } catch (err) {
      setError(`Error deleting note: ${String(err)}`);
    }
  };

  const getExpiresIn = (createdAtStr: string) => {
    const created = new Date(createdAtStr);
    const expiry = new Date(created.getTime() + 24 * 60 * 60 * 1000);
    const now = new Date();
    const diffMs = expiry.getTime() - now.getTime();

    if (diffMs <= 0) return 'Expired';

    const hours = Math.floor(diffMs / (3600 * 1000));
    const minutes = Math.floor((diffMs % (3600 * 1000)) / (60 * 1000));
    return `${hours}h ${minutes}m remaining`;
  };

  return (
    <div className={`space-y-6 ${isDarkMode ? 'text-[#d4e4fa]' : 'text-slate-800'}`}>
      <div className="flex justify-between items-center border-b border-white/20 pb-3">
        <div>
          <h2 className="text-xl font-bold uppercase tracking-wider text-[#7bd0ff]">Collaborative Shared Notes</h2>
          <p className="text-xs opacity-70 mt-1">
            Real-time feed for strategic alerts. All pins self-destruct exactly 24 hours post-creation.
          </p>
        </div>
        <button
          onClick={fetchNotes}
          className="flex items-center gap-1.5 border border-white/20 hover:bg-white/10 px-3 py-1.5 text-xs font-mono uppercase tracking-wider rounded transition-colors"
        >
          <span className="material-symbols-outlined text-sm">refresh</span>
          Refresh
        </button>
      </div>

      {error && (
        <div className="bg-red-950/40 border border-red-900/60 text-red-400 p-3 rounded text-xs font-mono flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="hover:text-white">✕</button>
        </div>
      )}

      {/* Add Note Form */}
      <form onSubmit={handleAddNote} className={`border p-4 rounded space-y-3 ${isDarkMode ? 'border-white/10 bg-[#122131]/10' : 'border-slate-200 bg-slate-50'}`}>
        <h3 className="text-xs font-mono uppercase tracking-widest text-[#7bd0ff] flex items-center gap-1.5">
          <span className="material-symbols-outlined text-base font-bold">add_circle</span>
          Add Geopolitical Update
        </h3>
        
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="md:col-span-2">
            <textarea
              required
              rows={3}
              value={newNote}
              onChange={(e) => setNewNote(e.target.value)}
              placeholder="Paste intelligence report, briefing updates, or news summaries..."
              className={`w-full text-xs p-2.5 rounded border focus:outline-none focus:border-[#7bd0ff] ${
                isDarkMode ? 'bg-black/40 border-white/20 text-white' : 'bg-white border-slate-300 text-black'
              }`}
            />
          </div>
          <div className="space-y-3">
            <input
              type="text"
              value={customAuthor}
              onChange={(e) => setCustomAuthor(e.target.value)}
              placeholder="Author (e.g. CEO, STRATCOM Ops)"
              className={`w-full text-xs p-2.5 rounded border focus:outline-none focus:border-[#7bd0ff] ${
                isDarkMode ? 'bg-black/40 border-white/20 text-white' : 'bg-white border-slate-300 text-black'
              }`}
            />
            <button
              type="submit"
              disabled={posting || !newNote.trim()}
              className={`w-full py-2.5 text-xs font-mono uppercase tracking-wider rounded font-bold transition-all ${
                posting || !newNote.trim()
                  ? 'bg-white/5 border border-white/10 text-white/30 cursor-not-allowed'
                  : 'bg-[#7bd0ff]/10 hover:bg-[#7bd0ff]/20 text-[#7bd0ff] border border-[#7bd0ff]/40 shadow-sm'
              }`}
            >
              {posting ? 'Broadcasting...' : 'Pin to Shared Feed'}
            </button>
          </div>
        </div>
      </form>

      {/* Shared Notes Feed */}
      <div className="space-y-3">
        <h3 className="text-xs uppercase font-mono tracking-widest text-[#7bd0ff]">
          Active Broadcasts ({notes.length})
        </h3>

        {loading && notes.length === 0 ? (
          <div className="space-y-3">
            {[1, 2].map((n) => (
              <div key={n} className="h-20 rounded border border-white/10 loading-shimmer-bg" />
            ))}
          </div>
        ) : notes.length === 0 ? (
          <div className="border border-white/10 p-8 text-center rounded">
            <span className="material-symbols-outlined text-3xl opacity-50">campaign</span>
            <p className="text-sm mt-2 opacity-70">No active shared updates in the last 24 hours.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {notes.map((note) => {
              const expireText = getExpiresIn(note.created_at);
              return (
                <div
                  key={note.id}
                  className={`border border-[#7bd0ff]/20 bg-[#122131]/20 p-4 rounded hover:border-[#7bd0ff]/40 transition-colors relative`}
                >
                  <div className="flex justify-between items-start gap-4">
                    <div className="space-y-1">
                      <div className="text-xs font-mono font-bold text-[#7bd0ff]">
                        {note.author}
                      </div>
                      <div className="text-[10px] opacity-50 font-mono">
                        Pinned {new Date(note.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} ({new Date(note.created_at).toLocaleDateString()})
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[9px] font-mono bg-red-950/60 text-red-400 border border-red-900/60 px-2 py-0.5 rounded uppercase">
                        {expireText}
                      </span>
                      <button
                        onClick={() => handleDeleteNote(note.id)}
                        className="text-white/35 hover:text-red-400 p-0.5 transition-colors"
                        title="Dismiss note"
                      >
                        <span className="material-symbols-outlined text-base">delete</span>
                      </button>
                    </div>
                  </div>
                  <div className="text-xs mt-3 leading-relaxed whitespace-pre-wrap font-mono text-[#bec6e0] border-t border-white/10 pt-2.5">
                    {note.content}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <span style={{ display: 'none' }}>{tick}</span>
    </div>
  );
}
