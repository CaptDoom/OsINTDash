
type LivePulseIndicatorProps = {
  isLive: boolean;
  label?: string;
  className?: string;
};

export function LivePulseIndicator({
  isLive,
  label = 'LIVE',
  className = '',
}: LivePulseIndicatorProps) {
  return (
    <div className={`live-pulse ${className}`}>
      <span className={`live-pulse__dot ${isLive ? 'live-pulse__dot--live' : 'live-pulse__dot--offline'}`} />
      <span className="live-pulse__label">{label}</span>
    </div>
  );
}
