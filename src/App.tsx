import { useEffect, useMemo, useRef, useState } from 'react';

type TimeWindow = '1m' | '5m' | '1h' | '24h' | '7d';
type Category = 'Political' | 'Social' | 'Tech' | 'Economic' | 'Military';
type LayoutMode = 'single' | 'triple' | 'split';
type TrustIndicator = 'Verified Source' | 'Developing' | 'Unverified' | 'Rumor';

type AuthUser = {
  id: string;
  name: string;
  role: string;
  clearance: string;
};

type Country = {
  id: string;
  name: string;
  capital: string;
  borderKm: number;
  region: string;
  coordinates: string;
  summary: string;
  threatLevel: 'Low' | 'Moderate' | 'High' | 'Critical';
  stabilityIndex: number;
  riskProbability: number;
  categories: Record<Category, { title: string; summary: string; impact: string; signal: string }>;
};

type Signal = {
  id?: string;
  country: string;
  category: Category;
  impact: 'High' | 'Medium' | 'Low';
  headline: string;
  summary: string;
  source: string;
  timestamp: string;
  relevance_score?: number;
  trust?: TrustIndicator;
  isNew?: boolean;
  is_breaking?: boolean;
};

type CountryIntel = {
  region: string;
  threat_level: 'Critical' | 'High' | 'Moderate' | 'Low';
  last_synced: string;
  operational_summary: string;
  signals: Signal[];
  source_status: 'normal' | 'degraded_mesh';
};

const countries: Country[] = [
  {
    id: 'china',
    name: 'China',
    capital: 'Beijing',
    borderKm: 3488,
    region: 'Northern Front',
    coordinates: '32.9000° N, 89.4000° E',
    summary: 'PLA deployments and joint logistics upgrades continue to expand. High-altitude operations are verified.',
    threatLevel: 'Critical',
    stabilityIndex: 0.54,
    riskProbability: 78.50,
    categories: {
      Political: { title: 'LAC signaling', summary: 'Beijing continues calibrated pressure along the LAC while reinforcing regional command structures.', impact: 'High', signal: 'Joint exercises and force posture shifts' },
      Social: { title: 'Frontier communities', summary: 'Securitized border-zone governance and local settlement programs are expanding.', impact: 'Medium', signal: 'State media emphasis' },
      Tech: { title: 'Logistics systems', summary: 'High-speed communication corridors and radar monitoring networks are fully operational.', impact: 'High', signal: 'Dual-use surveillance systems' },
      Economic: { title: 'Trade routes', summary: 'Strategic highway construction continues along disputed mountain corridors.', impact: 'High', signal: 'High-elevation road network expansion' },
      Military: { title: 'Operational readiness', summary: 'Rapid troop movements and joint command drills are rising in frequency.', impact: 'High', signal: 'Joint exercises' },
    },
  },
  {
    id: 'pakistan',
    name: 'Pakistan',
    capital: 'Islamabad',
    borderKm: 3323,
    region: 'Western Front',
    coordinates: '30.3753° N, 69.3451° E',
    summary: 'Strategic military coordination and drone logistics operations are expanding along western frontiers.',
    threatLevel: 'High',
    stabilityIndex: 0.68,
    riskProbability: 42.00,
    categories: {
      Political: { title: 'Bilateral arrangements', summary: 'Diplomatic coordination remains concentrated on shared investment projects.', impact: 'High', signal: 'Strategic meetings' },
      Social: { title: 'Local sentiment', summary: 'Friction between local administrations and civilian groups is rising.', impact: 'Medium', signal: 'Policy and media friction' },
      Tech: { title: 'UAV logistics', summary: 'Drone assembly hubs and assembly workshops are receiving system updates.', impact: 'High', signal: 'Advanced drone assembly lines' },
      Economic: { title: 'Economic hubs', summary: 'Gwadar port infrastructure expansion continues under high security protocols.', impact: 'High', signal: 'Deepwater port projects' },
      Military: { title: 'Defense deployment', summary: 'Joint naval drills and armor transfers are verified.', impact: 'High', signal: 'Joint drills' },
    },
  },
  {
    id: 'afghanistan',
    name: 'Afghanistan',
    capital: 'Kabul',
    borderKm: 106,
    region: 'Western Front',
    coordinates: '33.9391° N, 67.7100° E',
    summary: 'Contested border crossings and weapon circulation continue to create tactical risks.',
    threatLevel: 'High',
    stabilityIndex: 0.32,
    riskProbability: 84.10,
    categories: {
      Political: { title: 'Factional struggles', summary: 'Regional commanders maintain independent authority over border tax collection.', impact: 'High', signal: 'Local command friction' },
      Social: { title: 'Refugee movements', summary: 'Migration corridors see rising surveillance and control checks.', impact: 'Medium', signal: 'Checkpoint controls' },
      Tech: { title: 'Tactical systems', summary: 'Repurposed command gear and encrypted radio channels are in use.', impact: 'High', signal: 'Military surplus deployment' },
      Economic: { title: 'Border trade', summary: 'Contraband movements bypass primary regulatory channels.', impact: 'Medium', signal: 'Frontier smuggling loops' },
      Military: { title: 'Patrol friction', summary: 'Episodic clashes with border patrols are reported.', impact: 'High', signal: 'Border post clashes' },
    },
  },
  {
    id: 'bangladesh',
    name: 'Bangladesh',
    capital: 'Dhaka',
    borderKm: 4097,
    region: 'Eastern Front',
    coordinates: '23.6850° N, 90.3563° E',
    summary: 'Maritime infrastructure is expanding and border checkpoint patrols are receiving equipment updates.',
    threatLevel: 'Moderate',
    stabilityIndex: 0.74,
    riskProbability: 26.50,
    categories: {
      Political: { title: 'Policy resets', summary: 'Administrative focus shifts toward enhanced security protocols along the eastern borders.', impact: 'Medium', signal: 'Border management updates' },
      Social: { title: 'Migration patterns', summary: 'Riverine border crossings see increased coordinate tracking.', impact: 'Medium', signal: 'River patrol checkpoints' },
      Tech: { title: 'Surveillance nodes', summary: 'Night-vision surveillance clusters are deployed along delta sectors.', impact: 'Medium', signal: 'Electro-optical installations' },
      Economic: { title: 'Transit trade', summary: 'Port traffic at Chittagong shows steady container volumes.', impact: 'Medium', signal: 'Maritime cargo telemetry' },
      Military: { title: 'Coast guard patrols', summary: 'Naval assets expand patrolling routines in the Bay of Bengal.', impact: 'Medium', signal: 'Patrol boat exercises' },
    },
  },
  {
    id: 'myanmar',
    name: 'Myanmar',
    capital: 'Naypyidaw',
    borderKm: 1643,
    region: 'Southeastern Front',
    coordinates: '21.9162° N, 95.9560° E',
    summary: 'Contested border towns and non-state military coordination are verified near eastern lines.',
    threatLevel: 'Critical',
    stabilityIndex: 0.28,
    riskProbability: 89.50,
    categories: {
      Political: { title: 'Sovereignty shifts', summary: 'Border administrations are frequently contested by non-state coalitions.', impact: 'High', signal: 'Rebel administrative hubs' },
      Social: { title: 'Civil displacement', summary: 'Displaced populations seek shelter near border corridors.', impact: 'High', signal: 'Displacement camps' },
      Tech: { title: 'Tactical drones', summary: 'Commercially modified UAVs are deployed for target observation.', impact: 'High', signal: 'Modified weaponized drone runs' },
      Economic: { title: 'Mining concessions', summary: 'Rare earth mining zones remain under local military authority.', impact: 'Medium', signal: 'Resource extraction hubs' },
      Military: { title: 'Frontier clashes', summary: 'Artillery fire and airstrikes are recorded near boundary zones.', impact: 'Critical', signal: 'Airstrikes near border' },
    },
  },
  {
    id: 'nepal',
    name: 'Nepal',
    capital: 'Kathmandu',
    borderKm: 1751,
    region: 'Northern Front',
    coordinates: '28.3949° N, 84.1240° E',
    summary: 'Infrastructure investments and new roadway routes are under review in mountain sectors.',
    threatLevel: 'Moderate',
    stabilityIndex: 0.81,
    riskProbability: 18.00,
    categories: {
      Political: { title: 'Geopolitical posture', summary: 'Kathmandu balance of alliances remains steady, with active bilateral infrastructure reviews.', impact: 'Medium', signal: 'Treaty discussions' },
      Social: { title: 'Trade routes', summary: 'Mountain checkposts verify steady local trade traffic.', impact: 'Low', signal: 'Daily transit logs' },
      Tech: { title: 'Border sensors', summary: 'Passive weather and monitoring stations operate along high passes.', impact: 'Low', signal: 'Data link reporting' },
      Economic: { title: 'Corridor funding', summary: 'Financial assistance supports mountain highway renovation projects.', impact: 'Medium', signal: 'Project finance records' },
      Military: { title: 'Boundary policing', summary: 'Armed police force detachments maintain steady patrols.', impact: 'Medium', signal: 'Scheduled patrols' },
    },
  },
  {
    id: 'bhutan',
    name: 'Bhutan',
    capital: 'Thimphu',
    borderKm: 699,
    region: 'Northern Front',
    coordinates: '27.5142° N, 90.4336° E',
    summary: 'Boundary demarcation talks continue. High-altitude monitoring stations remain active.',
    threatLevel: 'Moderate',
    stabilityIndex: 0.89,
    riskProbability: 12.50,
    categories: {
      Political: { title: 'Border negotiations', summary: 'Demarcation dialogues proceed with focus on western disputed enclaves.', impact: 'Medium', signal: 'Diplomatic releases' },
      Social: { title: 'Highland grazing', summary: 'Traditional grazing access routes see structured permit checks.', impact: 'Low', signal: 'Nomadic movement logs' },
      Tech: { title: 'Satellite surveillance', summary: 'High-resolution passes monitor construction activity in valleys.', impact: 'Medium', signal: 'Valleys construction checks' },
      Economic: { title: 'Hydro projects', summary: 'Power grid connections with neighboring networks continue normal output.', impact: 'Low', signal: 'Export megawatts telemetry' },
      Military: { title: 'Frontier checks', summary: 'Joint checks confirm boundary status remains undisturbed.', impact: 'Low', signal: 'Scheduled patrol routes' },
    },
  },
  {
    id: 'sri-lanka',
    name: 'Sri Lanka',
    capital: 'Colombo',
    borderKm: 0,
    region: 'Indian Ocean',
    coordinates: '7.8731° N, 80.7718° E',
    summary: 'Maritime intelligence monitors deepwater port facilities and naval ship visits.',
    threatLevel: 'Moderate',
    stabilityIndex: 0.72,
    riskProbability: 32.00,
    categories: {
      Political: { title: 'Maritime agreements', summary: 'Colombo structures naval access rules for research vessels.', impact: 'Medium', signal: 'Port access regulations' },
      Social: { title: 'Fisheries friction', summary: 'Straying fishing vessels generate coordinate disputes in Palk Strait.', impact: 'Medium', signal: 'Fishing boat detentions' },
      Tech: { title: 'Port logistics', summary: 'Radar installations at Hambantota port track regional ship routing.', impact: 'Medium', signal: 'AIS system monitoring' },
      Economic: { title: 'Port revenue', summary: 'Investment deals expansion supports local logistics infrastructure.', impact: 'High', signal: 'Infrastructure leasing logs' },
      Military: { title: 'Naval coordinates', summary: 'Patrol vessels execute joint Search and Rescue exercises.', impact: 'Medium', signal: 'Search & rescue drills' },
    },
  },
  {
    id: 'maldives',
    name: 'Maldives',
    capital: 'Male',
    borderKm: 0,
    region: 'Indian Ocean',
    coordinates: '3.2028° N, 73.2207° E',
    summary: 'Oceanic monitoring tracks new radar telemetry arrays and maritime flight agreements.',
    threatLevel: 'Moderate',
    stabilityIndex: 0.65,
    riskProbability: 38.50,
    categories: {
      Political: { title: 'Maritime alignments', summary: 'Male coordinates naval surveillance agreements with regional powers.', impact: 'Medium', signal: 'Security treaties' },
      Social: { title: 'Island connectivity', summary: 'Satellite internet expansions improve communication with outer atolls.', impact: 'Low', signal: 'Outer atoll bandwidth logs' },
      Tech: { title: 'Radar surveillance', summary: 'Exclusive Economic Zone radar stations report real-time ship coordinates.', impact: 'Medium', signal: 'EEZ radar updates' },
      Economic: { title: 'Reef infrastructure', summary: 'Island reclamation initiatives alter local port logistics capacity.', impact: 'Medium', signal: 'Dredging projects telemetry' },
      Military: { title: 'Coast guard drills', summary: 'Patrol cutters conduct joint operations in southern channels.', impact: 'Medium', signal: 'Patrol exercises' },
    },
  },
];

const categories: Category[] = ['Political', 'Social', 'Tech', 'Economic', 'Military'];
const trustLevels: TrustIndicator[] = ['Verified Source', 'Developing', 'Unverified', 'Rumor'];

const demoUsers = {
  'analyst@intel.local': { password: 'Intel@2026', name: 'Asha Rao', role: 'analyst', clearance: 'Regional' },
  'operator@intel.local': { password: 'Ops@2026', name: 'Ravi Menon', role: 'operator', clearance: 'Border' },
  'admin@intel.local': { password: 'Admin@2026', name: 'Ishaan Verma', role: 'admin', clearance: 'All' },
};

// Web Audio API double beep chime
function playTerminalChime() {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    
    // Pitch A5 (880Hz) followed by Pitch C6 (1046.50Hz)
    const osc1 = ctx.createOscillator();
    const gain1 = ctx.createGain();
    osc1.connect(gain1);
    gain1.connect(ctx.destination);
    osc1.type = 'sine';
    osc1.frequency.setValueAtTime(880, ctx.currentTime);
    gain1.gain.setValueAtTime(0.04, ctx.currentTime);
    gain1.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
    osc1.start();
    osc1.stop(ctx.currentTime + 0.3);

    setTimeout(() => {
      const osc2 = ctx.createOscillator();
      const gain2 = ctx.createGain();
      osc2.connect(gain2);
      gain2.connect(ctx.destination);
      osc2.type = 'sine';
      osc2.frequency.setValueAtTime(1046.50, ctx.currentTime);
      gain2.gain.setValueAtTime(0.04, ctx.currentTime);
      gain2.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
      osc2.start();
      osc2.stop(ctx.currentTime + 0.3);
    }, 100);
  } catch (e) {
    console.warn('Web Audio warning:', e);
  }
}

// In-headline key entities tooltips database
const entityContexts: Record<string, string> = {
  'PLA': "People's Liberation Army (Chinese Armed Forces). Status: active monitoring.",
  'Taliban': "Taliban regime border command units. Status: high-alert watchlist.",
  'UAV': "Unmanned Aerial Vehicle (drone sweeps detected). Status: airspace watch.",
  'Gwadar': "Gwadar Strategic Port facilities. Status: deepwater maritime monitoring.",
  'LAC': "Line of Actual Control (contested China border). Status: tactical surveillance.",
  'LoC': "Line of Control (contested Pakistan border). Status: border post alert.",
  'India': "Republic of India STRATCOM regional security nodes."
};

function App() {
  const [selectedCountry, setSelectedCountry] = useState<Country>(countries[0]);
  const [timeWindow, setTimeWindow] = useState<TimeWindow>('24h');
  const [newsFeed, setNewsFeed] = useState<Record<string, CountryIntel>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Authentication State
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [loginForm, setLoginForm] = useState({ email: '', password: '' });
  const [loginError, setLoginError] = useState('');
  const [isWebAuthnSimulating, setIsWebAuthnSimulating] = useState(false);
  const [webauthnSuccess, setWebauthnSuccess] = useState(false);

  // Polling states (60 seconds countdown)
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [countdown, setCountdown] = useState(60);

  // Fallback timeframe notification state
  const [isFallbackTimeframe, setIsFallbackTimeframe] = useState(false);

  // Streaming mechanics states
  const [streamBuffer, setStreamBuffer] = useState<{ country: string; signal: Signal }[]>([]);
  const [isUserScrolledDown, setIsUserScrolledDown] = useState(false);
  const dossierScrollRef = useRef<HTMLDivElement>(null);

  // Layout mode state
  const [layoutMode, setLayoutMode] = useState<LayoutMode>('split');

  // Keyboard navigation cursor state
  const [keyboardCursorIndex, setKeyboardCursorIndex] = useState(-1);
  const [selectedDossierSignal, setSelectedDossierSignal] = useState<Signal | null>(null);

  // Matrix Filter States
  const [filterCategory, setFilterCategory] = useState<string>('All');
  const [filterImpact, setFilterImpact] = useState<string>('All');
  const [filterTrust, setFilterTrust] = useState<string>('All');
  const [filterQuery, setFilterQuery] = useState<string>('');

  // Active Tooltip entity state
  const [hoveredEntity, setHoveredEntity] = useState<{ text: string; x: number; y: number } | null>(null);

  // Load session from local storage on mount
  useEffect(() => {
    const cached = window.localStorage.getItem('intel-session');
    if (cached) {
      setAuthUser(JSON.parse(cached) as AuthUser);
    }
  }, []);

  // API Ingestion Loop (60s trigger, category-partitioned query router)
  useEffect(() => {
    async function loadFeed() {
      if (!authUser) return;
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(`/api/news/all?category=${filterCategory}`);
        if (!response.ok) {
          throw new Error(`Feed failure ${response.status}`);
        }
        const payload = (await response.json()) as Record<string, CountryIntel>;
        
        // Enrich signals with trust levels dynamically
        const enriched: Record<string, CountryIntel> = {};
        Object.entries(payload).forEach(([countryName, data]) => {
          enriched[countryName] = {
            ...data,
            signals: (data.signals || []).map((s, idx) => ({
              ...s,
              id: `${countryName}-${idx}-${s.timestamp}`,
              trust: trustLevels[idx % trustLevels.length],
              country: countryName
            }))
          };
        });

        setNewsFeed(enriched);
      } catch (feedError) {
        setError('Mesh database offline. Using localized validated summaries.');
      } finally {
        setLoading(false);
      }
    }

    void loadFeed();
  }, [authUser, refreshTrigger, filterCategory]);

  // SSE Stream Listener (category-specific)
  useEffect(() => {
    if (!authUser) return;

    const source = new EventSource(`/api/news/stream?category=${filterCategory}`);

    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'signal') {
          const incomingSignal: Signal = {
            ...data.signal,
            country: data.country,
            trust: trustLevels[Math.floor(Math.random() * trustLevels.length)],
            id: `${data.country}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            isNew: true
          };

          // Play double chime if high-impact or triggers key entities
          const hasKeyEntity = /pla|taliban|uav|drone|clash|loc|lac/i.test(incomingSignal.headline);
          if (incomingSignal.impact === 'High' || hasKeyEntity) {
            playTerminalChime();
          }

          if (isUserScrolledDown) {
            // Buffer updates when user is scrolled down reading
            setStreamBuffer((prev) => [...prev, { country: data.country, signal: incomingSignal }]);
          } else {
            // Otherwise, prepend directly to state feed
            setNewsFeed((prev) => {
              const currentFeed = prev[data.country];
              if (!currentFeed) return prev;
              const updatedSignals = [incomingSignal, ...currentFeed.signals].slice(0, 100);
              return {
                ...prev,
                [data.country]: {
                  ...currentFeed,
                  signals: updatedSignals,
                  operational_summary: `Ingestion mesh verified. Detected ${updatedSignals.length} tactical signals in historical monitoring window. [Live Stream Update Received]`
                }
              };
            });
          }
        }
      } catch (err) {
        console.warn('Error parsing SSE payload:', err);
      }
    };

    return () => {
      source.close();
    };
  }, [authUser, isUserScrolledDown, filterCategory]);

  // Release stream buffer queue
  const releaseStreamBuffer = () => {
    if (streamBuffer.length === 0) return;

    setNewsFeed((prev) => {
      const updated = { ...prev };
      streamBuffer.forEach(({ country, signal }) => {
        const currentFeed = updated[country];
        if (currentFeed) {
          updated[country] = {
            ...currentFeed,
            signals: [signal, ...currentFeed.signals].slice(0, 100),
            operational_summary: `Ingestion mesh verified. Detected ${currentFeed.signals.length + 1} tactical signals. [Stream Buffer Flushed]`
          };
        }
      });
      return updated;
    });

    setStreamBuffer([]);
    setIsUserScrolledDown(false);
    if (dossierScrollRef.current) {
      dossierScrollRef.current.scrollTop = 0;
    }
  };

  // Scroll handler to monitor "Pause-on-Scroll"
  const handleScroll = () => {
    if (dossierScrollRef.current) {
      const top = dossierScrollRef.current.scrollTop;
      setIsUserScrolledDown(top > 60);
    }
  };

  // Countdown timer (60 seconds)
  useEffect(() => {
    if (!authUser) return;
    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          setRefreshTrigger((r) => r + 1);
          return 60;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [authUser]);

  // Reset countdown on shifts
  useEffect(() => {
    setCountdown(60);
    setKeyboardCursorIndex(-1);
  }, [selectedCountry.id, timeWindow, refreshTrigger]);

  // Current selected country live intelligence info
  const selectedIntel = newsFeed[selectedCountry.name];
  const selectedSummary = selectedIntel?.operational_summary || selectedCountry.summary;
  const isSelectedDegraded = selectedIntel?.source_status === 'degraded_mesh';

  // Filter selected signals based on timeWindow, Matrix filters, and relevance score
  const selectedSignalsFiltered = useMemo(() => {
    const raw = selectedIntel?.signals ?? [];
    if (raw.length === 0) return [];

    const now = new Date();
    let windowMs = 7 * 24 * 60 * 60 * 1000; // default 7 days
    if (timeWindow === '1m') windowMs = 60 * 1000;
    else if (timeWindow === '5m') windowMs = 5 * 60 * 1000;
    else if (timeWindow === '1h') windowMs = 60 * 60 * 1000;
    else if (timeWindow === '24h') windowMs = 24 * 60 * 60 * 1000;
    else if (timeWindow === '7d') windowMs = 7 * 24 * 60 * 60 * 1000;

    const threshold = new Date(now.getTime() - windowMs);
    let filtered = raw.filter(s => new Date(s.timestamp) >= threshold);

    // Apply matrix parameters filters
    if (filterCategory !== 'All') {
      filtered = filtered.filter(s => s.category === filterCategory);
    }
    if (filterImpact !== 'All') {
      filtered = filtered.filter(s => s.impact === filterImpact);
    }
    if (filterTrust !== 'All') {
      filtered = filtered.filter(s => s.trust === filterTrust);
    }
    if (filterQuery.trim()) {
      const q = filterQuery.toLowerCase();
      filtered = filtered.filter(s => s.headline.toLowerCase().includes(q) || s.summary.toLowerCase().includes(q));
    }

    if (filtered.length === 0) {
      // Fallback: show next most relevant signals
      setIsFallbackTimeframe(true);
      return raw;
    } else {
      setIsFallbackTimeframe(false);
      return filtered;
    }
  }, [selectedIntel, timeWindow, filterCategory, filterImpact, filterTrust, filterQuery]);

  // Keyboard navigation shortcut listeners (J/K/Enter/Esc/Slash)
  useEffect(() => {
    if (!authUser) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (document.activeElement?.tagName === 'INPUT' || document.activeElement?.tagName === 'SELECT') {
        if (e.key === 'Escape') {
          (document.activeElement as HTMLElement).blur();
        }
        return;
      }

      if (e.key === 'j' || e.key === 'J') {
        setKeyboardCursorIndex((prev) => Math.min(selectedSignalsFiltered.length - 1, prev + 1));
      } else if (e.key === 'k' || e.key === 'K') {
        setKeyboardCursorIndex((prev) => Math.max(0, prev - 1));
      } else if (e.key === 'Enter') {
        if (keyboardCursorIndex >= 0 && keyboardCursorIndex < selectedSignalsFiltered.length) {
          const sig = selectedSignalsFiltered[keyboardCursorIndex];
          setSelectedDossierSignal(sig);
          if (layoutMode !== 'split') {
            setLayoutMode('split');
          }
        }
      } else if (e.key === 'Escape') {
        setSelectedDossierSignal(null);
      } else if (e.key === '/') {
        e.preventDefault();
        const searchInput = document.getElementById('coords-search-input');
        if (searchInput) searchInput.focus();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [authUser, selectedSignalsFiltered, keyboardCursorIndex, layoutMode]);

  const handleLogin = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoginError('');
    const seed = demoUsers[loginForm.email as keyof typeof demoUsers];
    if (!seed) {
      setLoginError('Unknown coordinate key. Use: analyst@intel.local');
      return;
    }
    if (loginForm.password !== seed.password) {
      setLoginError('MFA Credentials mismatch.');
      return;
    }
    
    // Simulate Fingerprint / Security Key MFA
    setIsWebAuthnSimulating(true);
  };

  const executeMfaSuccess = () => {
    setWebauthnSuccess(true);
    setTimeout(() => {
      const seed = demoUsers[loginForm.email as keyof typeof demoUsers];
      const user: AuthUser = {
        id: loginForm.email,
        name: seed.name,
        role: seed.role.toUpperCase(),
        clearance: seed.role === 'admin' ? 'SEC LEVEL 9-A' : seed.role === 'operator' ? 'SEC LEVEL 7-B' : 'SEC LEVEL 5-C',
      };
      window.localStorage.setItem('intel-session', JSON.stringify(user));
      setAuthUser(user);
      setIsWebAuthnSimulating(false);
      setWebauthnSuccess(false);
    }, 800);
  };

  const handleLogout = () => {
    window.localStorage.removeItem('intel-session');
    setAuthUser(null);
    setLoginForm({ email: '', password: '' });
  };

  // Highlight key entities inside headlines and create tooltips triggers using dynamic NER parser
  const renderHeadlineWithEntityTooltips = (text: string) => {
    // Matches capitalized terms or acronyms (e.g. PLA, Aksai Chin, S-400)
    const entityRegex = /\b([A-Z][a-zA-Z0-9\-]+(?:\s+[A-Z][a-zA-Z0-9\-]+)*)\b/g;
    
    const matches = Array.from(text.matchAll(entityRegex));
    if (matches.length === 0) return <span>{text}</span>;

    const ignoreWords = new Set([
      'The', 'A', 'An', 'And', 'Or', 'But', 'If', 'By', 'In', 'On', 'At', 'To', 'For', 'With', 'From', 
      'After', 'Before', 'Under', 'Above', 'Below', 'Is', 'Are', 'Was', 'Were', 'Be', 'Been', 'Being', 
      'Have', 'Has', 'Had', 'Do', 'Does', 'Did', 'Will', 'Would', 'Shall', 'Should', 'Can', 'Could', 
      'May', 'Might', 'Must', 'Exclusive', 'July', 'June', 'August', 'September', 'October', 'November', 
      'December', 'January', 'February', 'March', 'April', 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 
      'Thursday', 'Friday', 'Saturday', 'Why', 'How', 'What', 'Who', 'Where', 'When', 'Satellite', 
      'Border', 'Military', 'Political', 'Tech', 'Social', 'Economic', 'Active', 'Incident', 'Reports', 
      'Detected', 'Source', 'Wire', 'Ingestion', 'Ingested', 'Operational', 'Show', 'Showing', 'Latest',
      'Incidents', 'New', 'Update', 'Updates', 'Receive', 'Received', 'Incidents', 'Incident', 'Status',
      'Stable', 'Detected'
    ]);

    const parts: { text: string; isEntity: boolean }[] = [];
    let lastIndex = 0;
    
    let match;
    entityRegex.lastIndex = 0;
    while ((match = entityRegex.exec(text)) !== null) {
      const matchText = match[1];
      const matchIndex = match.index;
      
      if (matchIndex > lastIndex) {
        parts.push({ text: text.substring(lastIndex, matchIndex), isEntity: false });
      }
      
      const isIgnored = ignoreWords.has(matchText) || /^[0-9\-]+$/.test(matchText);
      parts.push({ text: matchText, isEntity: !isIgnored });
      lastIndex = entityRegex.lastIndex;
    }
    
    if (lastIndex < text.length) {
      parts.push({ text: text.substring(lastIndex), isEntity: false });
    }

    return (
      <>
        {parts.map((part, index) => {
          if (part.isEntity) {
            const context = entityContexts[part.text] || 
              `[Telemetry Profile]: Dynamic Entity parsed in selected boundary sector. Corresponds to local geographic coordinate, tactical asset, or strategic actor under active monitoring.`;
            return (
              <span
                key={index}
                className="text-[#7bd0ff] font-bold border-b border-dashed border-[#7bd0ff]/40 cursor-help hover:text-[#dae2fd] transition-colors relative"
                onMouseEnter={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  setHoveredEntity({
                    text: context,
                    x: rect.left,
                    y: rect.top - 50
                  });
                }}
                onMouseLeave={() => setHoveredEntity(null)}
              >
                {part.text}
              </span>
            );
          }
          return part.text;
        })}
      </>
    );
  };

  // Login MFA Screen
  if (!authUser) {
    return (
      <div className="min-h-screen flex flex-col font-mono relative overflow-hidden tactical-gradient">
        {/* Subtle dot backdrop */}
        <div className="fixed inset-0 pointer-events-none opacity-20">
          <div className="absolute top-0 left-0 w-full h-full" style={{ backgroundImage: 'radial-gradient(#1e293b 1px, transparent 1px)', backgroundSize: '32px 32px' }} />
        </div>

        {/* Header */}
        <header className="w-full flex justify-between items-center px-6 py-4 z-10">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-[#7bd0ff]" style={{ fontVariationSettings: "'FILL' 1" }}>security</span>
            <h1 className="text-xl font-bold tracking-wider text-[#7bd0ff]">GEOSPATIAL HUB</h1>
          </div>
          <div>
            <span className="text-xs text-[#ffb4ab] border border-[#ffb4ab]/30 px-2 py-0.5 bg-[#93000a]/10">SECURE SHELL GATE</span>
          </div>
        </header>

        {/* Auth Canvas */}
        <main className="flex-grow flex items-center justify-center px-6 z-10">
          <div className="max-w-md w-full">
            <div className="bg-[#122131] border border-[#45464d] p-8 relative glow-border overflow-hidden rounded">
              <div className="scan-line" />

              {!isWebAuthnSimulating ? (
                /* Stage 1: Key credential inputs */
                <form className="space-y-6" onSubmit={handleLogin}>
                  <div className="text-center mb-6">
                    <h2 className="text-[#d4e4fa] text-lg font-bold">Node credentials</h2>
                    <p className="text-xs text-[#c6c6cd] mt-1">Provide credentials to initialize security key biometric verification.</p>
                  </div>

                  <div className="space-y-4">
                    <div>
                      <label className="block text-xs uppercase tracking-wider text-[#c6c6cd] mb-1">Email</label>
                      <input
                        type="email"
                        required
                        className="w-full bg-[#051424] border border-[#45464d] px-3 py-2 text-sm text-[#d4e4fa] focus:border-[#7bd0ff] focus:ring-0 focus:outline-none rounded"
                        value={loginForm.email}
                        onChange={(e) => setLoginForm((prev) => ({ ...prev, email: e.target.value }))}
                        placeholder="analyst@intel.local"
                      />
                    </div>
                    <div>
                      <label className="block text-xs uppercase tracking-wider text-[#c6c6cd] mb-1">Password</label>
                      <input
                        type="password"
                        required
                        className="w-full bg-[#051424] border border-[#45464d] px-3 py-2 text-sm text-[#d4e4fa] focus:border-[#7bd0ff] focus:ring-0 focus:outline-none rounded"
                        value={loginForm.password}
                        onChange={(e) => setLoginForm((prev) => ({ ...prev, password: e.target.value }))}
                        placeholder="••••••••"
                      />
                    </div>
                  </div>

                  {loginError && <p className="text-xs text-[#ffb4ab] bg-[#93000a]/20 border border-[#ffb4ab]/30 p-2 rounded text-center">{loginError}</p>}

                  <button
                    type="submit"
                    className="w-full py-3 bg-[#7bd0ff]/10 hover:bg-[#7bd0ff]/20 text-[#7bd0ff] border border-[#7bd0ff]/40 text-xs uppercase tracking-wider font-bold transition-all rounded"
                  >
                    Initiate Authentication
                  </button>

                  <div className="border-t border-[#45464d] pt-4 text-[10px] text-[#c6c6cd]/60 space-y-1">
                    <p>Demo profiles:</p>
                    <p>• analyst@intel.local / Intel@2026 (Analyst)</p>
                    <p>• operator@intel.local / Ops@2026 (Operator)</p>
                  </div>
                </form>
              ) : (
                /* Stage 2: WebAuthn verification simulation */
                <div className="text-center py-4">
                  <div className="flex justify-center mb-6">
                    <div className="inline-flex items-center px-3 py-1 bg-[#1c2b3c] border border-[#4edea3]/30 rounded-full">
                      <span className="material-symbols-outlined text-[#4edea3] text-sm mr-2" style={{ fontVariationSettings: "'FILL' 1" }}>verified_user</span>
                      <span className="text-[10px] font-bold text-[#4edea3] tracking-widest uppercase">Zero-Trust Authenticated</span>
                    </div>
                  </div>

                  <div className="relative w-24 h-24 mx-auto mb-6 group cursor-pointer" id="auth-trigger" onClick={executeMfaSuccess}>
                    <div className="absolute inset-0 rounded-full bg-[#7bd0ff]/10 blur-xl group-hover:bg-[#7bd0ff]/20 transition-all duration-500" />
                    <div className="absolute inset-0 border border-[#7bd0ff]/40 rounded-full border-dashed spin-dashed-custom" />
                    <div className="relative w-full h-full border border-[#7bd0ff] flex items-center justify-center rounded-full bg-[#010f1f] transition-transform duration-300 group-hover:scale-105 active:scale-95">
                      <span
                        className={`material-symbols-outlined text-4xl transition-colors duration-300 ${
                          webauthnSuccess ? 'text-[#4edea3]' : 'text-[#7bd0ff]'
                        }`}
                        style={{ fontVariationSettings: "'FILL' 0" }}
                      >
                        {webauthnSuccess ? 'verified' : 'fingerprint'}
                      </span>
                    </div>
                  </div>

                  <h2 className="text-[#d4e4fa] text-lg font-bold mb-1">MFA Verification</h2>
                  <p className="text-xs text-[#c6c6cd] max-w-xs mx-auto mb-6 leading-relaxed">
                    Touch your <span className="text-[#7bd0ff] font-semibold">Security Key</span> or simulate biometrics above to initialize STRATCOM access.
                  </p>

                  <div className="space-y-2 bg-[#0d1c2d] p-4 border border-[#45464d] text-left text-xs">
                    <div className="flex justify-between">
                      <span className="text-[#c6c6cd]">PROTOCOL</span>
                      <span className="text-[#bec6e0] font-bold">WEBAUTHN 2.0</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-[#c6c6cd]">ENCRYPTION</span>
                      <span className="text-[#bec6e0] font-bold">AES-256-GCM</span>
                    </div>
                  </div>

                  <button
                    onClick={() => setIsWebAuthnSimulating(false)}
                    className="mt-6 text-xs text-[#c6c6cd]/75 hover:text-[#d4e4fa] underline underline-offset-4"
                  >
                    Cancel Request
                  </button>
                </div>
              )}
            </div>
            <div className="mt-6 text-center">
              <p className="text-[9px] text-[#c6c6cd]/40 tracking-widest uppercase">Obsidian Sentinel Security Framework v4.2.1</p>
            </div>
          </div>
        </main>

        {/* Global Footer */}
        <footer className="w-full bg-[#051424] border-t border-[#93000a] py-2 px-6 flex justify-between items-center text-[10px] text-[#c6c6cd]">
          <span>CLASSIFIED // TOP SECRET // NODE 9-B</span>
          <div className="flex items-center gap-1.5 text-[#ffb4ab]">
            <span className="w-1.5 h-1.5 rounded-full bg-[#ffb4ab] animate-ping" />
            <span className="font-bold">SYSTEM THREAT LEVEL: LOW</span>
          </div>
        </footer>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#051424] text-[#d4e4fa] flex flex-col font-sans overflow-hidden select-none relative">
      {/* Global Backdrop Tooltip Element */}
      {hoveredEntity && (
        <div
          className="fixed z-50 p-3 bg-[#122131]/95 border border-[#7bd0ff]/40 rounded backdrop-blur-md shadow-2xl text-xs font-mono max-w-xs transition-all pointer-events-none"
          style={{ left: `${hoveredEntity.x}px`, top: `${hoveredEntity.y}px`, transform: 'translateY(-100%)', marginTop: '-8px' }}
        >
          <div className="flex items-center gap-1.5 mb-1.5 text-[#7bd0ff] font-bold">
            <span className="material-symbols-outlined text-sm">info</span>
            <span>ENTITY BRIEF</span>
          </div>
          <p className="text-[#c6c6cd] leading-normal">{hoveredEntity.text}</p>
        </div>
      )}

      {/* Dynamic depleting bar at the top (60s timer) */}
      <div className="absolute top-0 left-0 w-full h-[2px] bg-[#122131] z-50">
        <div className="h-full bg-gradient-to-r from-[#7bd0ff] to-[#4edea3] transition-all duration-1000" style={{ width: `${(countdown / 60) * 100}%` }} />
      </div>

      {/* Main Header */}
      <header className="h-14 border-b border-[#45464d]/60 flex items-center justify-between px-6 bg-[#051424] z-20 shrink-0">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-[#7bd0ff] text-2xl" style={{ fontVariationSettings: "'FILL' 1" }}>security</span>
            <span className="font-mono text-base font-bold tracking-widest text-[#7bd0ff]">GEOSPATIAL HUB</span>
          </div>
          <div className="h-4 w-px bg-[#45464d]/60" />
          <div className="px-3 py-1.5 text-xs font-mono text-[#7bd0ff] font-bold border border-[#7bd0ff]/30 bg-[#1c2b3c] rounded">
            STRATCOM Dashboard Gate
          </div>
        </div>

        <div className="flex items-center gap-4">
          <div className="relative">
            <input
              id="coords-search-input"
              type="text"
              placeholder="QUERY COORDINATES... [/]"
              className="bg-[#122131] border border-[#45464d] text-[11px] font-mono text-[#d4e4fa] placeholder-[#c6c6cd]/50 pl-8 pr-3 py-1 w-48 focus:w-64 transition-all duration-300 focus:outline-none focus:border-[#7bd0ff] rounded"
            />
            <span className="material-symbols-outlined text-[#c6c6cd]/50 text-base absolute left-2 top-1.5">search</span>
          </div>

          <button className="relative p-1 hover:text-[#7bd0ff]" title="System Notifications">
            <span className="material-symbols-outlined text-xl">notifications</span>
            <span className="absolute top-1 right-1 w-1.5 h-1.5 bg-[#4edea3] rounded-full" />
          </button>

          <button className="p-1 hover:text-[#7bd0ff]" title="Console Configuration">
            <span className="material-symbols-outlined text-xl">settings</span>
          </button>

          <div className="flex items-center gap-2 border-l border-[#45464d]/60 pl-4">
            <div className="text-right font-mono">
              <p className="text-[10px] text-[#7bd0ff] font-bold">{authUser.name}</p>
              <p className="text-[9px] text-[#c6c6cd] opacity-60">{authUser.role}</p>
            </div>
            <button
              onClick={handleLogout}
              className="material-symbols-outlined text-xl text-[#ffb4ab] hover:text-[#ffdad6] hover:scale-105 transition-transform"
              title="Logout session"
            >
              logout
            </button>
          </div>
        </div>
      </header>

      {/* Primary Workspace */}
      <div className="flex-grow flex overflow-hidden">
        {/* Left Control Panel / Sidebar */}
        <aside className="w-56 border-r border-[#45464d]/60 bg-[#010f1f] flex flex-col justify-between shrink-0 font-mono">
          <div className="p-4 space-y-6 flex-grow flex flex-col overflow-hidden">
            {/* Operator Box */}
            <div className="bg-[#122131] border border-[#45464d]/70 p-3 rounded relative overflow-hidden shrink-0">
              <div className="absolute top-0 right-0 p-1">
                <span className="material-symbols-outlined text-[10px] text-[#4edea3] animate-pulse">radar</span>
              </div>
              <p className="text-[10px] font-bold text-[#c6c6cd] uppercase tracking-wider">STRATCOM-ALPHA</p>
              <p className="text-[9px] text-[#4edea3] flex items-center gap-1 mt-1">
                <span className="w-1.5 h-1.5 bg-[#4edea3] rounded-full pulse-soft" />
                {authUser.clearance}
              </p>
              <button
                onClick={() => setRefreshTrigger((r) => r + 1)}
                className="mt-3 w-full py-1.5 text-[9px] font-bold text-[#7bd0ff] border border-[#7bd0ff]/30 hover:bg-[#7bd0ff]/10 transition-colors uppercase rounded"
              >
                Force Sync Data
              </button>
            </div>

            {/* Country list dossier selector */}
            <div className="flex-grow flex flex-col overflow-hidden">
              <p className="text-[9px] text-[#c6c6cd] uppercase tracking-wider pl-2 mb-2 font-bold opacity-60 shrink-0">Neighbor Dossiers</p>
              <div className="flex-grow overflow-y-auto space-y-1 pr-1">
                {countries.map((c) => {
                  const intel = newsFeed[c.name];
                  const hasSignals = (intel?.signals?.length || 0) > 0;
                  const isSelected = selectedCountry.id === c.id;

                  return (
                    <button
                      key={c.id}
                      onClick={() => setSelectedCountry(c)}
                      className={`w-full text-left px-3 py-2 text-xs transition-colors border rounded flex justify-between items-center ${
                        isSelected
                          ? 'bg-[#1c2b3c] text-[#7bd0ff] border-[#7bd0ff]/50 font-bold'
                          : 'text-[#c6c6cd] hover:text-[#d4e4fa] hover:bg-[#122131]/30 border-transparent'
                      }`}
                    >
                      <span className="truncate">{c.name}</span>
                      {hasSignals && (
                        <span className="w-1.5 h-1.5 bg-[#4edea3] rounded-full pulse-soft shrink-0 ml-1" />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="p-4 border-t border-[#45464d]/40 bg-[#051424]/40 space-y-2 shrink-0">
            <div className="flex justify-between items-center text-[10px] text-[#c6c6cd]">
              <span>MESH STATUS</span>
              <span className="text-[#4edea3] font-bold">ONLINE</span>
            </div>
            <div className="flex justify-between items-center text-[10px] text-[#c6c6cd]">
              <span>SYNC IN</span>
              <span className="font-mono text-[#bec6e0]">{countdown}s</span>
            </div>
          </div>
        </aside>

        {/* Dossier Grid Landing Page Canvas */}
        <div className="flex-grow flex flex-col overflow-hidden">
          {/* Sticky Multi-Dimensional Matrix Filter Bar */}
          <div className="bg-[#0d1c2d]/90 border-b border-[#45464d]/60 px-6 py-3 flex flex-wrap items-center justify-between gap-4 z-10 shrink-0 font-mono text-xs">
            <div className="flex flex-wrap items-center gap-3">
              {/* Category */}
              <div>
                <label className="text-[10px] text-[#c6c6cd] uppercase block mb-1">Category</label>
                <select
                  value={filterCategory}
                  onChange={(e) => setFilterCategory(e.target.value)}
                  className="bg-[#122131] border border-[#45464d] text-xs text-[#d4e4fa] px-2.5 py-1 focus:outline-none focus:border-[#7bd0ff] rounded"
                >
                  <option value="All">All Categories</option>
                  {categories.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>

              {/* Impact */}
              <div>
                <label className="text-[10px] text-[#c6c6cd] uppercase block mb-1">Impact</label>
                <select
                  value={filterImpact}
                  onChange={(e) => setFilterImpact(e.target.value)}
                  className="bg-[#122131] border border-[#45464d] text-xs text-[#d4e4fa] px-2.5 py-1 focus:outline-none focus:border-[#7bd0ff] rounded"
                >
                  <option value="All">All Impacts</option>
                  <option value="High">High</option>
                  <option value="Medium">Medium</option>
                  <option value="Low">Low</option>
                </select>
              </div>

              {/* Trust level */}
              <div>
                <label className="text-[10px] text-[#c6c6cd] uppercase block mb-1">Trust Pipeline</label>
                <select
                  value={filterTrust}
                  onChange={(e) => setFilterTrust(e.target.value)}
                  className="bg-[#122131] border border-[#45464d] text-xs text-[#d4e4fa] px-2.5 py-1 focus:outline-none focus:border-[#7bd0ff] rounded"
                >
                  <option value="All">All Pipelines</option>
                  {trustLevels.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>

              {/* Text search */}
              <div>
                <label className="text-[10px] text-[#c6c6cd] uppercase block mb-1">Search Keywords</label>
                <input
                  type="text"
                  value={filterQuery}
                  onChange={(e) => setFilterQuery(e.target.value)}
                  placeholder="Filter signals..."
                  className="bg-[#122131] border border-[#45464d] text-xs text-[#d4e4fa] px-2.5 py-1 w-40 focus:w-48 focus:outline-none focus:border-[#7bd0ff] rounded"
                />
              </div>
            </div>

            {/* Layout Toggles */}
            <div className="flex items-center gap-1.5 bg-[#122131] border border-[#45464d] p-0.5 rounded">
              {(['single', 'triple', 'split'] as LayoutMode[]).map((mode) => (
                <button
                  key={mode}
                  onClick={() => {
                    setLayoutMode(mode);
                    setKeyboardCursorIndex(-1);
                  }}
                  className={`px-3 py-1 uppercase text-[10px] font-bold rounded transition-colors ${
                    layoutMode === mode ? 'bg-[#1c2b3c] text-[#7bd0ff] border border-[#7bd0ff]/30' : 'text-[#c6c6cd] hover:text-[#d4e4fa]'
                  }`}
                  title={`${mode} view layout`}
                >
                  {mode === 'single' ? 'Single col' : mode === 'triple' ? 'Triple grid' : 'Split Workspace'}
                </button>
              ))}
            </div>
          </div>

          {/* Dossier workspace grid */}
          <div className="flex-grow flex overflow-hidden relative">
            {/* Scrollable Container with scroll monitor */}
            <div
              ref={dossierScrollRef}
              onScroll={handleScroll}
              className="flex-grow overflow-y-auto p-6 space-y-6"
            >
              {/* Intelligent pause buffer warning bar */}
              {streamBuffer.length > 0 && (
                <div
                  onClick={releaseStreamBuffer}
                  className="sticky top-0 z-20 flex items-center justify-between bg-[#7bd0ff] hover:bg-[#c4e7ff] text-[#051424] font-mono text-xs font-bold py-2 px-4 shadow-lg cursor-pointer rounded transition-colors animate-pulse"
                >
                  <span className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-base">update</span>
                    {streamBuffer.length} breaking update{streamBuffer.length > 1 ? 's' : ''} buffered. Click to release and scroll to top.
                  </span>
                  <span className="underline">RELEASE WIRE FEED</span>
                </div>
              )}

              {/* Dossier Header */}
              <div className="flex justify-between items-start border-b border-[#45464d]/40 pb-4 shrink-0">
                <div>
                  <span className="text-[10px] font-mono text-[#4edea3] uppercase tracking-widest flex items-center gap-1">
                    <span className="w-1.5 h-1.5 bg-[#4edea3] rounded-full pulse-soft" />
                    Live Operational Feed
                  </span>
                  <h2 className="text-xl font-bold tracking-tight text-[#d4e4fa] mt-1">{selectedCountry.name.toUpperCase()}: DEEP-DIVE DOSSIER</h2>
                  <p className="text-xs text-[#c6c6cd] mt-0.5 font-mono">{selectedCountry.capital} • {selectedCountry.borderKm} km border • {selectedCountry.region}</p>
                </div>

                <div className="flex items-center gap-2">
                  <div className="bg-[#122131] border border-[#45464d] flex rounded p-0.5">
                    {(['1m', '5m', '1h', '24h', '7d'] as TimeWindow[]).map((t) => (
                      <button
                        key={t}
                        onClick={() => setTimeWindow(t)}
                        className={`px-2.5 py-1 text-[10px] font-mono uppercase rounded transition-colors ${
                          timeWindow === t ? 'bg-[#1c2b3c] text-[#7bd0ff] font-bold' : 'text-[#c6c6cd] hover:text-[#d4e4fa]'
                        }`}
                      >
                        {t === '1m' ? '1 MIN' : t === '5m' ? '5 MIN' : t === '1h' ? '1 HR' : t === '24h' ? '24 HR' : '7 DAY'}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Fallback Notice */}
              {isFallbackTimeframe && (
                <div className="p-3 bg-[#fbbf24]/10 border border-[#fbbf24]/30 rounded text-xs text-[#fbbf24] font-mono uppercase tracking-wider">
                  [Telemetry Notice]: No active signals detected in the strict selected {timeWindow} window. Displaying next most relevant historical data.
                </div>
              )}

              {/* Dossier Summary Status */}
              <div className={`p-4 border rounded ${isSelectedDegraded ? 'border-[#fbbf24]/50 border-dashed bg-[#fbbf24]/5' : 'border-[#45464d]/60 bg-[#122131]/20'}`}>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-[10px] font-mono text-[#7bd0ff] uppercase font-bold tracking-widest">Operational summary</span>
                  {isSelectedDegraded && <span className="text-[10px] font-mono text-[#fbbf24] font-bold uppercase blink">[Feed Degraded - Retrying]</span>}
                </div>
                <p className={`text-xs leading-relaxed ${selectedSummary.includes('STATUS: STABLE') ? 'text-[#4edea3] font-mono bg-[#4edea3]/5 border border-[#4edea3]/20 p-2.5 rounded' : 'text-[#d4e4fa]'}`}>
                  {selectedSummary}
                </p>
                {loading && <p className="text-[10px] text-[#7bd0ff] mt-2 font-mono flex items-center gap-1.5"><span className="w-1.5 h-1.5 bg-[#7bd0ff] rounded-full animate-ping" /> Re-evaluating secure channels...</p>}
                {error && <p className="text-[10px] text-[#ffb4ab] mt-2 font-mono">{error}</p>}
              </div>

              {/* LAYOUT RENDER MODES */}
              {/* layoutMode === 'split' */}
              {layoutMode === 'split' && (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {categories.map((category) => {
                    const fallback = selectedCountry.categories[category];
                    const categorySignals = selectedSignalsFiltered.filter((s) => s.category === category);
                    
                    return (
                      <article key={category} className="bg-[#122131] border border-[#45464d] p-4 flex flex-col justify-between min-h-[220px] rounded hover:border-[#7bd0ff]/30 transition-colors news-card-container">
                        <div>
                          <div className="flex justify-between items-center mb-2.5 border-b border-[#45464d]/30 pb-1.5">
                            <span className="text-xs font-mono font-bold text-[#7bd0ff] tracking-widest uppercase">{category}</span>
                            <span className="text-[9px] text-[#ffb4ab] font-mono">
                              IMPACT: {categorySignals.length > 0 
                                ? `${categorySignals[0].impact} (Score: ${Math.round(categorySignals[0].relevance_score ?? 0)})` 
                                : fallback.impact}
                            </span>
                          </div>
                          
                          {categorySignals.length > 0 ? (
                            <div className="space-y-3">
                              {categorySignals.slice(0, 2).map((sig, idx) => {
                                const sigIndex = selectedSignalsFiltered.findIndex(s => s.id === sig.id);
                                const isFocused = keyboardCursorIndex === sigIndex;
                                return (
                                  <div
                                    key={idx}
                                    onClick={() => setSelectedDossierSignal(sig)}
                                    className={`space-y-1 text-left p-1.5 cursor-pointer rounded transition-all hover:bg-[#1c2b3c]/20 ${
                                      isFocused ? 'keyboard-focus border border-[#7bd0ff]' : ''
                                    } ${sig.isNew ? 'stream-slide-in delta-update-glow-green' : ''}`}
                                  >
                                    <h4 className="text-xs font-bold text-[#d4e4fa] hover:text-[#7bd0ff] transition-colors leading-snug">
                                      {sig.is_breaking && (
                                        <span className="inline-block bg-[#ff3b30]/20 text-[#ff453a] text-[8px] font-bold font-mono uppercase px-1.5 py-0.5 border border-[#ff453a]/30 rounded animate-pulse mr-1.5">
                                          [BREAKING]
                                        </span>
                                      )}
                                      {renderHeadlineWithEntityTooltips(sig.headline)}
                                    </h4>
                                    <div className="flex justify-between items-center text-[9px] font-mono uppercase">
                                      <span className="text-[#4edea3] font-bold">{sig.trust}</span>
                                      <span className="text-[#c6c6cd] opacity-75">{new Date(sig.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <div className="space-y-2">
                              <span className="text-[9px] font-mono text-[#fbbf24]/70 uppercase tracking-wider block">[NO RECENT SIGNAL IN WINDOW]</span>
                              <h4 className="text-xs font-bold text-[#d4e4fa]/80">{fallback.title}</h4>
                              <p className="text-[11px] text-[#c6c6cd]/75 leading-relaxed line-clamp-3">{fallback.summary}</p>
                              <p className="text-[9px] font-mono text-[#c6c6cd]/40 uppercase">SIGNAL: {fallback.signal}</p>
                            </div>
                          )}
                        </div>
                      </article>
                    );
                  })}

                  {/* UAV Chart Card */}
                  <article className="bg-[#122131] border border-[#45464d] p-4 flex flex-col justify-between min-h-[220px] rounded news-card-container">
                    <div className="flex justify-between items-start mb-2 border-b border-[#45464d]/30 pb-1.5">
                      <span className="text-xs font-mono font-bold text-[#7bd0ff] tracking-widest uppercase">UAV Production Ramp</span>
                      <span className="text-[10px] text-[#ffb4ab] font-mono">TACTICAL RISK: HIGH</span>
                    </div>
                    <div className="h-20 flex items-end gap-2 justify-center my-3">
                      <div className="bg-[#4edea3] w-4 h-[20%] rounded-t-sm" title="Q1: 20%" />
                      <div className="bg-[#4edea3] w-4 h-[35%] rounded-t-sm" title="Q2: 35%" />
                      <div className="bg-[#4edea3] w-4 h-[50%] rounded-t-sm" title="Q3: 50%" />
                      <div className="bg-[#4edea3] w-4 h-[80%] rounded-t-sm" title="Q4: 80%" />
                      <div className="bg-[#ffb4ab] w-4 h-[95%] rounded-t-sm animate-pulse" title="Target: 95%" />
                    </div>
                    <div className="text-[9px] font-mono text-[#c6c6cd]/50 uppercase text-center pt-2 border-t border-[#45464d]/10">
                      Output Vol: Contested Border Assembly
                    </div>
                  </article>
                </div>
              )}

              {/* layoutMode === 'single' */}
              {layoutMode === 'single' && (
                <div className="space-y-4">
                  {selectedSignalsFiltered.map((sig, idx) => {
                    const isFocused = keyboardCursorIndex === idx;
                    return (
                      <div
                        key={sig.id || idx}
                        onClick={() => setSelectedDossierSignal(sig)}
                        className={`bg-[#122131] border border-[#45464d] p-4 hover:border-[#7bd0ff]/30 transition-colors cursor-pointer rounded flex flex-col gap-2 relative news-card-container ${
                          isFocused ? 'keyboard-focus border border-[#7bd0ff]' : ''
                        } ${sig.isNew ? 'stream-slide-in delta-update-glow-green' : ''}`}
                      >
                        <div className="flex justify-between items-center text-[10px] font-mono">
                          <span className="text-[#7bd0ff] font-bold uppercase">{sig.category} - {sig.source}</span>
                          <span className="text-[#c6c6cd] opacity-75">{new Date(sig.timestamp).toLocaleTimeString()}</span>
                        </div>
                        <h3 className="text-sm font-bold text-[#d4e4fa]">
                          {sig.is_breaking && (
                            <span className="inline-block bg-[#ff3b30]/20 text-[#ff453a] text-[8px] font-bold font-mono uppercase px-1.5 py-0.5 border border-[#ff453a]/30 rounded animate-pulse mr-1.5">
                              [BREAKING ALERT]
                            </span>
                          )}
                          {renderHeadlineWithEntityTooltips(sig.headline)}
                        </h3>
                        <p className="text-xs text-[#c6c6cd] leading-normal">{sig.summary}</p>
                        <div className="flex justify-between items-center text-[9px] font-mono">
                          <span className="bg-[#1c2b3c] text-[#7bd0ff] px-2 py-0.5 rounded border border-[#7bd0ff]/20 font-bold uppercase">{sig.trust}</span>
                          <span className="text-[#ffb4ab] font-bold">IMPACT: {sig.impact} (Score: {Math.round(sig.relevance_score ?? 0)})</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* layoutMode === 'triple' */}
              {layoutMode === 'triple' && (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {selectedSignalsFiltered.map((sig, idx) => {
                    const isFocused = keyboardCursorIndex === idx;
                    return (
                      <div
                        key={sig.id || idx}
                        onClick={() => setSelectedDossierSignal(sig)}
                        className={`bg-[#122131] border border-[#45464d] p-4 hover:border-[#7bd0ff]/30 transition-colors cursor-pointer rounded flex flex-col justify-between min-h-[180px] news-card-container ${
                          isFocused ? 'keyboard-focus border border-[#7bd0ff]' : ''
                        } ${sig.isNew ? 'stream-slide-in delta-update-glow-green' : ''}`}
                      >
                        <div>
                          <div className="flex justify-between items-center text-[9px] font-mono mb-2 border-b border-[#45464d]/30 pb-1">
                            <span className="text-[#7bd0ff] font-bold uppercase">{sig.category}</span>
                            <span className="text-[#c6c6cd] opacity-75">{new Date(sig.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                          </div>
                          <h3 className="text-xs font-bold text-[#d4e4fa] leading-snug mb-1">
                            {sig.is_breaking && (
                              <span className="inline-block bg-[#ff3b30]/20 text-[#ff453a] text-[8px] font-bold font-mono uppercase px-1.5 py-0.5 border border-[#ff453a]/30 rounded animate-pulse mr-1.5">
                                [BREAKING]
                              </span>
                            )}
                            {renderHeadlineWithEntityTooltips(sig.headline)}
                          </h3>
                          <p className="text-[11px] text-[#c6c6cd] line-clamp-3 leading-relaxed">{sig.summary}</p>
                        </div>
                        <div className="flex justify-between items-center text-[9px] font-mono mt-3 pt-2 border-t border-[#45464d]/20">
                          <span className="text-[#4edea3] font-bold uppercase">{sig.trust}</span>
                          <span className="text-[#ffb4ab] font-bold">Score: {Math.round(sig.relevance_score ?? 0)}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Split Workspace Preview Panel */}
            {selectedDossierSignal && (
              <div className="w-[450px] border-l border-[#45464d]/60 bg-[#010f1f]/95 backdrop-blur-md flex flex-col overflow-hidden shrink-0 font-mono transition-all duration-300">
                <div className="p-4 border-b border-[#45464d]/60 flex justify-between items-center shrink-0">
                  <h3 className="text-xs font-bold tracking-widest text-[#7bd0ff]">SIGNAL BRIEF PREVIEW</h3>
                  <button
                    onClick={() => setSelectedDossierSignal(null)}
                    className="material-symbols-outlined text-[#ffb4ab] hover:text-[#ffdad6]"
                  >
                    close
                  </button>
                </div>
                <div className="p-6 space-y-6 flex-grow overflow-y-auto">
                  <div className="space-y-2">
                    <span className="bg-[#93000a]/20 border border-[#ffb4ab]/30 text-[#ffb4ab] text-[9px] font-bold px-2 py-0.5 rounded">
                      CLASSIFIED TARGET
                    </span>
                    <h2 className="text-sm font-bold text-[#d4e4fa] leading-snug">
                      {selectedDossierSignal.is_breaking && (
                        <span className="inline-block bg-[#ff3b30]/20 text-[#ff453a] text-[8px] font-bold font-mono uppercase px-1.5 py-0.5 border border-[#ff453a]/30 rounded animate-pulse mr-1.5">
                          [BREAKING]
                        </span>
                      )}
                      {selectedDossierSignal.headline}
                    </h2>
                    <p className="text-[10px] text-[#c6c6cd]">
                      Ingestion Epoch: {new Date(selectedDossierSignal.timestamp).toLocaleString()}
                    </p>
                  </div>

                  <div className="border-t border-[#45464d]/40 pt-4 space-y-4">
                    <div>
                      <h4 className="text-[10px] text-[#7bd0ff] font-bold uppercase mb-1">Signal context</h4>
                      <p className="text-xs text-[#c6c6cd] leading-relaxed">
                        {selectedDossierSignal.summary}
                      </p>
                    </div>

                    <div className="grid grid-cols-2 gap-4 bg-[#122131]/40 p-4 border border-[#45464d]/40 text-xs">
                      <div>
                        <p className="text-[9px] text-[#c6c6cd] uppercase">Category</p>
                        <p className="text-[#bec6e0] font-bold mt-0.5">{selectedDossierSignal.category}</p>
                      </div>
                      <div>
                        <p className="text-[9px] text-[#c6c6cd] uppercase">Source Wire</p>
                        <p className="text-[#bec6e0] font-bold mt-0.5">{selectedDossierSignal.source}</p>
                      </div>
                      <div>
                        <p className="text-[9px] text-[#c6c6cd] uppercase">Trust Rating</p>
                        <p className="text-[#4edea3] font-bold mt-0.5">{selectedDossierSignal.trust}</p>
                      </div>
                      <div>
                        <p className="text-[9px] text-[#c6c6cd] uppercase">Relevance Index</p>
                        <p className="text-[#ffb4ab] font-bold mt-0.5">{Math.round(selectedDossierSignal.relevance_score ?? 0)}</p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Right Sidebar: Rapid Flash Alert */}
            <aside className="w-80 border-l border-[#45464d]/60 bg-[#010f1f] flex flex-col overflow-hidden shrink-0">
              <div className="p-4 border-b border-[#45464d]/60">
                <h3 className="text-xs font-bold font-mono tracking-widest text-[#7bd0ff]">RAPID FLASH ALERT</h3>
              </div>
              <div className="p-4 space-y-6 flex-grow overflow-y-auto">
                {/* Highlighted Alert Box */}
                <div className="bg-[#93000a]/20 border border-[#ffb4ab]/30 p-4 space-y-4 rounded">
                  <span className="inline-block bg-[#93000a] text-[#ffb4ab] text-[9px] font-bold font-mono uppercase px-2 py-0.5 border border-[#ffb4ab]/30 rounded">
                    CRITICAL FLAG
                  </span>
                  <h4 className="text-xs font-bold text-[#ffb4ab] leading-snug">
                    CRITICAL: DRONE ACTIVITY DETECTED NEAR BORDER CORRIDOR
                  </h4>
                  <div className="space-y-1 font-mono text-[10px] text-[#c6c6cd]">
                    <p>LAT/LONG: 33.6844° N, 73.0479° E</p>
                    <p>ELAPSED: T-MINUS 04:12</p>
                  </div>
                  <p className="text-[11px] text-[#c6c6cd]/90 leading-relaxed">
                    Unidentified UAV formation identified moving South-Southwest. Ground sensors suggest advanced signature spoofing algorithms.
                  </p>
                  <button className="w-full py-2 bg-[#93000a] hover:bg-[#93000a]/80 text-[#ffb4ab] text-xs font-mono font-bold border border-[#ffb4ab]/40 tracking-wider uppercase rounded transition-colors">
                    Engage Monitoring
                  </button>
                </div>

                {/* Core Metrics Box */}
                <div className="bg-[#122131] border border-[#45464d] p-4 space-y-3 font-mono text-xs">
                  <h4 className="text-[10px] font-bold text-[#7bd0ff] uppercase tracking-wider mb-2">Stability indices</h4>
                  <div className="flex justify-between items-center">
                    <span className="text-[#c6c6cd]">STABILITY INDEX</span>
                    <span className="text-[#4edea3] font-bold text-sm">{(selectedCountry.stabilityIndex).toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-[#c6c6cd]">RISK PROBABILITY</span>
                    <span className="text-[#ffb4ab] font-bold text-sm">{(selectedCountry.riskProbability).toFixed(2)}%</span>
                  </div>
                </div>
              </div>
            </aside>
          </div>
        </div>
      </div>

      {/* Ticker / Marquee Footer */}
      <footer className="h-8 bg-[#051424] border-t border-[#45464d]/60 flex items-center px-4 justify-between text-[10px] text-[#c6c6cd] font-mono shrink-0 select-none z-20">
        <div className="flex-grow flex items-center gap-4 overflow-hidden">
          <span className="text-[#ffb4ab] font-bold whitespace-nowrap">CLASSIFIED // TOP SECRET // NODE 9-B</span>
          <div className="h-3 w-px bg-[#45464d]/60" />
          {/* Marquee alerts text */}
          <div className="marquee-container flex-grow text-xs text-[#7bd0ff]">
            <div className="marquee-content">
              [ALERT] SYSTEM_READY_FOR_DATA_SYNTHESIS -- [URGENT] SYSTEM PROTOCOLS UPDATED FOR NODE 9-B -- [NOTICE] DATA STREAMING SECURE FROM MESH AGENTS -- [ALERT] STRATCOM RADAR ARRAYS TRACKING REGIONAL BORDER CHANNELS
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 border-l border-[#45464d]/60 pl-4">
          <span className="w-2 h-2 rounded-full bg-[#4edea3] pulse-soft" />
          <span className="text-[#4edea3] font-bold">SYSTEM STATUS: NOMINAL</span>
        </div>
      </footer>
    </div>
  );
}

export default App;
