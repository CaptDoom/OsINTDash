import { useEffect, useState, useMemo, useRef } from 'react';

interface IntelligenceDashboardProps {
  isDarkMode: boolean;
}

interface LiveAlert {
  id: string;
  country: string;
  headline: string;
  source: string;
  category: string;
  impact: 'High' | 'Medium' | 'Low';
  timestamp: string;
  url?: string;
}

interface ImpactBreakdown {
  high: number;
  medium: number;
  normal: number;
}

interface CountryCount {
  code: string;
  name: string;
  count: number;
}

interface DailyCount {
  total: number;
  high: number;
  medium: number;
  normal: number;
}

interface SourceHealthEntry {
  article_count: number;
  high_impact_ratio: number;
  reputation_score: number;
  tier: string;
}

interface TopStory {
  id: string;
  title: string;
  summary: string;
  country: string;
  department: string;
  source: string;
  url: string;
  timestamp: string;
  corroborated_by: number;
}

interface DashboardData {
  period_days: number;
  total_articles: number;
  impact_breakdown: ImpactBreakdown;
  by_department: Record<string, number>;
  top_countries: CountryCount[];
  trend: {
    direction: 'rising' | 'stable' | 'falling';
    daily: Record<string, DailyCount>;
  };
  source_health: Record<string, SourceHealthEntry>;
  top_stories: TopStory[];
  generated_at: string;
}

const TIER_LABELS: Record<string, string> = {
  tier_1_wire: 'Tier 1 Wire',
  tier_2_major: 'Tier 2 Major',
  tier_3_regional: 'Tier 3 Regional',
  tier_4_aggregator: 'Tier 4 Aggregator',
  tier_5_unverified: 'Tier 5 Unverified',
};

const TIER_COLORS: Record<string, string> = {
  tier_1_wire: 'text-green-400',
  tier_2_major: 'text-blue-400',
  tier_3_regional: 'text-yellow-400',
  tier_4_aggregator: 'text-orange-400',
  tier_5_unverified: 'text-red-400',
};

const DEPT_ICONS: Record<string, string> = {
  'Military & Defense': '🛡️',
  'Economic & Financial': '💰',
  'Political & Diplomatic': '🤝',
  'Social Affairs & Welfare': '👥',
  'Technology & Cyber': '💻',
};



function TrendBadge({ direction }: { direction: string }) {
  const config = {
    rising: { label: '▲ RISING', color: 'text-red-400 bg-red-950/40 border-red-900/60' },
    falling: { label: '▼ FALLING', color: 'text-green-400 bg-green-950/40 border-green-900/60' },
    stable: { label: '● STABLE', color: 'text-blue-400 bg-blue-950/40 border-blue-900/60' },
  }[direction] || { label: '● UNKNOWN', color: 'text-gray-400 bg-gray-950/40 border-gray-900/60' };

  return (
    <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${config.color}`}>
      {config.label}
    </span>
  );
}

export default function IntelligenceDashboard({ isDarkMode }: IntelligenceDashboardProps) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState(7);
  const [liveAlerts, setLiveAlerts] = useState<LiveAlert[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const liveAlertsRef = useRef<LiveAlert[]>([]);

  // WebSocket connection for live alerts
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.hostname}:3001`;
    let socket: WebSocket;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      try {
        socket = new WebSocket(wsUrl);

        socket.onopen = () => {
          setWsConnected(true);
          // Subscribe to alerts channel
          socket.send(JSON.stringify({ type: 'subscribe', channel: 'alerts' }));
        };

        socket.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'signal' && msg.signal) {
              const sig = msg.signal;
              const alert: LiveAlert = {
                id: sig.id || `live-${Date.now()}`,
                country: sig.country || 'Unknown',
                headline: sig.headline || sig.title || '',
                source: sig.source || 'News Feed',
                category: sig.category || sig.intel_category || 'Unknown',
                impact: sig.impact || 'Low',
                timestamp: sig.timestamp || new Date().toISOString(),
                url: sig.url,
              };
              liveAlertsRef.current = [alert, ...liveAlertsRef.current].slice(0, 50);
              setLiveAlerts([...liveAlertsRef.current]);
            }
          } catch { /* ignore parse errors */ }
        };

        socket.onclose = () => {
          setWsConnected(false);
          reconnectTimer = setTimeout(connect, 5000);
        };

        socket.onerror = () => {
          socket.close();
        };
      } catch {
        reconnectTimer = setTimeout(connect, 5000);
      }
    }

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      try { socket.close(); } catch { /* ignore */ }
    };
  }, []);

  // Fetch dashboard data
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch(`/api/intelligence/dashboard?days=${period}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(String(err));
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [period]);

  // Build daily trend chart data
  const trendData = useMemo(() => {
    if (!data?.trend?.daily) return [];
    return Object.entries(data.trend.daily)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, counts]) => ({ date: date.slice(5), ...counts }));
  }, [data]);

  const barMax = useMemo(() => {
    if (!trendData.length) return 1;
    return Math.max(...trendData.map((d) => d.total), 1);
  }, [trendData]);

  // Merge live alerts into impact counts
  const liveHighCount = liveAlerts.filter((a) => a.impact === 'High').length;
  const liveMediumCount = liveAlerts.filter((a) => a.impact === 'Medium').length;
  const liveTotalCount = liveAlerts.length;

  const isDark = isDarkMode;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[500px]">
        <div className="flex flex-col items-center gap-3">
          <span className="w-8 h-8 rounded-full border-2 border-[#7bd0ff] border-t-transparent animate-spin" />
          <p className="text-xs font-mono text-[#7bd0ff] animate-pulse">Loading intelligence data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-950/40 border border-red-900/60 text-red-400 p-4 rounded text-xs font-mono">
        Failed to load dashboard: {error}
      </div>
    );
  }

  if (!data) return null;

  const impactTotal = data.total_articles || 1;

  return (
    <div className={`space-y-5 ${isDark ? 'text-[#d4e4fa]' : 'text-slate-800'}`}>
      {/* Header */}
      <div className="border-b border-white/20 pb-3 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold uppercase tracking-wider text-[#7bd0ff] flex items-center gap-2">
            <span className="material-symbols-outlined text-lg">dashboard</span>
            Threat Intelligence Dashboard
            {wsConnected && (
              <span className="flex items-center gap-1.5 text-[10px] font-mono text-green-400 ml-2">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                LIVE
              </span>
            )}
          </h2>
          <p className="text-xs opacity-70 mt-1">
            Aggregated metrics, trend analysis, source health, and top stories.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {[7, 14, 30].map((d) => (
            <button
              key={d}
              onClick={() => setPeriod(d)}
              className={`text-[10px] font-mono px-2 py-1 rounded border transition-colors ${
                period === d
                  ? 'bg-white text-black border-white'
                  : 'border-white/10 hover:bg-white/5'
              }`}
            >
              {d}D
            </button>
          ))}
        </div>
      </div>

      {/* KPI Cards Row */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <KPICard
          label="Total Articles"
          value={data.total_articles + liveTotalCount}
          icon="article"
          liveDelta={liveTotalCount > 0 ? `+${liveTotalCount} live` : undefined}
          isDark={isDark}
        />
        <KPICard
          label="High Impact"
          value={data.impact_breakdown.high + liveHighCount}
          icon="warning"
          valueColor="text-red-400"
          liveDelta={liveHighCount > 0 ? `+${liveHighCount} live` : undefined}
          isDark={isDark}
        />
        <KPICard
          label="Medium Impact"
          value={data.impact_breakdown.medium + liveMediumCount}
          icon="info"
          valueColor="text-yellow-400"
          liveDelta={liveMediumCount > 0 ? `+${liveMediumCount} live` : undefined}
          isDark={isDark}
        />
        <KPICard
          label="Active Sources"
          value={Object.keys(data.source_health).length}
          icon="source"
          valueColor="text-[#7bd0ff]"
          isDark={isDark}
        />
        <KPICard
          label="Trend"
          value={data.trend.direction.toUpperCase()}
          icon="trending_up"
          valueColor={
            data.trend.direction === 'rising'
              ? 'text-red-400'
              : data.trend.direction === 'falling'
              ? 'text-green-400'
              : 'text-blue-400'
          }
          isDark={isDark}
        />
      </div>

      {/* Two-Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left: Trend Chart + Department Breakdown */}
        <div className="lg:col-span-2 space-y-4">
          {/* Daily Trend Chart */}
          <div className={`border p-4 rounded ${isDark ? 'border-white/10 bg-black/40' : 'border-slate-200 bg-white'}`}>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-[10px] font-mono uppercase tracking-widest text-[#7bd0ff] flex items-center gap-1.5">
                <span className="material-symbols-outlined text-[12px]">show_chart</span>
                Daily Activity Trend
              </h3>
              <TrendBadge direction={data.trend.direction} />
            </div>
            {trendData.length > 0 ? (
              <div className="space-y-1">
                {/* Bar chart */}
                <div className="flex items-end gap-1 h-[120px]">
                  {trendData.map((d, i) => {
                    const totalH = (d.total / barMax) * 100;
                    const highH = (d.high / barMax) * 100;
                    const medH = (d.medium / barMax) * 100;
                    return (
                      <div key={i} className="flex-1 flex flex-col items-center gap-0.5 group relative">
                        <div className="absolute -top-8 left-1/2 -translate-x-1/2 hidden group-hover:block z-10 bg-black/90 border border-white/20 text-[8px] font-mono p-1.5 rounded whitespace-nowrap">
                          <div>{d.date}</div>
                          <div className="text-red-400">High: {d.high}</div>
                          <div className="text-yellow-400">Med: {d.medium}</div>
                          <div className="text-blue-400">Low: {d.normal}</div>
                        </div>
                        <div className="w-full flex flex-col justify-end" style={{ height: '100px' }}>
                          <div className="w-full bg-red-500/70 rounded-t" style={{ height: `${highH}%` }} />
                          <div className="w-full bg-yellow-500/60" style={{ height: `${medH}%` }} />
                          <div className="w-full bg-blue-500/40 rounded-b" style={{ height: `${Math.max(totalH - highH - medH, 0)}%` }} />
                        </div>
                        <span className="text-[7px] font-mono opacity-50">{d.date}</span>
                      </div>
                    );
                  })}
                </div>
                {/* Legend */}
                <div className="flex gap-3 text-[8px] font-mono mt-1 justify-center">
                  <span className="flex items-center gap-1"><span className="w-2 h-2 bg-red-500/70 rounded" /> High</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-2 bg-yellow-500/60 rounded" /> Medium</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-2 bg-blue-500/40 rounded" /> Low</span>
                </div>
              </div>
            ) : (
              <p className="text-xs opacity-50 text-center py-8">No trend data available</p>
            )}
          </div>

          {/* Department Breakdown */}
          <div className={`border p-4 rounded ${isDark ? 'border-white/10 bg-black/40' : 'border-slate-200 bg-white'}`}>
            <h3 className="text-[10px] font-mono uppercase tracking-widest text-[#7bd0ff] mb-3 flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[12px]">category</span>
              Department Breakdown
            </h3>
            <div className="space-y-2">
              {Object.entries(data.by_department)
                .sort(([, a], [, b]) => b - a)
                .map(([dept, count]) => {
                  const pct = (count / impactTotal) * 100;
                  const icon = DEPT_ICONS[dept] || '📰';
                  return (
                    <div key={dept} className="flex items-center gap-2">
                      <span className="text-sm w-5 text-center">{icon}</span>
                      <span className="text-[10px] font-mono w-[140px] truncate">{dept}</span>
                      <div className="flex-1 h-2 bg-white/5 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-[#7bd0ff]/60 rounded-full transition-all duration-500"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-mono text-[#7bd0ff] w-16 text-right">{count} ({pct.toFixed(0)}%)</span>
                    </div>
                  );
                })}
            </div>
          </div>

          {/* Top Countries */}
          <div className={`border p-4 rounded ${isDark ? 'border-white/10 bg-black/40' : 'border-slate-200 bg-white'}`}>
            <h3 className="text-[10px] font-mono uppercase tracking-widest text-[#7bd0ff] mb-3 flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[12px]">public</span>
              Top Countries
            </h3>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
              {data.top_countries.map((c) => (
                <div
                  key={c.code}
                  className={`p-2 rounded border text-center ${isDark ? 'border-white/10 bg-white/5' : 'border-slate-200 bg-slate-50'}`}
                >
                  <div className="text-[10px] font-mono text-[#7bd0ff]">{c.code}</div>
                  <div className="text-xs font-bold mt-0.5 truncate">{c.name}</div>
                  <div className="text-[10px] font-mono opacity-60">{c.count} articles</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right Column */}
        <div className="space-y-4">
          {/* Live Alerts Feed */}
          <div className={`border p-4 rounded ${isDark ? 'border-white/10 bg-black/40' : 'border-slate-200 bg-white'}`}>
            <h3 className="text-[10px] font-mono uppercase tracking-widest text-[#7bd0ff] mb-3 flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[12px]">notifications_active</span>
              Live Alert Feed
              {wsConnected && (
                <span className="ml-auto flex items-center gap-1 text-[8px] text-green-400">
                  <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse" />
                  STREAMING
                </span>
              )}
            </h3>
            <div className="space-y-2 max-h-[250px] overflow-y-auto">
              {liveAlerts.length === 0 ? (
                <p className="text-[10px] opacity-50 text-center py-4 font-mono">
                  {wsConnected ? 'Listening for live signals...' : 'WebSocket connecting...'}
                </p>
              ) : (
                liveAlerts.map((alert) => (
                  <a
                    key={alert.id}
                    href={alert.url || '#'}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`block p-2 rounded border transition-colors ${
                      isDark ? 'border-white/5 bg-white/5 hover:border-[#7bd0ff]/30' : 'border-slate-100 bg-slate-50 hover:border-blue-300'
                    }`}
                  >
                    <div className="flex items-center gap-1.5">
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                        alert.impact === 'High' ? 'bg-red-400' : alert.impact === 'Medium' ? 'bg-yellow-400' : 'bg-blue-400'
                      }`} />
                      <span className="text-[10px] font-mono font-bold leading-tight line-clamp-1 flex-1">
                        {alert.headline}
                      </span>
                      <span className="text-[7px] font-mono opacity-50 shrink-0">
                        {alert.impact === 'High' ? '🔴' : alert.impact === 'Medium' ? '🟡' : '🔵'}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 mt-1 text-[8px] font-mono opacity-50">
                      <span>{alert.country}</span>
                      <span>·</span>
                      <span>{alert.category}</span>
                      <span>·</span>
                      <span>{alert.source}</span>
                    </div>
                  </a>
                ))
              )}
            </div>
          </div>

          {/* Source Health */}
          <div className={`border p-4 rounded ${isDark ? 'border-white/10 bg-black/40' : 'border-slate-200 bg-white'}`}>
            <h3 className="text-[10px] font-mono uppercase tracking-widest text-[#7bd0ff] mb-3 flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[12px]">verified</span>
              Source Health
            </h3>
            <div className="space-y-2 max-h-[300px] overflow-y-auto">
              {Object.entries(data.source_health)
                .sort(([, a], [, b]) => b.reputation_score - a.reputation_score)
                .map(([source, info]) => (
                  <div
                    key={source}
                    className={`p-2 rounded border text-xs ${isDark ? 'border-white/5 bg-white/5' : 'border-slate-100 bg-slate-50'}`}
                  >
                    <div className="flex justify-between items-center">
                      <span className="font-mono font-bold truncate max-w-[140px]">{source}</span>
                      <span className={`font-mono text-[8px] ${TIER_COLORS[info.tier] || 'text-gray-400'}`}>
                        {TIER_LABELS[info.tier] || info.tier}
                      </span>
                    </div>
                    <div className="flex justify-between mt-1 text-[9px] font-mono opacity-60">
                      <span>{info.article_count} articles</span>
                      <span>Rep: {(info.reputation_score * 100).toFixed(0)}%</span>
                      <span>High: {(info.high_impact_ratio * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                ))}
            </div>
          </div>

          {/* Top Stories */}
          <div className={`border p-4 rounded ${isDark ? 'border-white/10 bg-black/40' : 'border-slate-200 bg-white'}`}>
            <h3 className="text-[10px] font-mono uppercase tracking-widest text-[#7bd0ff] mb-3 flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[12px]">breaking_news_alt_1</span>
              Top Stories
            </h3>
            <div className="space-y-2 max-h-[400px] overflow-y-auto">
              {data.top_stories.length === 0 ? (
                <p className="text-[10px] opacity-50 text-center py-4">No high-impact stories in this period</p>
              ) : (
                data.top_stories.map((story) => (
                  <a
                    key={story.id}
                    href={story.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`block p-2.5 rounded border transition-colors ${
                      isDark
                        ? 'border-white/5 bg-white/5 hover:border-[#7bd0ff]/30'
                        : 'border-slate-100 bg-slate-50 hover:border-blue-300'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-[10px] font-mono font-bold leading-tight line-clamp-2">{story.title}</span>
                      {story.corroborated_by > 0 && (
                        <span className="text-[8px] font-mono bg-green-950/60 text-green-400 border border-green-900/40 px-1.5 py-0.5 rounded shrink-0">
                          {story.corroborated_by} sources
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 mt-1.5 text-[8px] font-mono opacity-60">
                      <span>{story.country}</span>
                      <span>·</span>
                      <span>{story.department.split(' ')[0]}</span>
                      <span>·</span>
                      <span>{story.source}</span>
                    </div>
                  </a>
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="text-center text-[9px] font-mono opacity-40 pt-2">
        Generated: {new Date(data.generated_at).toLocaleString()} · Period: {data.period_days} days
      </div>
    </div>
  );
}

function KPICard({ label, value, icon, valueColor = '', liveDelta, isDark }: {
  label: string;
  value: string | number;
  icon: string;
  valueColor?: string;
  liveDelta?: string;
  isDark: boolean;
}) {
  return (
    <div className={`border p-3 rounded ${isDark ? 'border-white/10 bg-black/40' : 'border-slate-200 bg-white'}`}>
      <div className="flex items-center gap-1.5 mb-1">
        <span className="material-symbols-outlined text-[10px] opacity-60">{icon}</span>
        <span className="text-[9px] font-mono uppercase tracking-wider opacity-60">{label}</span>
      </div>
      <div className="flex items-baseline gap-2">
        <div className={`text-xl font-bold font-mono ${valueColor || ''}`}>
          {value}
        </div>
        {liveDelta && (
          <span className="text-[8px] font-mono text-green-400 bg-green-950/40 px-1.5 py-0.5 rounded border border-green-900/40">
            {liveDelta}
          </span>
        )}
      </div>
    </div>
  );
}
