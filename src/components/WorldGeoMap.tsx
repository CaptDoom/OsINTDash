import { useMemo, useState } from 'react';
import { geoMercator, geoPath } from 'd3-geo';
import { feature } from 'topojson-client';
import worldAtlas from 'world-atlas/countries-110m.json';
import worldCountries from 'world-countries';

export type WorldGeoMapMarker = {
  id: string;
  location: string;
  lat: number;
  lon: number;
  severity: 'high' | 'medium' | 'low';
  headline: string;
  source: string;
  url: string;
  summary?: string;
  countryCode?: string;
  timestamp?: string;
};

type WorldGeoMapProps = {
  markers: WorldGeoMapMarker[];
  interactive?: boolean;
  showMarkers?: boolean;
  className?: string;
  zoom?: number;
  panX?: number;
  panY?: number;
  fitMode?: 'meet' | 'slice';
  selectedCountryName?: string;
  onCountryClick?: (name: string, code: string) => void;
};

type CountryHoverMeta = {
  name: string;
  capital: string;
  cca2: string;
};

export function WorldGeoMap({
  markers,
  interactive = true,
  showMarkers = true,
  className,
  zoom = 1,
  panX = 0,
  panY = 0,
  fitMode = 'meet',
  selectedCountryName,
  onCountryClick,
}: WorldGeoMapProps) {
  const [hoveredMarker, setHoveredMarker] = useState<WorldGeoMapMarker | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const { countryPaths, markerPoints } = useMemo(() => {
    const countriesGeoJson = feature(
      worldAtlas as any,
      (worldAtlas as any).objects.countries
    ) as any;

    const countryMetaByNumericCode = new Map<string, CountryHoverMeta>();
    (worldCountries as Array<{ ccn3?: string; cca2?: string; name?: { common?: string }; capital?: string[] }>).forEach((country) => {
      const code = country.ccn3?.padStart(3, '0');
      if (!code) return;
      countryMetaByNumericCode.set(code, {
        name: country.name?.common || 'Unknown country',
        capital: country.capital?.[0] || 'Capital unavailable',
        cca2: country.cca2 || '',
      });
    });

    // Reconfigured to geoMercator full world boundaries fit to viewport
    const projection = geoMercator().fitSize([1200, 620], { type: 'Sphere' } as never);
    const pathGenerator = geoPath(projection);

    const countryPaths = ((countriesGeoJson.features || []) as unknown[])
      .map((item: unknown, index: number) => {
        const featureItem = item as { id?: string | number };
        const path = pathGenerator(item as never);
        if (!path) return null;
        const code = String(featureItem.id ?? '').padStart(3, '0');
        const meta = countryMetaByNumericCode.get(code) || { name: 'Unknown country', capital: 'Capital unavailable', cca2: '' };
        return { id: `country-${index}`, d: path, meta };
      })
      .filter((item: { id: string; d: string; meta: CountryHoverMeta } | null): item is { id: string; d: string; meta: CountryHoverMeta } => Boolean(item));

    const locationOffsets = new Map<string, number>();

    const markerPoints = markers
      .map((marker) => {
        const point = projection([marker.lon, marker.lat]);
        if (!point) return null;
        const occurrence = locationOffsets.get(marker.location) || 0;
        locationOffsets.set(marker.location, occurrence + 1);
        const offsetX = occurrence === 0 ? 0 : occurrence % 2 === 1 ? 12 : -12;
        const offsetY = occurrence === 0 ? 0 : occurrence > 1 ? 8 : -8;
        return { marker, x: point[0] + offsetX, y: point[1] + offsetY };
      })
      .filter((item): item is { marker: WorldGeoMapMarker; x: number; y: number } => Boolean(item));

    return { countryPaths, markerPoints };
  }, [markers]);

  // Compute stats for tooltip
  const tooltipStats = useMemo(() => {
    if (!hoveredMarker) return { count: 0, latestHeadline: '' };
    const countryAlerts = markers.filter(
      (m) => m.location.toLowerCase() === hoveredMarker.location.toLowerCase()
    );
    return {
      count: countryAlerts.length,
      latestHeadline: countryAlerts[0]?.headline || hoveredMarker.headline,
    };
  }, [hoveredMarker, markers]);

  return (
    <>
      <svg
        viewBox="0 0 1200 620"
        className={className || 'h-full w-full'}
        role="img"
        aria-label="World map"
        preserveAspectRatio={`xMidYMid ${fitMode}`}
      >
        <rect x="0" y="0" width="1200" height="620" fill="transparent" />
        <g transform={`translate(${panX} ${panY}) translate(600 310) scale(${zoom}) translate(-600 -310)`}>
          {countryPaths.map((country: { id: string; d: string; meta: CountryHoverMeta }) => {
            const isSelected = selectedCountryName && country.meta.name.toLowerCase() === selectedCountryName.toLowerCase();
            return (
              <path
                key={country.id}
                d={country.d}
                fill={isSelected ? "rgba(0, 229, 255, 0.45)" : "rgba(170, 198, 222, 0.25)"}
                stroke={isSelected ? "#00e5ff" : "rgba(214, 231, 245, 0.75)"}
                strokeWidth={isSelected ? 1.5 : 0.8}
                onClick={() => {
                  if (interactive && onCountryClick && country.meta.name !== 'Unknown country') {
                    onCountryClick(country.meta.name, country.meta.cca2);
                  }
                }}
                style={{
                  cursor: (interactive && country.meta.name !== 'Unknown country') ? 'pointer' : 'default',
                  transition: 'fill 0.2s, stroke 0.2s'
                }}
                className="hover:fill-[#00e5ff]/20"
              >
                <title>{`${country.meta.name} - Capital: ${country.meta.capital}`}</title>
              </path>
            );
          })}

          {showMarkers &&
            markerPoints.map(({ marker, x, y }) => {
              // High: Red (#EF4444), Medium: Blue (#3B82F6), Low: Green (#22C55E)
              const color =
                marker.severity === 'high'
                  ? '#EF4444'
                  : marker.severity === 'medium'
                  ? '#3B82F6'
                  : '#22C55E';
              const pulseClass = marker.severity === 'high' ? 'animate-pulse' : '';

              return (
                <g
                  key={marker.id}
                  transform={`translate(${x}, ${y})`}
                  style={{ cursor: interactive ? 'pointer' : 'default' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (interactive && onCountryClick) {
                      onCountryClick(marker.location, marker.countryCode || '');
                    }
                  }}
                  onMouseEnter={(e) => {
                    setHoveredMarker(marker);
                    setTooltipPos({ x: e.clientX, y: e.clientY });
                  }}
                  onMouseMove={(e) => {
                    setTooltipPos({ x: e.clientX, y: e.clientY });
                  }}
                  onMouseLeave={() => {
                    setHoveredMarker(null);
                  }}
                >
                  <circle r={6.5} fill={color} opacity={0.95} className={pulseClass} />
                  <circle r={9.5} fill="transparent" stroke="rgba(0,0,0,0.7)" strokeWidth="1.4" />
                </g>
              );
            })}
        </g>
      </svg>

      {/* Floating Tactical Tooltip */}
      {hoveredMarker && (
        <div
          style={{
            position: 'fixed',
            left: tooltipPos.x + 15,
            top: tooltipPos.y + 15,
            zIndex: 9999,
            pointerEvents: 'none',
            fontFamily: 'monospace',
          }}
          className="bg-[#051424] border border-[#7bd0ff]/40 p-3 rounded shadow-2xl text-xs max-w-sm text-[#d4e4fa] backdrop-blur-sm"
        >
          <div className="font-bold text-[#7bd0ff] uppercase tracking-widest mb-1 border-b border-[#7bd0ff]/20 pb-1 flex justify-between items-center gap-4">
            <span>{hoveredMarker.location}</span>
            <span className="bg-[#7bd0ff]/20 text-[#7bd0ff] px-1.5 py-0.5 rounded text-[10px] font-bold">
              {tooltipStats.count} {tooltipStats.count > 1 ? 'ALERTS' : 'ALERT'}
            </span>
          </div>
          <div className="opacity-90 leading-relaxed font-semibold">
            {tooltipStats.latestHeadline}
          </div>
          <div className="text-[10px] text-white/50 flex justify-between items-center mt-2 pt-1.5 border-t border-white/10">
            <span>SOURCE: {hoveredMarker.source}</span>
            <span className="capitalize">{hoveredMarker.severity} severity</span>
          </div>
        </div>
      )}
    </>
  );
}
