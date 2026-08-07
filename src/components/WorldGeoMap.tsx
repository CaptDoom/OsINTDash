import { useMemo, useState } from 'react';
import { geoMercator, geoPath, geoGraticule } from 'd3-geo';
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
  continent: string;
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
  selectedContinent?: string;
  onCountryClick?: (name: string, code: string) => void;
  weatherData?: Record<string, any> | null;
  showWeatherOverlay?: boolean;
};

type CountryHoverMeta = {
  name: string;
  capital: string;
  cca2: string;
  region: string;
  subregion: string;
  continent: string;
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

// Maps region/subregion from world-countries npm package to standard continents
export function getContinentName(region: string, subregion: string): string {
  const reg = region || '';
  const sub = subregion || '';
  if (reg === 'Asia') return 'Asia';
  if (reg === 'Europe') return 'Europe';
  if (reg === 'Africa') return 'Africa';
  if (reg === 'Oceania') return 'Oceania';
  if (reg === 'Americas') {
    if (sub === 'South America') return 'South America';
    return 'North America'; // Northern America, Central America, Caribbean
  }
  return 'Other';
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
  selectedContinent = 'All',
  onCountryClick,
  weatherData,
  showWeatherOverlay = false,
}: WorldGeoMapProps) {
  const [hoveredGroup, setHoveredGroup] = useState<CountryAlertGroup | null>(null);
  const [hoveredWeather, setHoveredWeather] = useState<any | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const { countryPaths, alertGroups } = useMemo(() => {
    const countriesGeoJson = feature(
      worldAtlas as any,
      (worldAtlas as any).objects.countries
    ) as any;

    const countryMetaByNumericCode = new Map<string, CountryHoverMeta>();
    (worldCountries as Array<{ ccn3?: string; cca2?: string; name?: { common?: string }; capital?: string[]; region?: string; subregion?: string }>).forEach((country) => {
      const code = country.ccn3?.padStart(3, '0');
      if (!code) return;
      
      const region = country.region || 'Global';
      const subregion = country.subregion || '';
      const continent = getContinentName(region, subregion);

      countryMetaByNumericCode.set(code, {
        name: country.name?.common || 'Unknown country',
        capital: country.capital?.[0] || 'Capital unavailable',
        cca2: country.cca2 || '',
        region,
        subregion,
        continent,
      });
    });

    const projection = geoMercator().fitSize([1200, 620], { type: 'Sphere' } as never);
    const pathGenerator = geoPath(projection);

    const countryPaths = ((countriesGeoJson.features || []) as unknown[])
      .map((item: unknown, index: number) => {
        const featureItem = item as { id?: string | number };
        const path = pathGenerator(item as never);
        if (!path) return null;
        const code = String(featureItem.id ?? '').padStart(3, '0');
        const meta = countryMetaByNumericCode.get(code) || { 
          name: 'Unknown country', 
          capital: 'Capital unavailable', 
          cca2: '', 
          region: '', 
          subregion: '', 
          continent: 'Other' 
        };
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
        // Resolve continent for group
        const countryObj = (worldCountries as any[]).find(c => c.cca2 === cca2Code);
        const groupContinent = countryObj ? getContinentName(countryObj.region, countryObj.subregion) : 'Other';

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
          continent: groupContinent
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
      // Apply continent filter
      if (selectedContinent !== 'All' && group.continent !== selectedContinent) {
        return;
      }

      const point = projection([group.lon, group.lat]);
      if (point) {
        alertGroups.push({
          ...group,
          x: point[0],
          y: point[1],
        } as CountryAlertGroup);
      }
    });

    // Sort groups by severity and volume to prioritize most critical regions
    const sortedGroups = [...alertGroups].sort((a, b) => {
      const severityOrder = { high: 3, medium: 2, low: 1 };
      if (severityOrder[a.maxSeverity] !== severityOrder[b.maxSeverity]) {
        return severityOrder[b.maxSeverity] - severityOrder[a.maxSeverity];
      }
      return b.alerts.length - a.alerts.length;
    });

    // Geographical Coverage: Display all alert groups (100%) to preserve full data coverage
    return { countryPaths, alertGroups: sortedGroups };
  }, [markers, selectedContinent]);

  // Projected coordinate graticule layer using d3-geo
  const graticulePath = useMemo(() => {
    const projection = geoMercator().fitSize([1200, 620], { type: 'Sphere' } as never);
    const pathGenerator = geoPath(projection);
    try {
      const graticule = geoGraticule();
      return pathGenerator(graticule() as any);
    } catch (e) {
      console.error("Graticule generation failed", e);
      return null;
    }
  }, []);

  // Projected weather coordinates for the 15 tactical stations
  const projectedWeatherStations = useMemo(() => {
    if (!weatherData) return [];
    const projection = geoMercator().fitSize([1200, 620], { type: 'Sphere' } as never);
    return Object.values(weatherData)
      .map((w: any) => {
        const point = projection([w.longitude, w.latitude]);
        if (!point) return null;
        return {
          ...w,
          x: point[0],
          y: point[1]
        };
      })
      .filter((item): item is any => Boolean(item));
  }, [weatherData]);

  // Weather icon selector
  const getWeatherIconSvg = (cond: string) => {
    const c = (cond || '').toLowerCase();
    if (c.includes('snow') || c.includes('blizzard')) return 'ac_unit';
    if (c.includes('fog') || c.includes('mist') || c.includes('haze') || c.includes('overcast')) return 'blur_on';
    if (c.includes('rain') || c.includes('shower')) return 'grain';
    if (c.includes('wind') || c.includes('storm')) return 'air';
    return 'sunny';
  };

  return (
    <div className="relative w-full h-full">
      <svg
        viewBox="0 0 1200 620"
        className={className || 'h-full w-full'}
        role="img"
        aria-label="World map"
        preserveAspectRatio={`xMidYMid ${fitMode}`}
      >
        <defs>
          <filter id="map-glow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          <filter id="weather-glow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="1.5" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>
        <rect x="0" y="0" width="1200" height="620" fill="transparent" />
        <g transform={`translate(${panX} ${panY}) translate(600 310) scale(${zoom}) translate(-600 -310)`}>
          
          {/* Graticule Grid Layer */}
          {graticulePath && (
            <path
              d={graticulePath}
              fill="none"
              stroke="rgba(0, 229, 255, 0.05)"
              strokeWidth={0.5 / Math.sqrt(zoom)}
              pointerEvents="none"
            />
          )}
          
          {/* Countries Paths */}
          {countryPaths.map((country: { id: string; d: string; meta: CountryHoverMeta }) => {
            const isSelected = selectedCountryName && country.meta.name.toLowerCase() === selectedCountryName.toLowerCase();
            const isInFilteredContinent = selectedContinent === 'All' || country.meta.continent === selectedContinent;
            
            // Dim countries that do not match the active continent filter
            const pathFill = !isInFilteredContinent
              ? "rgba(15, 23, 30, 0.15)"
              : isSelected 
              ? "rgba(0, 229, 255, 0.35)" 
              : "rgba(45, 65, 85, 0.4)";
              
            const pathStroke = !isInFilteredContinent
              ? "rgba(35, 45, 55, 0.1)"
              : isSelected 
              ? "#00e5ff" 
              : "rgba(75, 105, 135, 0.4)";

            // Scale strokeWidth reactively with zoom to keep borders sharp and clean
            const calculatedStrokeWidth = isSelected 
              ? 1.5 / Math.sqrt(zoom) 
              : 0.8 / Math.sqrt(zoom);

            return (
              <path
                key={country.id}
                d={country.d}
                fill={pathFill}
                stroke={pathStroke}
                strokeWidth={calculatedStrokeWidth}
                onClick={() => {
                  if (!isInFilteredContinent) return;
                  if (interactive && country.meta.name !== 'Unknown country') {
                    // Clicking country redirects to the real-time link of the latest news item
                    const matchingGroup = alertGroups.find(
                      (g) => g.location.toLowerCase() === country.meta.name.toLowerCase() || g.countryCode.toUpperCase() === country.meta.cca2.toUpperCase()
                    );
                    if (matchingGroup && matchingGroup.alerts && matchingGroup.alerts.length > 0) {
                      const latestAlert = matchingGroup.alerts[0];
                      if (latestAlert && latestAlert.url) {
                        window.open(latestAlert.url, '_blank', 'noopener,noreferrer');
                      }
                    }
                    if (onCountryClick) {
                      onCountryClick(country.meta.name, country.meta.cca2);
                    }
                  }
                }}
                style={{
                  cursor: (interactive && isInFilteredContinent && country.meta.name !== 'Unknown country') ? 'pointer' : 'default',
                  transition: 'fill 0.25s ease, stroke 0.25s ease'
                }}
                className={isInFilteredContinent ? "hover:fill-[#00e5ff]/20" : ""}
              >
                <title>{`${country.meta.name} - Capital: ${country.meta.capital} [${country.meta.continent}]`}</title>
              </path>
            );
          })}

          {/* Alert news nodes (Markers) */}
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
                  transform={`translate(${group.x}, ${group.y}) scale(${1.0 / Math.pow(zoom, 0.75)})`}
                  style={{ cursor: interactive ? 'pointer' : 'default' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    // Redirect directly to the respective external link of the latest news item upon click
                    if (group.alerts && group.alerts.length > 0) {
                      const latestAlert = group.alerts[0];
                      if (latestAlert && latestAlert.url) {
                        window.open(latestAlert.url, '_blank', 'noopener,noreferrer');
                      }
                    }
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
                  <circle r={14} fill={color} opacity={0.12} />
                  <circle r={8} fill="none" stroke={color} strokeWidth="1" className="animate-ping" opacity={0.35} style={{ animationDuration: '3s' }} />

                  {/* Sleek tactical glowing circular dot (Decluttered) */}
                  <circle
                    r={5}
                    fill={color}
                    stroke="#ffffff"
                    strokeWidth={0.8}
                    style={{
                      filter: `drop-shadow(0 0 3px ${color})`
                    }}
                    className="transition-all duration-200 hover:scale-125"
                  />
                </g>
              );
            })}

          {/* Weather station beacons overlay */}
          {showWeatherOverlay && projectedWeatherStations.map((w: any) => {
            const isExtreme = w.temperature <= -10 || w.temperature >= 35 || w.visibility_km < 1.0;
            const markerColor = isExtreme ? '#FF5252' : '#00E5FF';
            
            return (
              <g
                key={w.sector}
                transform={`translate(${w.x}, ${w.y}) scale(${1.0 / Math.pow(zoom, 0.75)})`}
                style={{ cursor: 'pointer' }}
                onMouseEnter={(e) => {
                  setHoveredWeather(w);
                  setTooltipPos({ x: e.clientX, y: e.clientY });
                }}
                onMouseMove={(e) => {
                  setTooltipPos({ x: e.clientX, y: e.clientY });
                }}
                onMouseLeave={() => {
                  setHoveredWeather(null);
                }}
              >
                <circle r={9} fill={markerColor} opacity={0.08} />
                <circle r={6} fill="none" stroke={markerColor} strokeWidth="0.8" className="animate-ping" opacity={0.25} style={{ animationDuration: '4s' }} />
                <circle r={4.5} fill="#010912" stroke={markerColor} strokeWidth="1" />
                <text
                  style={{
                    fontFamily: 'Material Symbols Outlined',
                    fontSize: '8px',
                    fill: markerColor,
                    textAnchor: 'middle',
                    dominantBaseline: 'central',
                    fontWeight: 'bold',
                    pointerEvents: 'none'
                  }}
                  y={0.5}
                >
                  {getWeatherIconSvg(w.condition)}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      {/* Floating Tactical Tooltip on Hover showing news headlines and links */}
      {hoveredGroup && (
        <div
          style={{
            position: 'fixed',
            left: tooltipPos.x + 15,
            top: tooltipPos.y + 15,
            zIndex: 9999,
            pointerEvents: 'auto', // Enable clicking links within the tooltip
            fontFamily: 'monospace',
          }}
          className="bg-[#051424]/95 border border-[#7bd0ff]/40 p-3 rounded shadow-2xl text-xs w-80 text-[#d4e4fa] backdrop-blur-sm select-text"
        >
          <div className="font-bold text-[#7bd0ff] uppercase tracking-widest mb-2 border-b border-[#7bd0ff]/20 pb-1 flex justify-between items-center gap-4">
            <span className="flex items-center gap-1.5">
              <span>{getFlagEmoji(hoveredGroup.countryCode)}</span>
              <span>{hoveredGroup.location}</span>
            </span>
            <span className="bg-[#7bd0ff]/20 text-[#7bd0ff] px-1.5 py-0.5 rounded text-[10px] font-bold">
              {hoveredGroup.alerts.length} {hoveredGroup.alerts.length > 1 ? 'ALERTS' : 'ALERT'}
            </span>
          </div>
          
          <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
            {hoveredGroup.alerts.map((alert, idx) => (
              <div key={alert.id || idx} className="border-b border-white/5 pb-2 last:border-0 last:pb-0">
                <a
                  href={alert.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:underline font-semibold block text-white/90 hover:text-[#00e5ff] text-[11px] leading-tight transition-colors"
                >
                  • {alert.headline} ↗
                </a>
                <div className="text-[9px] text-white/40 mt-1 flex justify-between uppercase">
                  <span>VIA: {alert.source}</span>
                  <span>{alert.severity}</span>
                </div>
              </div>
            ))}
          </div>
          <div className="text-[9px] text-[#7bd0ff]/60 border-t border-white/10 mt-2 pt-1 flex justify-between uppercase">
            <span>REGION: {hoveredGroup.continent}</span>
            <span>CLICK TO GO TO LATEST</span>
          </div>
        </div>
      )}

      {/* Floating Tactical Weather Tooltip */}
      {hoveredWeather && (
        <div
          style={{
            position: 'fixed',
            left: tooltipPos.x + 15,
            top: tooltipPos.y + 15,
            zIndex: 9999,
            pointerEvents: 'none',
            fontFamily: 'monospace',
          }}
          className="bg-[#010912]/95 border border-[#00e5ff]/50 p-3 rounded shadow-2xl text-xs w-64 text-[#d4e4fa] backdrop-blur-sm animate-pulse-slow"
        >
          <div className="font-bold text-[#00e5ff] uppercase tracking-widest mb-1.5 border-b border-[#00e5ff]/20 pb-1 flex justify-between items-center">
            <span className="flex items-center gap-1">
              <span className="material-symbols-outlined text-xs animate-pulse">sensors</span>
              <span>{hoveredWeather.sector}</span>
            </span>
            {hoveredWeather.source === 'TACTICAL-SIMULATOR' && (
              <span className="bg-amber-500/20 text-amber-400 text-[7px] px-1 rounded border border-amber-500/30 font-bold">SIM</span>
            )}
          </div>
          
          <div className="space-y-1 text-[10px]">
            <div className="flex justify-between">
              <span className="opacity-60">COORDINATES:</span>
              <span>{hoveredWeather.latitude.toFixed(4)}°N, {hoveredWeather.longitude.toFixed(4)}°E</span>
            </div>
            <div className="flex justify-between">
              <span className="opacity-60">TEMPERATURE:</span>
              <span className="font-bold text-white">{hoveredWeather.temperature}°C</span>
            </div>
            <div className="flex justify-between">
              <span className="opacity-60">CONDITION:</span>
              <span className="uppercase text-[#00e5ff]">{hoveredWeather.condition}</span>
            </div>
            <div className="flex justify-between">
              <span className="opacity-60">VISIBILITY:</span>
              <span>{hoveredWeather.visibility_km} KM</span>
            </div>
            <div className="flex justify-between">
              <span className="opacity-60">WIND VELOCITY:</span>
              <span>{hoveredWeather.wind_speed_kmh} KM/H</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
