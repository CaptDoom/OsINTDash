import React from 'react';

export type ThreatLevel = 'Critical' | 'High' | 'Moderate' | 'Low';

const RISK_CONFIG: Record<ThreatLevel, {
  color: string;
  bg: string;
  border: string;
  icon: string;
  glow: string;
  label: string;
}> = {
  Critical: {
    color: '#D32F2F',
    bg: 'rgba(211, 47, 47, 0.12)',
    border: 'rgba(211, 47, 47, 0.45)',
    icon: 'error',
    glow: '0 0 12px rgba(211, 47, 47, 0.3)',
    label: 'CRITICAL',
  },
  High: {
    color: '#F57C00',
    bg: 'rgba(245, 124, 0, 0.10)',
    border: 'rgba(245, 124, 0, 0.40)',
    icon: 'warning',
    glow: '0 0 10px rgba(245, 124, 0, 0.25)',
    label: 'HIGH',
  },
  Moderate: {
    color: '#FBC02D',
    bg: 'rgba(251, 192, 45, 0.08)',
    border: 'rgba(251, 192, 45, 0.35)',
    icon: 'info',
    glow: '0 0 8px rgba(251, 192, 45, 0.2)',
    label: 'MODERATE',
  },
  Low: {
    color: '#388E3C',
    bg: 'rgba(56, 142, 60, 0.10)',
    border: 'rgba(56, 142, 60, 0.35)',
    icon: 'check_circle',
    glow: '0 0 6px rgba(56, 142, 60, 0.2)',
    label: 'LOW',
  },
};

export function getRiskConfig(level: ThreatLevel) {
  return RISK_CONFIG[level] || RISK_CONFIG.Low;
}

type RiskLevelBadgeProps = {
  level: ThreatLevel;
  size?: 'sm' | 'md' | 'lg';
  showIcon?: boolean;
  showLabel?: boolean;
  pulse?: boolean;
  className?: string;
};

export function RiskLevelBadge({
  level,
  size = 'md',
  showIcon = true,
  showLabel = true,
  pulse = false,
  className = '',
}: RiskLevelBadgeProps) {
  const config = RISK_CONFIG[level] || RISK_CONFIG.Low;
  const sizeClasses = {
    sm: 'text-[9px] px-1.5 py-0.5 gap-1',
    md: 'text-[11px] px-2.5 py-1 gap-1.5',
    lg: 'text-xs px-3 py-1.5 gap-2',
  };
  const iconSizes = { sm: '10px', md: '13px', lg: '16px' };

  return (
    <span
      className={`inline-flex items-center font-mono font-bold uppercase tracking-widest rounded-full ${sizeClasses[size]} ${className}`}
      style={{
        color: config.color,
        background: config.bg,
        border: `1px solid ${config.border}`,
        boxShadow: level === 'Critical' || level === 'High' ? config.glow : 'none',
      }}
      role="status"
      aria-label={`Threat level: ${level}`}
    >
      {showIcon && (
        <span
          className={`material-symbols-outlined ${pulse ? 'animate-pulse' : ''}`}
          style={{ fontSize: iconSizes[size] }}
        >
          {config.icon}
        </span>
      )}
      {showLabel && <span>{config.label}</span>}
    </span>
  );
}

type RiskLevelBarProps = {
  level: ThreatLevel;
  height?: number;
  className?: string;
};

export function RiskLevelBar({ level, height = 4, className = '' }: RiskLevelBarProps) {
  const config = RISK_CONFIG[level] || RISK_CONFIG.Low;
  return (
    <div
      className={`rounded-full overflow-hidden ${className}`}
      style={{
        height,
        background: 'rgba(255,255,255,0.06)',
      }}
    >
      <div
        className="h-full rounded-full"
        style={{
          width: level === 'Critical' ? '100%' : level === 'High' ? '75%' : level === 'Moderate' ? '50%' : '25%',
          background: `linear-gradient(90deg, ${config.color}, ${config.color}aa)`,
          boxShadow: `0 0 6px ${config.color}44`,
        }}
      />
    </div>
  );
}

export function RiskLegend({ className = '' }: { className?: string }) {
  return (
    <div className={`flex items-center gap-4 flex-wrap ${className}`}>
      {(Object.keys(RISK_CONFIG) as ThreatLevel[]).map((level) => (
        <RiskLevelBadge key={level} level={level} size="sm" />
      ))}
    </div>
  );
}
