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

export type CountryAlertGroup = {
  countryCode: string;
  location: string;
  lat: number;
  lon: number;
  alerts: WorldGeoMapMarker[];
  maxSeverity: 'high' | 'medium' | 'low';
  highCount: number;
  mediumCount: number;
  lowCount: number;
  x: number;
  y: number;
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

// Helper to convert country code to emoji flag
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
  const [hoveredGroup, setHoveredGroup] = useState<CountryAlertGroup | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<CountryAlertGroup | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const { countryPaths, alertGroups } = useMemo(() => {
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

    // Grouping markers by country/location
    const groupsMap = new Map<string, Omit<CountryAlertGroup, 'x' | 'y'>>();

    markers.forEach((marker) => {
      const locName = marker.location || 'Global';
      let cca2Code = (marker.countryCode || '').toUpperCase();
      if (!cca2Code) {
        const found = (worldCountries as any[]).find(c => c.name.common.toLowerCase() === locName.toLowerCase());
        if (found && found.cca2) {
          cca2Code = found.cca2.toUpperCase();
        }
      }
      
      const key = cca2Code || locName.toUpperCase();

      let group = groupsMap.get(key);
      if (!group) {
        group = {
          countryCode: cca2Code || '',
          location: locName,
          lat: marker.lat,
          lon: marker.lon,
          alerts: [],
          maxSeverity: 'low',
          highCount: 0,
          mediumCount: 0,
          lowCount: 0,
        };
        groupsMap.set(key, group);
      }
      group.alerts.push(marker);

      if (marker.severity === 'high') {
        group.highCount++;
        group.maxSeverity = 'high';
      } else if (marker.severity === 'medium') {
        group.mediumCount++;
        if (group.maxSeverity !== 'high') {
          group.maxSeverity = 'medium';
        }
      } else {
        group.lowCount++;
        if (group.maxSeverity !== 'high' && group.maxSeverity !== 'medium') {
          group.maxSeverity = 'low';
        }
      }
    });

    const alertGroups: CountryAlertGroup[] = [];
    groupsMap.forEach((group) => {
      const point = projection([group.lon, group.lat]);
      if (point) {
        alertGroups.push({
          ...group,
          x: point[0],
          y: point[1],
        });
      }
    });

    return { countryPaths, alertGroups };
  }, [markers]);

  // Keep track of the currently selected country name to keep popup open / synced
  useMemo(() => {
    if (selectedCountryName) {
      const foundGroup = alertGroups.find(
        (g) => g.location.toLowerCase() === selectedCountryName.toLowerCase()
      );
      if (foundGroup) {
        setSelectedGroup(foundGroup);
      } else {
        setSelectedGroup(null);
      }
    } else {
      setSelectedGroup(null);
    }
  }, [selectedCountryName, alertGroups]);

  return (
    <div className="relative w-full h-full">
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
                fill={isSelected ? "rgba(0, 229, 255, 0.35)" : "rgba(45, 65, 85, 0.4)"}
                stroke={isSelected ? "#00e5ff" : "rgba(75, 105, 135, 0.4)"}
                strokeWidth={isSelected ? 1.5 : 0.8}
                onClick={() => {
                  if (interactive && onCountryClick && country.meta.name !== 'Unknown country') {
                    onCountryClick(country.meta.name, country.meta.cca2);
                  }
                }}
                style={{
                  cursor: (interactive && country.meta.name !== 'Unknown country') ? 'pointer' : 'default',
                  transition: 'fill 0.25s ease, stroke 0.25s ease'
                }}
                className="hover:fill-[#00e5ff]/20"
              >
                <title>{`${country.meta.name} - Capital: ${country.meta.capital}`}</title>
              </path>
            );
          })}

          {showMarkers &&
            alertGroups.map((group) => {
              const color =
                group.maxSeverity === 'high'
                  ? '#EF4444'
                  : group.maxSeverity === 'medium'
                  ? '#F59E0B'
                  : '#10B981';

              return (
                <g
                  key={group.countryCode || group.location}
                  transform={`translate(${group.x}, ${group.y})`}
                  style={{ cursor: interactive ? 'pointer' : 'default' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedGroup(group);
                    if (interactive && onCountryClick) {
                      onCountryClick(group.location, group.countryCode || '');
                    }
                  }}
                  onMouseEnter={(e) => {
                    setHoveredGroup(group);
                    setTooltipPos({ x: e.clientX, y: e.clientY });
                  }}
                  onMouseMove={(e) => {
                    setTooltipPos({ x: e.clientX, y: e.clientY });
                  }}
                  onMouseLeave={() => {
                    setHoveredGroup(null);
                  }}
                >
                  {/* Glowing Radar Beacons */}
                  <circle r={24} fill="none" stroke={color} strokeWidth="1" opacity={0.15} />
                  <circle r={18} fill="none" stroke={color} strokeWidth="1.5" className="animate-ping" opacity={0.4} style={{ animationDuration: '3s' }} />

                  {/* Tactically Styled pill-shape Badge */}
                  <rect
                    x={-22}
                    y={-10}
                    width={44}
                    height={20}
                    rx={4}
                    ry={4}
                    fill="#051424"
                    stroke={color}
                    strokeWidth={1.8}
                    filter="drop-shadow(0px 2px 6px rgba(0,0,0,0.6))"
                    className="transition-all duration-200 hover:scale-105"
                  />

                  {/* Country Code Label */}
                  <text
                    x={-10}
                    y={4}
                    textAnchor="middle"
                    fill="#7bd0ff"
                    fontSize="9.5"
                    fontWeight="bold"
                    fontFamily="monospace"
                    letterSpacing="0.02em"
                  >
                    {group.countryCode || group.location.substring(0, 2).toUpperCase()}
                  </text>

                  {/* Badge Vertical Separator */}
                  <line x1={0} y1={-6} x2={0} y2={6} stroke="rgba(123, 208, 255, 0.25)" strokeWidth="1" />

                  {/* Total Alerts Count */}
                  <text
                    x={10}
                    y={4}
                    textAnchor="middle"
                    fill={color}
                    fontSize="9.5"
                    fontWeight="black"
                    fontFamily="monospace"
                  >
                    {group.alerts.length}
                  </text>
                </g>
              );
            })}
        </g>
      </svg>

      {/* Floating Tactical Tooltip on Hover */}
      {hoveredGroup && (
        <div
          style={{
            position: 'fixed',
            left: tooltipPos.x + 15,
            top: tooltipPos.y + 15,
            zIndex: 9999,
            pointerEvents: 'none',
            fontFamily: 'monospace',
          }}
          className="bg-[#051424]/95 border border-[#7bd0ff]/40 p-3 rounded shadow-2xl text-xs max-w-xs text-[#d4e4fa] backdrop-blur-sm"
        >
          <div className="font-bold text-[#7bd0ff] uppercase tracking-widest mb-1 border-b border-[#7bd0ff]/20 pb-1 flex justify-between items-center gap-4">
            <span className="flex items-center gap-1.5">
              <span>{getFlagEmoji(hoveredGroup.countryCode)}</span>
              <span>{hoveredGroup.location}</span>
            </span>
            <span className="bg-[#7bd0ff]/20 text-[#7bd0ff] px-1.5 py-0.5 rounded text-[10px] font-bold">
              {hoveredGroup.alerts.length} {hoveredGroup.alerts.length > 1 ? 'ALERTS' : 'ALERT'}
            </span>
          </div>
          <div className="opacity-90 leading-relaxed font-semibold mb-2">
            {hoveredGroup.alerts[0]?.headline}
          </div>
          <div className="text-[10px] text-white/50 flex justify-between items-center pt-1.5 border-t border-white/10">
            <span>MAX SEVERITY: {hoveredGroup.maxSeverity.toUpperCase()}</span>
            <span>{hoveredGroup.highCount} High, {hoveredGroup.mediumCount} Med</span>
          </div>
        </div>
      )}

      {/* Slide-out Interactive Intel Transmissions Sidebar */}
      {selectedGroup && (
        <div className="absolute top-4 right-4 bottom-4 w-80 bg-[#051424]/95 border border-[#7bd0ff]/40 p-4 rounded z-30 flex flex-col backdrop-blur-md text-[#d4e4fa] font-mono shadow-2xl">
          <div className="flex items-center justify-between border-b border-[#7bd0ff]/30 pb-2 mb-3">
            <h3 className="font-bold text-sm tracking-wider text-[#7bd0ff] uppercase flex items-center gap-1.5">
              <span>{getFlagEmoji(selectedGroup.countryCode)}</span>
              <span>{selectedGroup.location} INTEL WIRE</span>
            </h3>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setSelectedGroup(null);
              }}
              className="text-[#7bd0ff] hover:text-white hover:bg-white/10 px-1.5 py-0.5 rounded text-xs transition border border-[#7bd0ff]/20"
            >
              [CLOSE]
            </button>
          </div>

          <div className="flex gap-1.5 text-[9px] uppercase tracking-wider mb-3">
            {selectedGroup.highCount > 0 && (
              <span className="bg-red-500/20 text-red-400 px-1.5 py-0.5 rounded border border-red-500/30 font-bold">
                HIGH: {selectedGroup.highCount}
              </span>
            )}
            {selectedGroup.mediumCount > 0 && (
              <span className="bg-yellow-500/20 text-yellow-400 px-1.5 py-0.5 rounded border border-yellow-500/30 font-bold">
                MED: {selectedGroup.mediumCount}
              </span>
            )}
            {selectedGroup.lowCount > 0 && (
              <span className="bg-green-500/20 text-green-400 px-1.5 py-0.5 rounded border border-green-500/30 font-bold">
                LOW: {selectedGroup.lowCount}
              </span>
            )}
          </div>

          <div className="flex-1 overflow-y-auto space-y-3.5 pr-1" style={{ scrollbarWidth: 'thin', scrollbarColor: '#7bd0ff/30 transparent' }}>
            {selectedGroup.alerts.map((alert, idx) => {
              const severityColor =
                alert.severity === 'high'
                  ? 'text-red-400 border-red-500/30 bg-red-500/10'
                  : alert.severity === 'medium'
                  ? 'text-yellow-400 border-yellow-500/30 bg-yellow-500/10'
                  : 'text-green-400 border-green-500/30 bg-green-500/10';

              return (
                <div key={alert.id || idx} className="border-b border-white/5 pb-3 last:border-0">
                  <div className="flex items-center justify-between gap-2 mb-1.5">
                    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${severityColor} uppercase shrink-0`}>
                      {alert.severity}
                    </span>
                    <span className="text-[9px] text-white/40 font-mono">
                      {alert.timestamp ? new Date(alert.timestamp).toLocaleTimeString() : ''}
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed font-semibold text-white/90 mb-2">
                    {alert.headline}
                  </p>
                  <div className="flex items-center justify-between text-[10px] text-white/50 pt-1">
                    <span>VIA: {alert.source}</span>
                    <a
                      href={alert.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[#00e5ff] hover:text-[#7bd0ff] hover:underline flex items-center gap-0.5 font-bold transition-colors"
                    >
                      SOURCE LINK ↗
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
