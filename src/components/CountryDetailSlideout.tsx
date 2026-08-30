import { useEffect, useRef } from 'react';
import { RiskLevelBadge, type ThreatLevel, RiskLevelBar, getRiskConfig } from './RiskLevel';

type Signal = {
  id?: string;
  country: string;
  category: string;
  impact: string;
  headline: string;
  summary: string;
  source: string;
  timestamp: string;
  url?: string;
  verification_status?: string;
  confidence_score?: number;
  entities?: any;
  source_links?: { name: string; url: string }[];
  corroboration_status?: string;
};

type CountryDetailSlideoutProps = {
  isOpen: boolean;
  onClose: () => void;
  countryName: string;
  region: string;
  threatLevel: ThreatLevel;
  signalCount: number;
  stabilityIndex: number;
  riskProbability: number;
  operationalSummary: string;
  signals: Signal[];
  selectedCategory: string;
  onCategoryChange: (cat: string) => void;
};

const CATEGORIES = ['All', 'Political', 'Social', 'Tech', 'Economic', 'Military'];

function formatTimeAgo(timestamp: string) {
  const now = Date.now();
  const then = new Date(timestamp).getTime();
  const diffMin = Math.floor((now - then) / 60000);
  if (diffMin < 0) return 'Just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  const diffD = Math.floor(diffH / 24);
  return `${diffD}d ago`;
}

function getImpactColor(impact: string) {
  if (impact === 'High') return '#D32F2F';
  if (impact === 'Medium') return '#F57C00';
  return '#388E3C';
}

function getFlagEmoji(countryCode: string): string {
  if (!countryCode || countryCode.length !== 2) return '';
  const codePoints = countryCode.toUpperCase().split('').map((c) => 127397 + c.charCodeAt(0));
  try { return String.fromCodePoint(...codePoints); } catch { return ''; }
}

const COUNTRY_CODE_MAP: Record<string, string> = {
  China: 'CN', Pakistan: 'PK', Afghanistan: 'AF', Bangladesh: 'BD',
  Myanmar: 'MM', Nepal: 'NP', Bhutan: 'BT', 'Sri Lanka': 'LK',
  Maldives: 'MV', India: 'IN', 'United States': 'US', Russia: 'RU',
  Iran: 'IR', Israel: 'IL', Taiwan: 'TW', Japan: 'JP', Australia: 'AU',
  'United Kingdom': 'GB', Germany: 'DE', Ukraine: 'UA', 'South Korea': 'KR',
};

export function CountryDetailSlideout({
  isOpen,
  onClose,
  countryName,
  region,
  threatLevel,
  signalCount,
  stabilityIndex,
  riskProbability,
  operationalSummary,
  signals,
  selectedCategory,
  onCategoryChange,
}: CountryDetailSlideoutProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const config = getRiskConfig(threatLevel);
  const flag = getFlagEmoji(COUNTRY_CODE_MAP[countryName] || '');

  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [isOpen]);

  const filteredSignals = selectedCategory === 'All'
    ? signals
    : signals.filter((s) => s.category === selectedCategory);

  return (
    <>
      {/* Backdrop */}
      {isOpen && (
        <div
          className="slideout-backdrop"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      {/* Slide-out Panel */}
      <div
        ref={panelRef}
        className={`slideout-panel ${isOpen ? 'slideout-panel--open' : ''}`}
        role="dialog"
        aria-label={`${countryName} intelligence detail`}
        aria-hidden={!isOpen}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          className="slideout-close"
          aria-label="Close panel"
        >
          <span className="material-symbols-outlined">close</span>
        </button>

        {/* Header */}
        <div className="slideout-header">
          <div className="flex items-center gap-3">
            {flag && <span className="text-2xl">{flag}</span>}
            <div>
              <h2 className="slideout-title">{countryName}</h2>
              <span className="slideout-region">{region}</span>
            </div>
          </div>
          <RiskLevelBadge level={threatLevel} size="md" pulse={threatLevel === 'Critical'} />
        </div>

        <RiskLevelBar level={threatLevel} height={4} className="mb-4" />

        {/* Key Metrics */}
        <div className="slideout-metrics">
          <div className="slideout-metric">
            <span className="slideout-metric-value" style={{ color: config.color }}>{signalCount}</span>
            <span className="slideout-metric-label">Signals</span>
          </div>
          <div className="slideout-metric">
            <span className="slideout-metric-value text-[#4edea3]">{(stabilityIndex * 100).toFixed(0)}%</span>
            <span className="slideout-metric-label">Stability</span>
          </div>
          <div className="slideout-metric">
            <span className="slideout-metric-value" style={{
              color: riskProbability > 70 ? '#D32F2F' : riskProbability > 40 ? '#F57C00' : '#388E3C'
            }}>{riskProbability.toFixed(1)}</span>
            <span className="slideout-metric-label">Risk Score</span>
          </div>
        </div>

        {/* Operational Summary */}
        <div className="slideout-summary">
          <span className="slideout-section-label">INTEL SUMMARY</span>
          <p className="slideout-summary-text">{operationalSummary}</p>
        </div>

        {/* Category Filter */}
        <div className="slideout-categories">
          <span className="slideout-section-label">NEWS CATEGORIES</span>
          <div className="slideout-cat-chips">
            {CATEGORIES.map((cat) => (
              <button
                key={cat}
                className={`slideout-cat-chip ${selectedCategory === cat ? 'slideout-cat-chip--active' : ''}`}
                onClick={() => onCategoryChange(cat)}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>

        {/* Signals List */}
        <div className="slideout-signals">
          <span className="slideout-section-label">
            LATEST INTELLIGENCE ({filteredSignals.length})
          </span>
          <div className="slideout-signals-list">
            {filteredSignals.length === 0 ? (
              <div className="slideout-empty">
                <span className="material-symbols-outlined text-2xl opacity-40">search_off</span>
                <span className="text-xs opacity-50">No signals in this category</span>
              </div>
            ) : (
              filteredSignals.slice(0, 50).map((signal, idx) => (
                <a
                  key={signal.id || idx}
                  href={signal.url || '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="slideout-signal-card"
                >
                  <div className="flex items-start justify-between gap-2 mb-1">
                    <span className="slideout-signal-headline">{signal.headline}</span>
                    <span
                      className="slideout-signal-impact"
                      style={{
                        color: getImpactColor(signal.impact),
                        borderColor: `${getImpactColor(signal.impact)}44`,
                      }}
                    >
                      {signal.impact}
                    </span>
                  </div>
                  <div className="slideout-signal-meta">
                    <span>{signal.source}</span>
                    <span>{signal.category}</span>
                    <span>{formatTimeAgo(signal.timestamp)}</span>
                  </div>
                  <div className="slideout-signal-trust">
                    {signal.verification_status && (
                      <span className="flex items-center gap-1">
                        <span className="material-symbols-outlined" style={{ fontSize: '10px' }}>
                          {signal.verification_status === 'Verified Source' ? 'verified' : 'help_outline'}
                        </span>
                        <span>{signal.verification_status}</span>
                      </span>
                    )}
                    {signal.corroboration_status && (
                      <span className="flex items-center gap-1 ml-2">
                        <span className="material-symbols-outlined" style={{ fontSize: '10px' }}>
                          {signal.corroboration_status === 'verified' ? 'fact_check' : signal.corroboration_status === 'cross_referenced' ? 'link' : 'hourglass_empty'}
                        </span>
                        <span className="capitalize">{signal.corroboration_status.replace('_', ' ')}</span>
                      </span>
                    )}
                  </div>
                </a>
              ))
            )}
          </div>
        </div>
      </div>
    </>
  );
}
