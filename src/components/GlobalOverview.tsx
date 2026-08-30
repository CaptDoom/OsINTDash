import { RiskLevelBadge, type ThreatLevel, getRiskConfig } from './RiskLevel';

type GlobalOverviewProps = {
  totalCountries: number;
  criticalCount: number;
  highCount: number;
  moderateCount: number;
  lowCount: number;
  totalSignals: number;
  lastUpdated: string;
  isLive: boolean;
};

export function GlobalOverview({
  totalCountries,
  criticalCount,
  highCount,
  moderateCount,
  lowCount,
  totalSignals,
  lastUpdated,
  isLive,
}: GlobalOverviewProps) {
  const avgRisk: ThreatLevel =
    criticalCount > 0 ? 'Critical' :
    highCount > criticalCount ? 'High' :
    moderateCount > highCount ? 'Moderate' : 'Low';

  const metrics = [
    {
      label: 'COUNTRIES',
      value: totalCountries,
      sublabel: 'MONITORED',
      icon: 'public',
      color: '#7bd0ff',
    },
    {
      label: 'CRITICAL',
      value: criticalCount,
      sublabel: 'ZONES',
      icon: 'error',
      color: '#D32F2F',
    },
    {
      label: 'HIGH RISK',
      value: highCount,
      sublabel: 'ZONES',
      icon: 'warning',
      color: '#F57C00',
    },
    {
      label: 'SIGNALS',
      value: totalSignals,
      sublabel: 'TOTAL',
      icon: 'sensors',
      color: '#4edea3',
    },
  ];

  return (
    <div className="grid-overview-container">
      {/* Global Risk Summary Bar */}
      <div className="global-risk-bar">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${isLive ? 'bg-[#4edea3] animate-pulse' : 'bg-[#ffb4ab]'}`}
            />
            <span className="font-mono text-[10px] uppercase tracking-widest text-[#7bd0ff]">
              GLOBAL STATUS
            </span>
          </div>
          <RiskLevelBadge level={avgRisk} size="sm" pulse={avgRisk === 'Critical'} />
        </div>

        <div className="flex items-center gap-4">
          <div className="risk-counts">
            {([
              { level: 'Critical' as ThreatLevel, count: criticalCount },
              { level: 'High' as ThreatLevel, count: highCount },
              { level: 'Moderate' as ThreatLevel, count: moderateCount },
              { level: 'Low' as ThreatLevel, count: lowCount },
            ]).map(({ level, count }) => {
              const cfg = getRiskConfig(level);
              return (
                <div key={level} className="flex items-center gap-1.5">
                  <span
                    className="w-1.5 h-1.5 rounded-full"
                    style={{ background: cfg.color }}
                  />
                  <span className="font-mono text-[10px] opacity-70">{count}</span>
                </div>
              );
            })}
          </div>

          {lastUpdated && (
            <span className="font-mono text-[9px] text-[#c6c6cd] opacity-50 hidden md:block">
              UPDATED {new Date(lastUpdated).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          )}
        </div>
      </div>

      {/* Metric Cards Grid */}
      <div className="global-metrics-grid">
        {metrics.map((m) => (
          <div key={m.label} className="global-metric-card">
            <div className="flex items-center gap-1.5 mb-1">
              <span className="material-symbols-outlined text-[14px]" style={{ color: m.color }}>
                {m.icon}
              </span>
              <span className="global-metric-label">{m.label}</span>
            </div>
            <div className="global-metric-value" style={{ color: m.color }}>
              {m.value}
            </div>
            <span className="global-metric-sublabel">{m.sublabel}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
