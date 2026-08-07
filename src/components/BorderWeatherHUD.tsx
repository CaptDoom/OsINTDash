import { useEffect, useState } from 'react';

type WeatherInfo = {
  sector: string;
  latitude: number;
  longitude: number;
  temperature: number;
  condition: string;
  visibility_km: number;
  wind_speed_kmh: number;
  source: string;
};

export function BorderWeatherHUD() {
  const [weatherData, setWeatherData] = useState<Record<string, WeatherInfo> | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchWeather = async () => {
    try {
      const response = await fetch('/api/weather/border');
      if (!response.ok) {
        throw new Error(`Server returned status ${response.status}`);
      }
      const data = await response.json();
      setWeatherData(data);
      setError(null);
    } catch (err) {
      console.error('[WeatherHUD] Fetch error:', err);
      setError('Telemetry connection offline');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWeather();
    // Poll weather data every 5 minutes
    const interval = setInterval(fetchWeather, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  const getWeatherIcon = (cond: string) => {
    const c = cond.toLowerCase();
    if (c.includes('snow') || c.includes('blizzard')) return 'ac_unit';
    if (c.includes('fog') || c.includes('mist') || c.includes('haze')) return 'blur_on';
    if (c.includes('rain') || c.includes('shower')) return 'grain';
    if (c.includes('wind') || c.includes('storm')) return 'air';
    return 'sunny';
  };

  const getTemperatureColor = (temp: number) => {
    if (temp <= 0) return 'text-[#7bd0ff]'; // freezing cold
    if (temp > 30) return 'text-[#ffb4ab]';  // extreme heat
    return 'text-[#4edea3]';                  // temperate
  };

  if (loading && !weatherData) {
    return (
      <div className="bg-[#051424] border-b border-[#45464d]/60 h-10 px-6 flex items-center gap-2 text-[10px] font-mono text-[#7bd0ff] tracking-widest uppercase">
        <span className="w-1.5 h-1.5 rounded-full bg-[#7bd0ff] animate-ping" />
        Syncing border sector weather telemetry...
      </div>
    );
  }

  if (error && !weatherData) {
    return (
      <div className="bg-[#051424] border-b border-[#45464d]/60 h-10 px-6 flex items-center gap-2 text-[10px] font-mono text-[#ffb4ab] tracking-widest uppercase">
        <span className="material-symbols-outlined text-sm text-[#ffb4ab]">warning</span>
        Weather Telemetry degraded: {error}
      </div>
    );
  }

  const weatherList = weatherData ? Object.values(weatherData) : [];

  return (
    <div className="bg-[#010912] border-b border-[#45464d]/60 py-2.5 px-6 flex items-center justify-between overflow-x-auto shrink-0 select-none z-10 gap-4 scrollbar-none">
      <div className="flex items-center gap-2 pr-4 border-r border-[#45464d]/30 shrink-0">
        <span className="material-symbols-outlined text-[#7bd0ff] text-base animate-pulse">thermostat</span>
        <div className="font-mono">
          <p className="text-[10px] font-bold text-[#7bd0ff] uppercase tracking-wider">Meteorological HUD</p>
          <p className="text-[8px] text-[#c6c6cd] opacity-60">TACTICAL RADAR SYNC</p>
        </div>
      </div>

      <div className="flex items-center gap-3 overflow-x-auto scrollbar-none flex-grow">
        {weatherList.map((w) => {
          const isExtreme = w.temperature <= -10 || w.temperature >= 35 || w.visibility_km < 1.0;
          return (
            <div
              key={w.sector}
              className={`flex items-center gap-3 bg-[#0a1829]/80 border ${
                isExtreme ? 'border-[#ffb4ab]/40 animate-pulse-slow' : 'border-[#45464d]/40'
              } px-3 py-1.5 rounded min-w-[210px] max-w-[240px] font-mono text-[#bec6e0] transition-colors hover:border-[#7bd0ff]/40`}
            >
              <span className={`material-symbols-outlined text-xl ${getTemperatureColor(w.temperature)}`}>
                {getWeatherIcon(w.condition)}
              </span>

              <div className="flex-1 min-w-0">
                <div className="flex justify-between items-center gap-2">
                  <div className="flex items-center gap-1 min-w-0">
                    <span className="text-[10px] font-bold text-white truncate uppercase">{w.sector}</span>
                    {w.source === 'TACTICAL-SIMULATOR' && (
                      <span className="bg-amber-500/20 text-amber-400 text-[6px] px-1 rounded border border-amber-500/30 tracking-widest font-bold font-mono">SIM</span>
                    )}
                  </div>
                  <span className={`text-[10px] font-bold ${getTemperatureColor(w.temperature)}`}>
                    {w.temperature}°C
                  </span>
                </div>
                
                <div className="flex justify-between items-center text-[8px] opacity-75 mt-0.5">
                  <span className="truncate max-w-[90px]">{w.condition.toUpperCase()}</span>
                  <span>VIS: {w.visibility_km}KM</span>
                  <span>WND: {Math.round(w.wind_speed_kmh)}KM/H</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="hidden lg:flex items-center gap-2 border-l border-[#45464d]/30 pl-4 shrink-0 font-mono text-[8px] text-[#c6c6cd] opacity-60">
        <span>AUTO-REFRESH: 5M</span>
        <div className="w-1.5 h-1.5 rounded-full bg-[#4edea3]" />
      </div>
    </div>
  );
}
