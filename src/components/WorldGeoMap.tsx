import { useMemo } from 'react';
import { geoNaturalEarth1, geoPath } from 'd3-geo';
import { feature } from 'topojson-client';
import worldAtlas from 'world-atlas/countries-110m.json';
import worldCountries from 'world-countries';

export type WorldGeoMapMarker = {
  id: string;
  location: string;
  lat: number;
  lon: number;
  severity: 'high' | 'medium';
  headline: string;
  source: string;
  url: string;
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
};

type CountryHoverMeta = {
  name: string;
  capital: string;
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
}: WorldGeoMapProps) {
  const { countryPaths, markerPoints } = useMemo(() => {
    const countriesGeoJson = feature(
      worldAtlas as any,
      (worldAtlas as any).objects.countries
    ) as any;

    const countryMetaByNumericCode = new Map<string, CountryHoverMeta>();
    (worldCountries as Array<{ ccn3?: string; name?: { common?: string }; capital?: string[] }>).forEach((country) => {
      const code = country.ccn3?.padStart(3, '0');
      if (!code) return;
      countryMetaByNumericCode.set(code, {
        name: country.name?.common || 'Unknown country',
        capital: country.capital?.[0] || 'Capital unavailable',
      });
    });

    const projection = geoNaturalEarth1().fitSize([1200, 620], { type: 'Sphere' } as never);
    const pathGenerator = geoPath(projection);

    const countryPaths = ((countriesGeoJson.features || []) as unknown[])
      .map((item: unknown, index: number) => {
        const featureItem = item as { id?: string | number };
        const path = pathGenerator(item as never);
        if (!path) return null;
        const code = String(featureItem.id ?? '').padStart(3, '0');
        const meta = countryMetaByNumericCode.get(code) || { name: 'Unknown country', capital: 'Capital unavailable' };
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

  return (
    <svg
      viewBox="0 0 1200 620"
      className={className || 'h-full w-full'}
      role="img"
      aria-label="World map"
      preserveAspectRatio={`xMidYMid ${fitMode}`}
    >
      <rect x="0" y="0" width="1200" height="620" fill="transparent" />
      <g transform={`translate(${panX} ${panY}) translate(600 310) scale(${zoom}) translate(-600 -310)`}>
        {countryPaths.map((country: { id: string; d: string; meta: CountryHoverMeta }) => (
          <path key={country.id} d={country.d} fill="rgba(170, 198, 222, 0.35)" stroke="rgba(214, 231, 245, 0.75)" strokeWidth="0.8">
            <title>{`${country.meta.name} - Capital: ${country.meta.capital}`}</title>
          </path>
        ))}

        {showMarkers &&
          markerPoints.map(({ marker, x, y }) => {
            const color = marker.severity === 'high' ? '#ef4444' : '#facc15';
            const pulseClass = marker.severity === 'high' ? 'animate-pulse' : '';

            if (!interactive) {
              return <circle key={marker.id} cx={x} cy={y} r={5.5} fill={color} opacity={0.95} className={pulseClass} />;
            }

            return (
              <a
                key={marker.id}
                href={marker.url}
                target="_blank"
                rel="noreferrer"
                className="group"
              >
                <g transform={`translate(${x}, ${y})`}>
                  <circle r={5.5} fill={color} opacity={0.95} className={pulseClass} />
                  <circle r={8.5} fill="transparent" stroke="rgba(0,0,0,0.7)" strokeWidth="1.4" />
                </g>
                <title>{`${marker.location}: ${marker.headline}`}</title>
              </a>
            );
          })}
      </g>
    </svg>
  );
}
