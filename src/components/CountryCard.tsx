import React from 'react';
import { RiskLevelBadge, RiskLevelBar, type ThreatLevel, getRiskConfig } from './RiskLevel';

type Signal = {
  impact: string;
  category: string;
};

type CountryCardProps = {
  name: string;
  region: string;
  threatLevel: ThreatLevel;
  signalCount: number;
  highSignalCount: number;
  stabilityIndex: number;
  riskProbability: number;
  isSelected: boolean;
  onClick: () => void;
  signals?: Signal[];
};

function getFlagEmoji(countryCode: string): string {
  if (!countryCode || countryCode.length !== 2) return '';
  const codePoints = countryCode
    .toUpperCase()
    .split('')
    .map((char) => 127397 + char.charCodeAt(0));
  try {
    return String.fromCodePoint(...codePoints);
  } catch {
    return '';
  }
}

const COUNTRY_CODE_MAP: Record<string, string> = {
  China: 'CN', Pakistan: 'PK', Afghanistan: 'AF', Bangladesh: 'BD',
  Myanmar: 'MM', Nepal: 'NP', Bhutan: 'BT', 'Sri Lanka': 'LK',
  Maldives: 'MV', India: 'IN', 'United States': 'US', Russia: 'RU',
  Iran: 'IR', Israel: 'IL', Taiwan: 'TW', Japan: 'JP', Australia: 'AU',
  'United Kingdom': 'GB', Germany: 'DE', Ukraine: 'UA', 'South Korea': 'KR',
};

export function CountryCard({
  name,
  region,
  threatLevel,
  signalCount,
  highSignalCount,
  stabilityIndex,
  riskProbability,
  isSelected,
  onClick,
  signals = [],
}: CountryCardProps) {
  const config = getRiskConfig(threatLevel);
  const countryCode = COUNTRY_CODE_MAP[name] || '';
  const flag = getFlagEmoji(countryCode);

  // Category breakdown
  const categories = ['Political', 'Social', 'Tech', 'Economic', 'Military'];
  const categorySignals = categories.map((cat) => ({
    name: cat.substring(0, 4),
    count: signals.filter((s) => s.category === cat).length,
  }));
  const maxCatCount = Math.max(...categorySignals.map((c) => c.count), 1);

  return (
    <div
      className={`country-card ${isSelected ? 'country-card--selected' : ''}`}
      onClick={onClick}
      style={{
        borderColor: isSelected ? config.color : undefined,
      }}
      role="button"
      tabIndex={0}
      aria-label={`${name}: Threat level ${threatLevel}, ${signalCount} signals`}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onClick(); }}
    >
      {/* Top accent line */}
      <div
        className="country-card__accent"
        style={{ background: `linear-gradient(90deg, ${config.color}, ${config.color}44)` }}
      />

      {/* Header */}
      <div className="country-card__header">
        <div className="flex items-center gap-2 min-w-0">
          {flag && <span className="text-base leading-none">{flag}</span>}
          <h3 className="country-card__name truncate">{name}</h3>
        </div>
        <RiskLevelBadge level={threatLevel} size="sm" />
      </div>

      {/* Region */}
      <div className="country-card__region">{region}</div>

      {/* Risk Bar */}
      <RiskLevelBar level={threatLevel} height={3} className="mt-2 mb-2" />

      {/* Metrics Row */}
      <div className="country-card__metrics">
        <div>
          <span className="country-card__metric-value" style={{ color: config.color }}>
            {signalCount}
          </span>
          <span className="country-card__metric-label">SIG</span>
        </div>
        <div>
          <span className="country-card__metric-value text-[#F57C00]">
            {highSignalCount}
          </span>
          <span className="country-card__metric-label">HI</span>
        </div>
        <div>
          <span className="country-card__metric-value text-[#4edea3]">
            {(stabilityIndex * 100).toFixed(0)}%
          </span>
          <span className="country-card__metric-label">STAB</span>
        </div>
        <div>
          <span className="country-card__metric-value" style={{
            color: riskProbability > 70 ? '#D32F2F' : riskProbability > 40 ? '#F57C00' : '#388E3C'
          }}>
            {riskProbability.toFixed(0)}
          </span>
          <span className="country-card__metric-label">RISK</span>
        </div>
      </div>

      {/* Category Mini Bars */}
      <div className="country-card__cat-bars">
        {categorySignals.map((cat) => (
          <div key={cat.name} className="flex items-center gap-1">
            <span className="country-card__cat-label">{cat.name}</span>
            <div className="country-card__cat-bar">
              <div
                className="country-card__cat-fill"
                style={{
                  width: `${(cat.count / maxCatCount) * 100}%`,
                  background: config.color,
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
