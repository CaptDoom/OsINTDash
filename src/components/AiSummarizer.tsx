import React, { useState } from 'react';

interface AiSummarizerProps {
  isDarkMode: boolean;
}

interface ArticleMeta {
  id: string;
  title: string;
  url: string;
  source: string;
  department: string;
  impact_level: string;
  published_at: string;
}

interface StreamMetadata {
  country: string;
  timeframe: string;
  total_articles: number;
  high_impact: number;
  medium_impact: number;
  sources: string[];
  articles: ArticleMeta[];
}

const COUNTRIES = [
  { code: 'CN', name: 'China' },
  { code: 'IN', name: 'India' },
  { code: 'PK', name: 'Pakistan' },
  { code: 'AF', name: 'Afghanistan' },
  { code: 'BD', name: 'Bangladesh' },
  { code: 'MM', name: 'Myanmar' },
  { code: 'NP', name: 'Nepal' },
  { code: 'BT', name: 'Bhutan' },
  { code: 'LK', name: 'Sri Lanka' },
  { code: 'MV', name: 'Maldives' },
  { code: 'US', name: 'United States' },
  { code: 'RU', name: 'Russia' },
  { code: 'IR', name: 'Iran' },
  { code: 'IL', name: 'Israel' },
  { code: 'TW', name: 'Taiwan' },
  { code: 'UA', name: 'Ukraine' },
];

export default function AiSummarizer({ isDarkMode }: AiSummarizerProps) {
  const [selectedCountry, setSelectedCountry] = useState('CN');
  const [selectedTimeframe, setSelectedTimeframe] = useState<'1M' | '6M' | '1Y'>('1M');
  const [urls, setUrls] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [generating, setGenerating] = useState(false);
  const [summary, setSummary] = useState('');
  const [metadata, setMetadata] = useState<StreamMetadata | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streamingPhase, setStreamingPhase] = useState<'idle' | 'fetching' | 'streaming' | 'done'>('idle');

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const selectedFiles = Array.from(e.target.files);
      setFiles((prev) => [...prev, ...selectedFiles]);
    }
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    setGenerating(true);
    setSummary('');
    setMetadata(null);
    setError(null);
    setStreamingPhase('fetching');

    const formData = new FormData();
    formData.append('country_code', selectedCountry);
    formData.append('timeframe', selectedTimeframe);
    if (urls.trim()) {
      formData.append('urls', urls);
    }
    files.forEach((file) => {
      formData.append('files', file);
    });

    try {
      const res = await fetch('/api/summarizer/stream', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const errText = await res.text();
        setError(`Failed to generate summary: ${errText || res.statusText}`);
        setGenerating(false);
        setStreamingPhase('idle');
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        setError('Streaming not supported by browser.');
        setGenerating(false);
        setStreamingPhase('idle');
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';
      let fullText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        let eventType = '';
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            const dataStr = line.slice(6);

            if (eventType === 'metadata') {
              try {
                const meta = JSON.parse(dataStr);
                setMetadata(meta);
                setStreamingPhase('streaming');
              } catch { /* ignore parse errors */ }
            } else if (eventType === 'token') {
              try {
                const tokenData = JSON.parse(dataStr);
                if (tokenData.text) {
                  fullText += tokenData.text;
                  setSummary(fullText);
                }
              } catch { /* ignore parse errors */ }
            } else if (eventType === 'done') {
              try {
                const doneData = JSON.parse(dataStr);
                if (doneData.summary) {
                  setSummary(doneData.summary);
                }
              } catch { /* use accumulated text */ }
              setStreamingPhase('done');
            }
            eventType = '';
          }
        }
      }
    } catch (err) {
      setError(`Network error calling summarizer: ${String(err)}`);
    } finally {
      setGenerating(false);
      setStreamingPhase('idle');
    }
  };

  return (
    <div className={`space-y-6 ${isDarkMode ? 'text-[#d4e4fa]' : 'text-slate-800'}`}>
      <div className="border-b border-white/20 pb-3">
        <h2 className="text-xl font-bold uppercase tracking-wider text-[#7bd0ff]">Real-Time AI Geopolitical Summarizer</h2>
        <p className="text-xs opacity-70 mt-1">
          Stream synthesized intelligence reports in real-time. Select target entity, timeframe, and upload optional documents or URLs.
        </p>
      </div>

      {error && (
        <div className="bg-red-950/40 border border-red-900/60 text-red-400 p-3 rounded text-xs font-mono flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="hover:text-white">✕</button>
        </div>
      )}

      <form onSubmit={handleGenerate} className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Selection & Context Ingest */}
        <div className={`lg:col-span-1 border p-5 rounded space-y-4 ${isDarkMode ? 'border-white/10 bg-[#122131]/10' : 'border-slate-200 bg-slate-50'}`}>
          <h3 className="text-xs font-mono uppercase tracking-widest text-[#7bd0ff] border-b border-white/10 pb-2 mb-3">
            Configuration
          </h3>

          {/* Country Selection */}
          <div className="space-y-1.5">
            <label className="text-[10px] font-mono uppercase tracking-wider opacity-70">Target Entity</label>
            <select
              value={selectedCountry}
              onChange={(e) => setSelectedCountry(e.target.value)}
              className={`w-full text-xs p-2 rounded border focus:outline-none focus:border-[#7bd0ff] ${
                isDarkMode ? 'bg-black/60 border-white/20 text-white' : 'bg-white border-slate-300 text-black'
              }`}
            >
              {COUNTRIES.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.name} ({c.code})
                </option>
              ))}
            </select>
          </div>

          {/* Timeframe Selection */}
          <div className="space-y-1.5">
            <label className="text-[10px] font-mono uppercase tracking-wider opacity-70">Timeframe Window</label>
            <div className="grid grid-cols-3 gap-2">
              {(['1M', '6M', '1Y'] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setSelectedTimeframe(t)}
                  className={`py-1.5 text-xs font-mono border rounded transition-colors ${
                    selectedTimeframe === t
                      ? 'bg-white text-black border-white'
                      : 'border-white/10 hover:bg-white/5'
                  }`}
                >
                  {t === '1M' ? '1 Month' : t === '6M' ? '6 Months' : '1 Year'}
                </button>
              ))}
            </div>
          </div>

          {/* Web Links */}
          <div className="space-y-1.5">
            <label className="text-[10px] font-mono uppercase tracking-wider opacity-70">External Links (comma-separated)</label>
            <input
              type="text"
              value={urls}
              onChange={(e) => setUrls(e.target.value)}
              placeholder="https://reuters.com/... , https://bbc.com/..."
              className={`w-full text-xs p-2 rounded border focus:outline-none focus:border-[#7bd0ff] ${
                isDarkMode ? 'bg-black/60 border-white/20 text-white' : 'bg-white border-slate-300 text-black'
              }`}
            />
          </div>

          {/* File Upload */}
          <div className="space-y-1.5">
            <label className="text-[10px] font-mono uppercase tracking-wider opacity-70">Upload Documents (PDF / Word / Text)</label>
            <div className="border border-dashed border-white/20 rounded p-4 text-center hover:border-[#7bd0ff]/40 transition-colors relative cursor-pointer">
              <input
                type="file"
                multiple
                accept=".pdf,.docx,.doc,.txt"
                onChange={handleFileChange}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
              />
              <span className="material-symbols-outlined text-2xl opacity-60">upload_file</span>
              <p className="text-[10px] mt-1 opacity-70">Drag & drop files or click to browse</p>
            </div>
            {files.length > 0 && (
              <div className="space-y-1.5 mt-2 max-h-[120px] overflow-y-auto border border-white/10 p-2 rounded bg-black/20">
                {files.map((file, idx) => (
                  <div key={idx} className="flex justify-between items-center text-[10px] font-mono p-1 border-b border-white/5 last:border-0">
                    <span className="truncate max-w-[150px]">{file.name}</span>
                    <button
                      type="button"
                      onClick={() => removeFile(idx)}
                      className="text-red-400 hover:text-red-300"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={generating}
            className={`w-full py-2.5 text-xs font-mono uppercase tracking-wider rounded font-bold transition-all mt-4 ${
              generating
                ? 'bg-white/5 border border-white/10 text-white/30 cursor-not-allowed'
                : 'bg-[#7bd0ff]/10 hover:bg-[#7bd0ff]/20 text-[#7bd0ff] border border-[#7bd0ff]/40 shadow-md'
            }`}
          >
            {generating ? (
              <span className="flex items-center justify-center gap-2">
                <span className="w-3 h-3 rounded-full border border-[#7bd0ff] border-t-transparent animate-spin" />
                {streamingPhase === 'fetching' ? 'Connecting...' : streamingPhase === 'streaming' ? 'Streaming...' : 'Compiling...'}
              </span>
            ) : 'Generate AI Briefing'}
          </button>
        </div>

        {/* Results Screen */}
        <div className="lg:col-span-2 flex flex-col min-h-[450px]">
          <div className={`flex-1 border p-5 rounded relative overflow-hidden flex flex-col ${
            isDarkMode ? 'border-white/10 bg-black/40' : 'border-slate-200 bg-white'
          }`}>
            <div className="scan-line" />
            <h3 className="text-xs font-mono uppercase tracking-widest text-[#7bd0ff] border-b border-white/10 pb-2 mb-3 flex items-center gap-1.5">
              <span className="material-symbols-outlined text-base">analytics</span>
              Operational Summary Briefing
              {streamingPhase === 'streaming' && (
                <span className="ml-auto flex items-center gap-1.5 text-[10px] text-green-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                  LIVE
                </span>
              )}
            </h3>

            {/* Stream Metadata Bar */}
            {metadata && (
              <div className={`mb-3 p-3 rounded border ${isDarkMode ? 'border-white/10 bg-[#0a1929]/60' : 'border-slate-200 bg-slate-50'}`}>
                <div className="flex flex-wrap gap-3 text-[10px] font-mono">
                  <span className="text-[#7bd0ff]">
                    <span className="material-symbols-outlined text-[10px] align-middle">public</span> {metadata.country}
                  </span>
                  <span className="opacity-60">|</span>
                  <span>{metadata.timeframe}</span>
                  <span className="opacity-60">|</span>
                  <span>{metadata.total_articles} articles</span>
                  <span className="opacity-60">|</span>
                  <span className="text-red-400">{metadata.high_impact} high</span>
                  <span className="text-yellow-400">{metadata.medium_impact} medium</span>
                  <span className="opacity-60">|</span>
                  <span className="opacity-70">Sources: {metadata.sources.slice(0, 5).join(', ')}{metadata.sources.length > 5 ? ` +${metadata.sources.length - 5}` : ''}</span>
                </div>
              </div>
            )}

            {generating && !summary ? (
              <div className="flex-1 flex flex-col items-center justify-center space-y-4">
                <span className="w-8 h-8 rounded-full border-2 border-[#7bd0ff] border-t-transparent animate-spin" />
                <p className="text-xs font-mono text-[#7bd0ff] animate-pulse">
                  {streamingPhase === 'fetching' ? 'CONNECTING TO INTELLIGENCE MESH...' : 'INGESTING SIGNALS & SYNTHESIZING...'}
                </p>
              </div>
            ) : summary ? (
              <div className="flex-1 text-xs leading-relaxed font-mono whitespace-pre-wrap overflow-y-auto text-[#bec6e0] max-h-[500px] pr-2">
                {summary}
                {streamingPhase === 'streaming' && (
                  <span className="inline-block w-1.5 h-3 bg-[#7bd0ff] animate-pulse ml-0.5 align-middle" />
                )}
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center text-center opacity-60">
                <span className="material-symbols-outlined text-4xl">travel_explore</span>
                <p className="text-sm mt-2 font-mono uppercase">Waiting for operational parameters...</p>
                <p className="text-[10px] mt-1">Select timeframe/target and trigger briefing sweep.</p>
              </div>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}
