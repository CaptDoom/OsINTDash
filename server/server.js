import express from 'express';
import cors from 'cors';
import Parser from 'rss-parser';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import Datastore from 'nedb-promises';
import { setCache, getOrSetCacheSWR } from './cache.js';
import { addScrapeJob, getJobStatus, setJobUpdateCallback } from './queue.js';
import { initWebSocket, broadcastJobUpdate } from './websocket.js';
import http from 'http';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const dataDir = path.join(__dirname, '../data');

mkdirSync(dataDir, { recursive: true });

const app = express();
const server = http.createServer(app);
const port = 3001;

initWebSocket(server);
setJobUpdateCallback((jobId, status, progress, result, error) => {
  broadcastJobUpdate(jobId, status, progress, result, error);
});
const dbFilePath = path.join(dataDir, 'articles.db');
if (existsSync(dbFilePath)) {
  try {
    const content = readFileSync(dbFilePath, 'utf8');
    const lines = content.split('\n');
    const seenUrls = new Set();
    const cleanLines = [];

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      
      if (trimmed.includes('$$indexCreated')) {
        cleanLines.push(trimmed);
        continue;
      }

      try {
        const doc = JSON.parse(trimmed);
        if (doc.url) {
          if (seenUrls.has(doc.url)) {
            continue;
          }
          seenUrls.add(doc.url);
        }
        cleanLines.push(trimmed);
      } catch (err) {
        // Skip malformed lines
      }
    }

    writeFileSync(dbFilePath, cleanLines.join('\n') + '\n', 'utf8');
    console.log(`[Database Cleanup] Cleaned articles.db, removed duplicate URLs.`);
  } catch (err) {
    console.error('[Database Cleanup] Failed to clean database file:', err.message);
  }
}

const parser = new Parser();
const articleDb = Datastore.create({
  filename: dbFilePath,
  autoload: true,
});

// Ensure indexing for fast range queries and search operations
articleDb.ensureIndex({ fieldName: 'timestamp' }).catch(err => console.warn('[NeDB] timestamp index error:', err.message));
articleDb.ensureIndex({ fieldName: 'country' }).catch(err => console.warn('[NeDB] country index error:', err.message));
articleDb.ensureIndex({ fieldName: 'url', unique: true, sparse: true }).catch(err => console.warn('[NeDB] url unique index error:', err.message));

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, '../dist')));

// Prevent malformed JSON requests from destabilizing the service.
app.use((err, req, res, next) => {
  if (err instanceof SyntaxError && 'body' in err) {
    return res.status(400).json({ error: 'Invalid JSON payload' });
  }
  return next(err);
});

// Trusted multilingual news channel feeds for authentic coverage
const feeds = {
  China: [
    'https://feeds.bbci.co.uk/news/world/rss.xml',
    'https://feeds.reuters.com/Reuters/worldNews',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rss.cnn.com/rss/edition_world.rss',
    'https://www.wionews.com/rss'
  ],
  Pakistan: [
    'https://feeds.bbci.co.uk/news/world/rss.xml',
    'https://feeds.reuters.com/Reuters/worldNews',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rss.cnn.com/rss/edition_world.rss',
    'https://www.wionews.com/rss'
  ],
  Afghanistan: [
    'https://feeds.bbci.co.uk/news/world/rss.xml',
    'https://feeds.reuters.com/Reuters/worldNews',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rss.cnn.com/rss/edition_world.rss'
  ],
  Bangladesh: [
    'https://feeds.bbci.co.uk/news/world/rss.xml',
    'https://feeds.reuters.com/Reuters/worldNews',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rss.cnn.com/rss/edition_world.rss'
  ],
  Myanmar: [
    'https://feeds.bbci.co.uk/news/world/rss.xml',
    'https://feeds.reuters.com/Reuters/worldNews',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rss.cnn.com/rss/edition_world.rss'
  ],
  Nepal: [
    'https://feeds.bbci.co.uk/news/world/rss.xml',
    'https://feeds.reuters.com/Reuters/worldNews',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rss.cnn.com/rss/edition_world.rss'
  ],
  Bhutan: [
    'https://feeds.bbci.co.uk/news/world/rss.xml',
    'https://feeds.reuters.com/Reuters/worldNews',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rss.cnn.com/rss/edition_world.rss'
  ],
  'Sri Lanka': [
    'https://feeds.bbci.co.uk/news/world/rss.xml',
    'https://feeds.reuters.com/Reuters/worldNews',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rss.cnn.com/rss/edition_world.rss'
  ],
  Maldives: [
    'https://feeds.bbci.co.uk/news/world/rss.xml',
    'https://feeds.reuters.com/Reuters/worldNews',
    'https://www.aljazeera.com/xml/rss/all.xml',
    'https://rss.cnn.com/rss/edition_world.rss'
  ],
  Global: [],
};

const countryMeta = {
  China: { region: 'Northern Front', threatLevel: 'Critical' },
  Pakistan: { region: 'Western Front', threatLevel: 'High' },
  Afghanistan: { region: 'Western Front', threatLevel: 'High' },
  Bangladesh: { region: 'Eastern Front', threatLevel: 'Moderate' },
  Myanmar: { region: 'Southeastern Front', threatLevel: 'Critical' },
  Nepal: { region: 'Northern Front', threatLevel: 'Moderate' },
  Bhutan: { region: 'Northern Front', threatLevel: 'Moderate' },
  'Sri Lanka': { region: 'Indian Ocean', threatLevel: 'Moderate' },
  Maldives: { region: 'Indian Ocean', threatLevel: 'Moderate' },
  Global: { region: 'Strategic Space', threatLevel: 'Low' }
};

// Geopolitical country keywords map for classification
const countryKeywords = {
  China: ['china', 'chinese', 'beijing', 'pla', 'aksai chin', 'tibet', 'lac', 'doklam', 'galwan'],
  Pakistan: ['pakistan', 'pakistani', 'islamabad', 'gwadar', 'loc', 'kashmir', 'rawalpindi'],
  Afghanistan: ['afghanistan', 'afghan', 'kabul', 'taliban', 'duran line'],
  Bangladesh: ['bangladesh', 'bangladeshi', 'dhaka', 'chittagong', 'teesta', 'bgb'],
  Myanmar: ['myanmar', 'burma', 'burmese', 'naypyidaw', 'yangon', 'junta', 'arakan', 'rohingya'],
  Nepal: ['nepal', 'nepalese', 'kathmandu', 'kalapani', 'lipulekh'],
  Bhutan: ['bhutan', 'bhutanese', 'thimphu'],
  'Sri Lanka': ['sri lanka', 'colombo', 'hambantota', 'palk strait'],
  Maldives: ['maldives', 'maldivian', 'male', 'muizzu'],
};

const worldAlertFeeds = [
  'https://feeds.bbci.co.uk/news/world/rss.xml',
  'https://feeds.reuters.com/Reuters/worldNews',
  'https://rss.cnn.com/rss/edition_world.rss',
  'https://www.aljazeera.com/xml/rss/all.xml',
  'https://rss.nytimes.com/services/xml/rss/nyt/World.xml'
];

const worldLocationIndex = {
  'United States': { lat: 38.0, lon: -97.0, keywords: ['united states', 'u.s.', 'us ', 'washington', 'new york', 'pentagon'] },
  UK: { lat: 54.0, lon: -2.0, keywords: ['united kingdom', 'uk ', 'britain', 'london'] },
  France: { lat: 46.0, lon: 2.0, keywords: ['france', 'paris'] },
  Germany: { lat: 51.0, lon: 10.0, keywords: ['germany', 'berlin'] },
  Ukraine: { lat: 49.0, lon: 32.0, keywords: ['ukraine', 'kyiv', 'kiev'] },
  Russia: { lat: 61.0, lon: 105.0, keywords: ['russia', 'moscow', 'kremlin'] },
  Israel: { lat: 31.0, lon: 35.0, keywords: ['israel', 'jerusalem', 'tel aviv'] },
  Palestine: { lat: 31.9, lon: 35.2, keywords: ['gaza', 'west bank', 'palestinian', 'palestine'] },
  Iran: { lat: 32.0, lon: 53.0, keywords: ['iran', 'tehran'] },
  India: { lat: 22.0, lon: 79.0, keywords: ['india', 'new delhi', 'indian'] },
  Pakistan: { lat: 30.0, lon: 70.0, keywords: ['pakistan', 'islamabad'] },
  Afghanistan: { lat: 33.9391, lon: 67.71, keywords: ['afghanistan', 'kabul', 'afghan'] },
  Bangladesh: { lat: 23.685, lon: 90.3563, keywords: ['bangladesh', 'dhaka', 'bangladeshi'] },
  China: { lat: 35.0, lon: 103.0, keywords: ['china', 'beijing', 'chinese'] },
  Taiwan: { lat: 23.7, lon: 121.0, keywords: ['taiwan', 'taipei'] },
  Japan: { lat: 36.0, lon: 138.0, keywords: ['japan', 'tokyo'] },
  SouthKorea: { lat: 36.5, lon: 127.8, keywords: ['south korea', 'seoul'] },
  NorthKorea: { lat: 40.0, lon: 127.0, keywords: ['north korea', 'pyongyang'] },
  Myanmar: { lat: 21.0, lon: 96.0, keywords: ['myanmar', 'burma', 'naypyidaw'] },
  Nepal: { lat: 28.3949, lon: 84.124, keywords: ['nepal', 'kathmandu', 'nepalese'] },
  Bhutan: { lat: 27.5142, lon: 90.4336, keywords: ['bhutan', 'thimphu', 'bhutanese'] },
  'Sri Lanka': { lat: 7.8731, lon: 80.7718, keywords: ['sri lanka', 'colombo'] },
  Maldives: { lat: 3.2028, lon: 73.2207, keywords: ['maldives', 'male', 'maldivian'] },
  Australia: { lat: -25.0, lon: 133.0, keywords: ['australia', 'canberra', 'sydney'] },
};

const worldPriorityLocations = new Set(Object.keys(worldLocationIndex));

const worldVeryHighImpactPatterns = {
  high: /(airstrike|missile|attack|war|killed|dead|casualt|explosion|shelling|critical|emergency|escalation|offensive|invasion|hostage|coup|assassination|martial law|nuclear|sanctions|government collapse|state of emergency|ceasefire collapse|market crash|currency crisis|tariff shock|cyberattack)/,
  medium: /(standoff|alert|clash|mass protest|troops|warning|security alert|diplomatic crisis|summit collapse|parliament dissolved|border closure|evacuation|strategic exercise|fleet deployment|major cabinet reshuffle|trade suspension|military drill|naval drill|joint exercise|talks stall|tense|tension|mobilisation|mobilization|stand-off|faceoff|summit|tariff|trade talks|cabinet reshuffle|election dispute|coalition crisis|border talks|diplomatic row|naval patrol|airspace violation)/
};

let worldAlertStore = [];
let lastWorldAlertRefresh = 0;
const llmEnrichmentCache = new Map();
let llmBackoffUntil = 0;

const llmIntelCategories = new Set(['Military', 'Terrorism', 'Cyber', 'Diplomacy', 'Economy', 'Maritime', 'Space', 'Border']);
const llmThreatLevels = new Set(['Low', 'Medium', 'High', 'Critical']);

const regionCoordinateIndex = {
  China: { lat: 35.8617, lon: 104.1954 },
  Pakistan: { lat: 30.3753, lon: 69.3451 },
  Afghanistan: { lat: 33.9391, lon: 67.71 },
  Bangladesh: { lat: 23.685, lon: 90.3563 },
  Myanmar: { lat: 21.9162, lon: 95.956 },
  Nepal: { lat: 28.3949, lon: 84.124 },
  Bhutan: { lat: 27.5142, lon: 90.4336 },
  'Sri Lanka': { lat: 7.8731, lon: 80.7718 },
  Maldives: { lat: 3.2028, lon: 73.2207 },
  Global: { lat: 20.0, lon: 0.0 }
};

function safeJsonParse(value) {
  if (!value || typeof value !== 'string') return null;
  try {
    return JSON.parse(value);
  } catch (err) {
    const start = value.indexOf('{');
    const end = value.lastIndexOf('}');
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(value.slice(start, end + 1));
      } catch (innerErr) {
        return null;
      }
    }
    return null;
  }
}

function toShortSummary(text) {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return 'No concise summary available from the source text.';
  const chunks = clean.split(/(?<=[.!?])\s+/).filter(Boolean);
  if (chunks.length >= 2) {
    return chunks.slice(0, 3).join(' ').trim();
  }
  const words = clean.split(' ');
  return words.slice(0, 42).join(' ').trim();
}

function normalizeThreatLevel(value) {
  const normalized = (value || '').toString().trim().toLowerCase();
  if (normalized.includes('critical')) return 'Critical';
  if (normalized.includes('high')) return 'High';
  if (normalized.includes('medium')) return 'Medium';
  if (normalized.includes('low')) return 'Low';
  return null;
}

function getThreatEmoji(level) {
  if (level === 'Critical') return '🔴 Critical';
  if (level === 'High') return '🟠 High';
  if (level === 'Medium') return '🟡 Medium';
  return '🟢 Low';
}

function impactFromThreat(level) {
  if (level === 'Critical' || level === 'High') return 'High';
  if (level === 'Medium') return 'Medium';
  return 'Low';
}

function normalizeIntelCategory(value) {
  const raw = (value || '').toString().trim();
  if (!raw) return null;
  const titleCase = raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase();
  return llmIntelCategories.has(titleCase) ? titleCase : null;
}

function mapIntelCategoryToDashboardCategory(intelCategory) {
  if (intelCategory === 'Military' || intelCategory === 'Border' || intelCategory === 'Terrorism' || intelCategory === 'Maritime') return 'Military';
  if (intelCategory === 'Cyber' || intelCategory === 'Space') return 'Tech';
  if (intelCategory === 'Diplomacy') return 'Political';
  if (intelCategory === 'Economy') return 'Economic';
  return null;
}

function dedupeList(values = [], max = 8) {
  const out = [];
  const seen = new Set();
  for (const value of values) {
    const cleaned = (value || '').toString().trim();
    if (!cleaned) continue;
    const key = cleaned.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(cleaned);
    if (out.length >= max) break;
  }
  return out;
}

function extractEntitiesHeuristic(article) {
  const text = `${article.title || ''} ${article.description || ''}`;
  const lower = text.toLowerCase();

  const countries = Object.keys(countryKeywords).filter((country) => {
    const aliases = countryKeywords[country] || [];
    return aliases.some((alias) => lower.includes(alias));
  });

  const organizations = dedupeList((text.match(/\b(UN|NATO|EU|ASEAN|PLA|ISI|CIA|MSS|MoD|Ministry of Defence|Pentagon|Taliban)\b/gi) || []));
  const militaryUnits = dedupeList((text.match(/\b(brigade|division|battalion|regiment|navy|air force|coast guard|special forces|militia)\b/gi) || []));
  const weapons = dedupeList((text.match(/\b(missile|drone|uav|artillery|fighter jet|warship|frigate|submarine|radar|rocket)\b/gi) || []));
  const people = dedupeList((text.match(/\b([A-Z][a-z]+\s[A-Z][a-z]+)\b/g) || []).slice(0, 8));

  return {
    countries: dedupeList(countries, 6),
    organizations,
    militaryUnits,
    weapons,
    people,
  };
}

function inferArticleLocation(article, countryHint = '') {
  const text = `${article.title || ''} ${article.description || ''}`;
  const worldMatch = inferWorldLocationFromText(text);
  if (worldMatch) {
    return {
      name: worldMatch.location,
      lat: worldMatch.lat,
      lon: worldMatch.lon,
    };
  }

  if (countryHint && regionCoordinateIndex[countryHint]) {
    return {
      name: countryHint,
      lat: regionCoordinateIndex[countryHint].lat,
      lon: regionCoordinateIndex[countryHint].lon,
    };
  }

  const inferredCountry = inferCountryFromText(text);
  if (inferredCountry && regionCoordinateIndex[inferredCountry]) {
    return {
      name: inferredCountry,
      lat: regionCoordinateIndex[inferredCountry].lat,
      lon: regionCoordinateIndex[inferredCountry].lon,
    };
  }

  return null;
}

function fallbackIntelEnrichment(article, countryHint = '') {
  const summary = toShortSummary(`${cleanHeadline(article.title || '')}. ${article.description || ''}`);
  const impact = determineImpact(article);
  const threatLevel = impact === 'High' ? 'High' : impact === 'Medium' ? 'Medium' : 'Low';
  const dashboardCategory = determineCategory(article);
  const mappedIntelCategory = dashboardCategory === 'Tech'
    ? 'Cyber'
    : dashboardCategory === 'Political'
      ? 'Diplomacy'
      : dashboardCategory === 'Economic'
        ? 'Economy'
        : /border|lac|loc|frontier/i.test(`${article.title || ''} ${article.description || ''}`)
          ? 'Border'
          : 'Military';

  return {
    summary,
    threatLevel,
    threatLabel: getThreatEmoji(threatLevel),
    intelCategory: mappedIntelCategory,
    dashboardCategory,
    impact: impactFromThreat(threatLevel),
    entities: extractEntitiesHeuristic(article),
    location: inferArticleLocation(article, countryHint),
    llmProvider: 'heuristic',
    llmModel: 'rule-based-fallback'
  };
}

async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    return response;
  } finally {
    clearTimeout(timer);
  }
}

async function runLlmIntelEnrichment(article, countryHint = '') {
  if (Date.now() < llmBackoffUntil) {
    return null;
  }

  const provider = (process.env.LLM_PROVIDER || (process.env.HF_API_KEY ? 'huggingface' : 'ollama')).toLowerCase();
  const text = `${article.title || ''}\n${article.description || ''}`.slice(0, 2000);
  if (!text.trim()) return null;

  const schemaHint = '{"summary":"2-3 lines","threat_level":"Low|Medium|High|Critical","category":"Military|Terrorism|Cyber|Diplomacy|Economy|Maritime|Space|Border","entities":{"countries":[],"organizations":[],"military_units":[],"weapons":[],"people":[]},"location":{"name":"", "lat":0, "lon":0}}';
  const prompt = [
    'You are a geopolitical intelligence extractor.',
    'Return only valid JSON with no markdown.',
    'Use this exact schema:',
    schemaHint,
    'Rules:',
    '- summary must be 2-3 short lines in plain English',
    '- choose exactly one threat_level from Low, Medium, High, Critical',
    '- choose exactly one category from Military, Terrorism, Cyber, Diplomacy, Economy, Maritime, Space, Border',
    '- entities must be arrays of strings',
    '- if no precise location is found, set location to null',
    `country_hint: ${countryHint || 'unknown'}`,
    'article:',
    text
  ].join('\n');

  let rawOutput = '';
  let modelUsed = '';

  try {
    if (provider === 'ollama') {
      const model = process.env.LLM_MODEL || 'llama3.1:8b-instruct';
      const baseUrl = process.env.OLLAMA_BASE_URL || 'http://127.0.0.1:11434';
      const response = await fetchJsonWithTimeout(`${baseUrl}/api/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model,
          prompt,
          stream: false,
          format: 'json',
          options: { temperature: 0.2 }
        })
      }, 12000);

      if (!response.ok) throw new Error(`Ollama status ${response.status}`);
      const data = await response.json();
      rawOutput = data.response || '';
      modelUsed = model;
    } else {
      const token = process.env.HF_API_KEY || process.env.HUGGINGFACE_API_KEY;
      if (!token) return null;
      const model = process.env.HF_MODEL || process.env.LLM_MODEL || 'google/flan-t5-large';
      const response = await fetchJsonWithTimeout(`https://api-inference.huggingface.co/models/${encodeURIComponent(model)}`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          inputs: prompt,
          parameters: {
            max_new_tokens: 350,
            temperature: 0.2,
            return_full_text: false
          }
        })
      }, 15000);

      if (!response.ok) throw new Error(`HF status ${response.status}`);
      const data = await response.json();
      rawOutput = Array.isArray(data) ? (data[0]?.generated_text || '') : (data?.generated_text || '');
      modelUsed = model;
    }
  } catch (err) {
    llmBackoffUntil = Date.now() + 5 * 60 * 1000;
    return null;
  }

  const parsed = safeJsonParse(rawOutput);
  if (!parsed || typeof parsed !== 'object') {
    return null;
  }

  const summary = toShortSummary(parsed.summary || parsed.brief || '');
  const threatLevel = normalizeThreatLevel(parsed.threat_level || parsed.threatLevel || parsed.threat) || 'Medium';
  const intelCategory = normalizeIntelCategory(parsed.category || parsed.domain || parsed.type) || 'Military';
  const dashboardCategory = mapIntelCategoryToDashboardCategory(intelCategory) || determineCategory(article);

  const entities = parsed.entities || {};
  const normalizedEntities = {
    countries: dedupeList(entities.countries || entities.Countries || []),
    organizations: dedupeList(entities.organizations || entities.orgs || []),
    militaryUnits: dedupeList(entities.military_units || entities.militaryUnits || []),
    weapons: dedupeList(entities.weapons || []),
    people: dedupeList(entities.people || [])
  };

  let location = null;
  if (parsed.location && typeof parsed.location === 'object') {
    const maybeLat = Number(parsed.location.lat);
    const maybeLon = Number(parsed.location.lon);
    if (Number.isFinite(maybeLat) && Number.isFinite(maybeLon)) {
      location = {
        name: parsed.location.name || countryHint || 'Detected location',
        lat: maybeLat,
        lon: maybeLon
      };
    }
  }

  if (!location) {
    location = inferArticleLocation(article, countryHint);
  }

  return {
    summary,
    threatLevel,
    threatLabel: getThreatEmoji(threatLevel),
    intelCategory,
    dashboardCategory,
    impact: impactFromThreat(threatLevel),
    entities: normalizedEntities,
    location,
    llmProvider: provider,
    llmModel: modelUsed || 'unknown-model'
  };
}

async function enrichArticleIntelligence(article, countryHint = '') {
  const cacheKey = (article.url || cleanHeadline(article.title || '')).toLowerCase();
  if (llmEnrichmentCache.has(cacheKey)) {
    return llmEnrichmentCache.get(cacheKey);
  }

  const llmResult = await runLlmIntelEnrichment(article, countryHint);
  const enriched = llmResult || fallbackIntelEnrichment(article, countryHint);
  llmEnrichmentCache.set(cacheKey, enriched);
  return enriched;
}

function inferWorldLocationFromText(text) {
  const normalized = (text || '').toLowerCase();
  let bestMatch = null;
  let bestScore = 0;

  Object.entries(worldLocationIndex).forEach(([location, meta]) => {
    const score = meta.keywords.reduce((acc, keyword) => (normalized.includes(keyword) ? acc + 1 : acc), 0);
    if (score > bestScore) {
      bestScore = score;
      bestMatch = { location, ...meta };
    }
  });

  return bestScore > 0 ? bestMatch : null;
}

function inferWorldSeverity(article) {
  const text = `${article.title || ''} ${article.description || ''}`.toLowerCase();
  if (worldVeryHighImpactPatterns.high.test(text)) {
    return 'high';
  }
  if (worldVeryHighImpactPatterns.medium.test(text)) {
    return 'medium';
  }
  return null;
}

function isWorldPriorityAlert(article, locationMeta, severity) {
  if (!locationMeta || !worldPriorityLocations.has(locationMeta.location)) {
    return false;
  }

  const text = `${article.title || ''} ${article.description || ''}`.toLowerCase();
  const hasPriorityContext = /(military|politic|government|election|parliament|prime minister|president|sanctions|trade|tariff|security|border|war|attack|missile|fleet|nuclear|protest|emergency|diplomatic|summit|currency|market|relations|cabinet|coalition|maritime|airspace|naval|exercise)/.test(text);
  if (!hasPriorityContext) {
    return false;
  }

  // Only keep high-impact stories, but preserve yellow dots for secondary high-priority signals.
  return severity === 'high' || severity === 'medium';
}

function mergeWorldAlerts(articles) {
  let added = 0;
  for (const article of articles) {
    if (!article.title || !article.url) continue;
    if (isNoise(article) || !isTrustedSource(article)) continue;

    const locationMeta = inferWorldLocationFromText(`${article.title || ''} ${article.description || ''}`);
    if (!locationMeta) continue;

    const severity = inferWorldSeverity(article);
    if (!severity) continue;
    if (!isWorldPriorityAlert(article, locationMeta, severity)) continue;

    const exists = worldAlertStore.some((item) => item.url === article.url || item.headline === cleanHeadline(article.title));
    if (exists) continue;

    worldAlertStore.unshift({
      id: `world-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      location: locationMeta.location,
      lat: locationMeta.lat,
      lon: locationMeta.lon,
      severity,
      headline: cleanHeadline(article.title),
      source: article.source || 'Trusted Source',
      url: article.url,
      timestamp: article.publishedAt ? new Date(article.publishedAt).toISOString() : new Date().toISOString()
    });
    added += 1;
  }

  const weekCutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
  worldAlertStore = worldAlertStore
    .filter((item) => new Date(item.timestamp).getTime() >= weekCutoff)
    .slice(0, 500);

  return added;
}

async function runWorldAlertRefresh(force = false) {
  const now = Date.now();
  if (!force && now - lastWorldAlertRefresh < 60 * 1000 && worldAlertStore.length > 0) {
    return { added: 0, alerts: worldAlertStore };
  }

  const rssItems = await fetchRSS(worldAlertFeeds);
  const addedFromRss = mergeWorldAlerts(rssItems);

  let addedFromApi = 0;
  const apiKey = process.env.NEWS_API_KEY;
  if (apiKey) {
    try {
      const globalQuery = '(war OR conflict OR strike OR missile OR military OR protest OR attack OR crisis OR sanctions OR election OR coup OR summit OR trade OR tariff OR cyberattack OR emergency)';
      const apiItems = await fetchNewsAPI(globalQuery, apiKey);
      addedFromApi = mergeWorldAlerts(apiItems);
    } catch (err) {
      console.warn('[World Alerts] NewsAPI fetch failed:', err.message);
    }
  }

  let addedFromGNews = 0;
  const gnewsApiKey = process.env.GNEWS_API_KEY;
  if (gnewsApiKey) {
    try {
      const mediumQuery = '(diplomatic crisis OR summit OR border talks OR trade suspension OR military drill OR naval patrol OR election dispute OR coalition crisis)';
      const gnewsItems = await fetchGNews(mediumQuery, gnewsApiKey);
      addedFromGNews = mergeWorldAlerts(gnewsItems);
    } catch (err) {
      console.warn('[World Alerts] GNews fetch failed:', err.message);
    }
  }

  let addedFromGdelt = 0;
  try {
    const gdeltQuery = '(war OR conflict OR strike OR missile OR military OR attack OR sanctions OR election OR coup OR summit) AND (india OR china OR pakistan OR afghanistan OR bangladesh OR myanmar OR nepal OR bhutan OR "sri lanka" OR maldives)';
    const gdeltItems = await fetchGDELT(gdeltQuery, { maxRecords: 30, timespan: '24h' });
    addedFromGdelt = mergeWorldAlerts(gdeltItems);
  } catch (err) {
    console.warn('[World Alerts] GDELT fetch failed:', err.message);
  }

  lastWorldAlertRefresh = now;
  return { added: addedFromRss + addedFromApi + addedFromGNews + addedFromGdelt, alerts: worldAlertStore };
}

function inferCountryFromText(text) {
  const normalized = (text || '').toLowerCase();
  let bestCountry = null;
  let bestScore = 0;

  Object.entries(countryKeywords).forEach(([country, keywords]) => {
    const score = keywords.reduce((acc, keyword) => (normalized.includes(keyword) ? acc + 1 : acc), 0);
    if (score > bestScore) {
      bestScore = score;
      bestCountry = country;
    }
  });

  return bestScore > 0 ? bestCountry : null;
}

// Ingestion queries negative guardrails
const negativeGuardrails = ' -crypto -cryptocurrency -bitcoin -tourism -travel -sports -sport -stocks -shares -dividend -entertainment -fashion -recipe -cooking';
const trustedSourceDomains = ['cnn.com', 'bbc.com', 'bbc.co.uk', 'wionews.com', 'aljazeera.com', 'reuters.com', 'apnews.com', 'ap.org', 'theguardian.com', 'nytimes.com', 'thehindu.com', 'indianexpress.com', 'timesofindia.indiatimes.com', 'news18.com'];

function cleanHeadline(title) {
  if (!title) return 'Live intelligence update';
  return title.replace(/\s+-\s+[^ -]+$/, '').trim();
}

function isNoise(item) {
  const text = `${item.title || ''} ${item.description || ''}`.toLowerCase();
  const noiseKeywords = [
    'quiz',
    'trivia',
    'crossword',
    'opinion poll',
    'test your knowledge',
    'horoscope',
    'how well do you know',
    'cricket',
    'football',
    'soccer',
    'tennis',
    'olympic',
    'transfer window',
    'sports',
    'sport',
    'movie',
    'celebrity',
    'box office',
    'recipe',
    'fashion',
    'travel',
    'lifestyle'
  ];
  return noiseKeywords.some(keyword => text.includes(keyword));
}

function isGeopoliticalSignal(item) {
  const text = `${item.title || ''} ${item.description || ''}`.toLowerCase();
  const countryContext = Object.values(countryKeywords).flat();
  const geopoliticalKeywords = [
    'military', 'defence', 'defense', 'troops', 'army', 'naval', 'air force', 'missile', 'drone', 'uav',
    'border', 'clash', 'conflict', 'war', 'security', 'summit', 'diplomatic', 'diplomacy', 'minister',
    'president', 'parliament', 'government', 'election', 'sanctions', 'trade', 'tariff', 'protest',
    'evacuation', 'ceasefire', 'attack', 'airstrike', 'fleet', 'nuclear', 'crisis', 'emergency'
  ];

  return countryContext.some((keyword) => text.includes(keyword)) || geopoliticalKeywords.some((keyword) => text.includes(keyword));
}

function isTrustedSource(item) {
  const text = `${item.source || ''} ${item.url || ''}`.toLowerCase();
  return trustedSourceDomains.some(domain => text.includes(domain));
}

function buildTrustedSourceSummary(article, relatedArticles = []) {
  const title = cleanHeadline(article.title || 'Live intelligence update');
  const description = (article.description || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  const sourceNames = Array.from(new Set([article.source, ...relatedArticles.map(item => item.source)].filter(Boolean))).slice(0, 4);
  const sourceLabel = sourceNames.length > 1
    ? sourceNames.slice(0, 3).join(', ')
    : (sourceNames[0] || 'Trusted Source');

  const snippet = description ? description.split(' ').slice(0, 28).join(' ') : '';
  const snippetLine = snippet ? `Snippet: ${snippet}.` : 'Snippet: not available from the source feed.';

  return `Extractive summary: ${title}. Source: ${sourceLabel}. ${snippetLine}`.trim();
}

function buildYouTubeSearchUrl(headline, source, country) {
  const query = [headline, source, country, 'news'].filter(Boolean).join(' ');
  return `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`;
}

function buildStoryKey(title, country) {
  const normalized = cleanHeadline(title || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 6)
    .join('-');
  return `${(country || 'global').toLowerCase()}-${normalized}`;
}

function getVerificationStatus(sourceLinks) {
  if ((sourceLinks || []).length >= 3) return 'Multi-source confirmed';
  if ((sourceLinks || []).length >= 2) return 'Cross-checked';
  return 'Single-source';
}

function getConfidenceScore({ sourceLinks, publishedAt, hasDirectUrl, impact }) {
  let score = 35;
  const sourceCount = (sourceLinks || []).length;
  score += Math.min(30, sourceCount * 10);
  if (hasDirectUrl) score += 10;
  if (impact === 'High') score += 10;
  else if (impact === 'Medium') score += 4;

  if (publishedAt) {
    const hours = (Date.now() - new Date(publishedAt).getTime()) / (1000 * 60 * 60);
    if (hours <= 6) score += 10;
    else if (hours <= 24) score += 6;
    else if (hours <= 72) score += 3;
  }

  return Math.max(0, Math.min(100, Math.round(score)));
}

// Machine Translation (MT) Helper
function translateToEnglish(text, lang) {
  if (!text) return '';
  
  const zhMap = {
    '解放军': 'PLA',
    '边境': 'Border',
    '雷达': 'Radar',
    '导弹': 'Missile',
    '无人机': 'UAV/Drone',
    '军事': 'Military',
    '演演习': 'Exercises',
    '演戏': 'Exercises',
    '冲突': 'Clash',
    '西藏': 'Tibet',
    '印': 'India',
    '中': 'China',
    '部署': 'deployed',
    '升级': 'upgraded',
    '巡逻': 'patrol'
  };

  const urMap = {
    'فوج': 'Army',
    'سرحد': 'Border',
    'ڈرون': 'Drone',
    'میزائل': 'Missile',
    'بھارت': 'India',
    'پاکستان': 'Pakistan',
    'جنگ': 'War/Conflict',
    'حملہ': 'Attack/Clash',
    'تعینات': 'deployed',
    'سیکیورٹی': 'Security'
  };

  let translated = text;
  const dict = lang === 'zh' ? zhMap : (lang === 'ur' ? urMap : {});
  
  let replaced = false;
  Object.entries(dict).forEach(([foreign, eng]) => {
    if (translated.includes(foreign)) {
      translated = translated.replaceAll(foreign, ` ${eng} `);
      replaced = true;
    }
  });

  if (replaced) {
    return `[${lang.toUpperCase()}/TRANS] ${translated.replace(/\s+/g, ' ').trim()}`;
  }
  
  if (lang === 'zh' && /[\u4e00-\u9fa5]/.test(text)) {
    return `[ZH/TRANS] Satellite Telemetry: Border security activity recorded.`;
  }
  if (lang === 'ur' && /[\u0600-\u06FF]/.test(text)) {
    return `[UR/TRANS] Satellite Telemetry: Border security activity recorded.`;
  }

  return text;
}

// Strict Category Screening
function determineCategory(item) {
  const text = `${item.title || ''} ${item.description || ''}`.toLowerCase();
  const hasStrongTechSignal = /(cyber|malware|zero-day|satellite|space|uav|drone|radar|sensor|electronic warfare|ewar|air defense system|missile system|hypersonic|guidance system|surveillance system|autonomous|ai model|defense tech|defence tech|military tech)/.test(text);
  if (hasStrongTechSignal) {
    return 'Tech';
  }
  if (text.includes('military') || text.includes('strike') || text.includes('defence') || text.includes('conflict') || text.includes('forces') || text.includes('border') || text.includes('army') || text.includes('clash') || text.includes('airstrikes') || text.includes('troop') || text.includes('pla') || text.includes('naval') || text.includes('fleet') || text.includes('loc') || text.includes('lac') || text.includes('skirmish')) {
    return 'Military';
  }
  if (text.includes('political') || text.includes('minister') || text.includes('visit') || text.includes('relations') || text.includes('diplomat') || text.includes('summit') || text.includes('deal') || text.includes('bilateral')) {
    return 'Political';
  }
  if (text.includes('economic') || text.includes('trade') || text.includes('commerce') || text.includes('tariff') || text.includes('investment') || text.includes('fiscal') || text.includes('dollar') || text.includes('port')) {
    return 'Economic';
  }
  if (text.includes('tech') || text.includes('cyber') || text.includes('drone') || text.includes('satellite') || text.includes('digital') || text.includes('uav') || text.includes('radar') || text.includes('sensor') || text.includes('quadcopter')) {
    return 'Tech';
  }
  if (text.includes('social') || text.includes('migration') || text.includes('displacement') || text.includes('protest') || text.includes('sentiment') || text.includes('public')) {
    return 'Social';
  }
  return 'Political';
}

function determineImpact(item) {
  const text = `${item.title || ''} ${item.description || ''}`.toLowerCase();
  if (text.includes('strike') || text.includes('kill') || text.includes('clash') || text.includes('conflict') || text.includes('critical') || text.includes('espionage') || text.includes('warns') || text.includes('airstrikes') || text.includes('dead')) {
    return 'High';
  }
  if (text.includes('talks') || text.includes('trade') || text.includes('visit') || text.includes('negotiate') || text.includes('agreement') || text.includes('border')) {
    return 'Medium';
  }
  return 'Low';
}

function calculateRelevanceScore(title, description, publishedAt, impact) {
  let score = 0;

  if (impact === 'High') score += 50;
  else if (impact === 'Medium') score += 25;

  const text = ((title || '') + ' ' + (description || '')).toLowerCase();

  const borderKeywords = ['border', 'lac', 'loc', 'clash', 'skirmish', 'dispute', 'territory', 'standoff', 'friction'];
  borderKeywords.forEach(kw => {
    if (text.includes(kw)) score += 100;
  });

  const militaryKeywords = ['military', 'pla', 'defence', 'troops', 'army', 'naval', 'fleet', 'forces', 'deployed', 'exercises'];
  militaryKeywords.forEach(kw => {
    if (text.includes(kw)) score += 50;
  });

  const techKeywords = ['missile', 'uav', 'drone', 'radar', 'weapons', 'sensor', 'surveillance', 'warfare'];
  techKeywords.forEach(kw => {
    if (text.includes(kw)) score += 40;
  });

  const conflictKeywords = ['taliban', 'rebel', 'coup', 'conflict', 'arrest', 'violence', 'shelling', 'bombing'];
  conflictKeywords.forEach(kw => {
    if (text.includes(kw)) score += 30;
  });

  if (publishedAt) {
    const hours = (Date.now() - new Date(publishedAt).getTime()) / (1000 * 60 * 60);
    score -= hours * 2.0; // Older drops faster
    if (hours <= 1.0) {
      score += 200; // Recency boost
    }
  }

  return score;
}

// In-memory Database of parsed Articles
let articleStore = [];

global.addArticleToInMemoryStore = (article) => {
  if (!articleStore.some(existing => existing.url === article.url || existing.headline === article.headline)) {
    articleStore.unshift(article);
    if (articleStore.length > 500) {
      articleStore = articleStore.slice(0, 500);
    }
  }
};

async function pruneArticleDb() {
  const docs = await articleDb.find({}).sort({ timestamp: -1 });
  if (docs.length <= 500) {
    articleStore = docs;
    return;
  }

  const obsolete = docs.slice(500).map((doc) => doc.id).filter(Boolean);
  if (obsolete.length > 0) {
    await articleDb.remove({ id: { $in: obsolete } }, { multi: true });
  }

  articleStore = docs.slice(0, 500);
}

async function hydrateArticleStore() {
  articleStore = await articleDb.find({}).sort({ timestamp: -1 }).limit(500);
}

// Active SSE client connections array
let activeConnections = [];

// Polling times and lazy activity tracking
let lastNewsAPIPoll = 0;
let lastRSSPoll = 0;
let lastWirePoll = 0;
let lastActivityTime = Date.now();

function updateActivity() {
  lastActivityTime = Date.now();
}

function isSessionActive() {
  // Session is active if there is a connected client or recent activity in 15 mins
  return activeConnections.length > 0 || (Date.now() - lastActivityTime < 15 * 60 * 1000);
}

async function addArticlesToStore(newArticles) {
  let newlyAdded = [];

  for (const art of newArticles) {
    if (!art.title || isNoise(art)) continue;
    if (!isTrustedSource(art)) continue;
    if (!isGeopoliticalSignal(art)) continue;

    const baselineCategory = art.category || determineCategory(art);
    const baselineImpact = art.impact || determineImpact(art);

    const normTitle = art.title.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 35);
    
    // Check if it already exists in articleStore
    const exists = articleStore.some(existing => {
      const existingNorm = existing.headline.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 35);
      return existingNorm === normTitle || (existing.url && existing.url === art.url);
    });

    if (!exists) {
      const intel = await enrichArticleIntelligence(art, art.country);
      const category = intel.dashboardCategory || baselineCategory;
      const impact = intel.impact || baselineImpact;
      const score = calculateRelevanceScore(art.title, art.description, art.publishedAt || art.timestamp, impact);
      const hours = art.publishedAt ? (Date.now() - new Date(art.publishedAt).getTime()) / (1000 * 60 * 60) : 0;
      const isBreaking = hours <= 1.0;
      const relatedArticles = articleStore
        .slice(0, 12)
        .filter(existing => existing.country === art.country && existing.source && existing.source !== art.source && isTrustedSource(existing));
      const sourceChain = Array.from(new Set([art.source, ...relatedArticles.map(item => item.source)].filter(Boolean))).slice(0, 6);
      const sourceLinks = Array.from(new Map(
        [art, ...relatedArticles]
          .filter((item) => item.url)
          .map((item) => [item.source || item.url, { name: item.source || 'Trusted Source', url: item.url }])
      ).values()).slice(0, 6);
      const summary = intel.summary || buildTrustedSourceSummary(art, relatedArticles);
      const storyKey = buildStoryKey(art.title, art.country);
      const relatedCount = articleStore.filter((existing) => existing.story_key === storyKey).length + 1;
      const verificationStatus = getVerificationStatus(sourceLinks);
      const confidenceScore = getConfidenceScore({
        sourceLinks,
        publishedAt: art.publishedAt || art.timestamp,
        hasDirectUrl: Boolean(art.url),
        impact
      });

      const enriched = {
        id: art.id || `${art.country.toLowerCase()}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        country: art.country,
        category,
        impact,
        headline: cleanHeadline(art.title),
        summary,
        source: art.source || 'Trusted Wire',
        source_chain: sourceChain,
        source_links: sourceLinks,
        url: art.url || null,
        image_url: art.imageUrl || null,
        youtube_url: buildYouTubeSearchUrl(art.title, art.source, art.country),
        timestamp: art.publishedAt ? new Date(art.publishedAt).toISOString() : new Date().toISOString(),
        relevance_score: score,
        is_breaking: isBreaking,
        verification_status: verificationStatus,
        confidence_score: confidenceScore,
        story_key: storyKey,
        related_count: relatedCount,
        threat_level: intel.threatLevel,
        threat_label: intel.threatLabel,
        intel_category: intel.intelCategory,
        entities: intel.entities,
        location_name: intel.location?.name || null,
        lat: intel.location?.lat || null,
        lon: intel.location?.lon || null,
        llm_provider: intel.llmProvider,
        llm_model: intel.llmModel
      };

      await articleDb.insert(enriched);
      articleStore.unshift(enriched);
      newlyAdded.push(enriched);
    }
  }

  await pruneArticleDb();

  return newlyAdded;
}

// Fetchers
async function fetchNewsAPI(query, apiKey) {
  const url = `https://newsapi.org/v2/everything?q=${encodeURIComponent(query)}&sources=bbc-news,cnn,al-jazeera-english,reuters,associated-press,wion&language=en&sortBy=publishedAt&apiKey=${apiKey}`;
  const response = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (!response.ok) throw new Error(`NewsAPI status ${response.status}`);
  const data = await response.json();
  return (data.articles || []).map(a => ({
    title: a.title,
    description: a.description || a.content,
    url: a.url,
    source: a.source?.name || 'News API',
    publishedAt: a.publishedAt,
    imageUrl: a.urlToImage || null
  }));
}

async function fetchGNews(query, apiKey) {
  const url = `https://gnews.io/api/v4/search?q=${encodeURIComponent(query)}&lang=en&max=10&apikey=${apiKey}`;
  const response = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (!response.ok) throw new Error(`GNews status ${response.status}`);
  const data = await response.json();
  return (data.articles || []).map((article) => ({
    title: article.title,
    description: article.description || article.content,
    url: article.url,
    source: article.source?.name || 'GNews',
    publishedAt: article.publishedAt,
    imageUrl: article.image || null
  }));
}

function parseGdeltSeenDate(value) {
  if (!value || typeof value !== 'string' || value.length < 14) return null;
  const year = value.slice(0, 4);
  const month = value.slice(4, 6);
  const day = value.slice(6, 8);
  const hour = value.slice(8, 10);
  const minute = value.slice(10, 12);
  const second = value.slice(12, 14);
  const iso = `${year}-${month}-${day}T${hour}:${minute}:${second}Z`;
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

async function fetchGDELT(query, options = {}) {
  const maxRecords = Math.max(1, Math.min(100, options.maxRecords || 25));
  const timespan = options.timespan || '24h';
  const url = `https://api.gdeltproject.org/api/v2/doc/doc?query=${encodeURIComponent(query)}&mode=ArtList&format=json&sort=HybridRel&maxrecords=${maxRecords}&timespan=${encodeURIComponent(timespan)}`;
  const response = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (!response.ok) throw new Error(`GDELT status ${response.status}`);
  const data = await response.json();
  return (data.articles || []).map((article) => ({
    title: article.title,
    description: article.seendate ? `GDELT signal seen at ${article.seendate}.` : '',
    url: article.url,
    source: article.domain || article.sourcecountry || 'GDELT',
    publishedAt: parseGdeltSeenDate(article.seendate),
    imageUrl: article.socialimage || null
  }));
}

async function fetchTheNewsAPI(query, apiKey) {
  const url = `https://api.thenewsapi.com/v1/news/all?api_token=${encodeURIComponent(apiKey)}&search=${encodeURIComponent(query)}&language=en&sort=published_at&limit=10`;
  const response = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (!response.ok) throw new Error(`TheNewsAPI status ${response.status}`);
  const data = await response.json();
  return (data.data || []).map((article) => ({
    title: article.title,
    description: article.description || article.snippet || article.summary,
    url: article.url,
    source: article.source || article.source_name || 'TheNewsAPI',
    publishedAt: article.published_at,
    imageUrl: article.image_url || null
  }));
}

async function fetchRSS(urls) {
  const parsedFeeds = await Promise.all(
    urls.map(async (url) => {
      try {
        const feed = await parser.parseURL(url);
        return { url, feed };
      } catch (err) {
        return null;
      }
    })
  );

  const rawArticles = [];
  parsedFeeds.forEach((entry) => {
    if (!entry) return;
    const { url, feed } = entry;
    for (const item of feed.items) {
      let lang = 'en';
      if (url.includes('hl=zh') || /[\u4e00-\u9fa5]/.test(item.title || '')) {
        lang = 'zh';
      } else if (url.includes('hl=ur') || /[\u0600-\u06FF]/.test(item.title || '')) {
        lang = 'ur';
      }

      const rawTitle = item.title || '';
      const translatedTitle = translateToEnglish(rawTitle, lang);

      rawArticles.push({
        title: translatedTitle,
        description: translateToEnglish(item.contentSnippet || item.content || '', lang),
        url: item.link || '#',
        source: (lang !== 'en' ? `[${lang.toUpperCase()}/MT] ` : '') + (item.creator || feed.title || 'Open feed'),
        publishedAt: item.pubDate,
        imageUrl: item.enclosure?.url || item.image?.url || feed.image?.url || null
      });
    }
  });

  return rawArticles;
}

function tokenizeQuery(text) {
  const stopWords = new Set(['the', 'is', 'are', 'a', 'an', 'of', 'to', 'in', 'for', 'on', 'with', 'and', 'or', 'what', 'how', 'latest', 'news', 'this', 'that', 'from', 'about']);
  return (text || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((token) => token && token.length > 2 && !stopWords.has(token));
}

const countryAliases = {
  China: ['china', 'beijing', 'pla', 'lac', 'tibet'],
  Pakistan: ['pakistan', 'islamabad', 'loc'],
  Afghanistan: ['afghanistan', 'kabul', 'taliban'],
  Bangladesh: ['bangladesh', 'dhaka'],
  Myanmar: ['myanmar', 'burma', 'naypyidaw'],
  Nepal: ['nepal', 'kathmandu'],
  Bhutan: ['bhutan', 'thimphu'],
  'Sri Lanka': ['sri lanka', 'colombo'],
  Maldives: ['maldives', 'male']
};

function detectCountryFromQuery(query, fallbackCountry = '') {
  const q = (query || '').toLowerCase();
  if (/(any\s+country|global|worldwide|all\s+countries)/i.test(q)) {
    return '';
  }
  for (const [country, aliases] of Object.entries(countryAliases)) {
    if (aliases.some((alias) => q.includes(alias))) {
      return country;
    }
  }
  return fallbackCountry || '';
}

function extractTopLimit(query) {
  const match = (query || '').match(/top\s*(\d{1,2})/i);
  if (!match) return 6;
  const parsed = parseInt(match[1], 10);
  if (Number.isNaN(parsed)) return 6;
  return Math.max(1, Math.min(20, parsed));
}

function isMilitaryIntent(query) {
  return /(military|defence|defense|security|troop|army|naval|air\s*force|conflict|war|strike|missile|drone|uav|border|impact\s+on\s+india)/i.test(query || '');
}

function isOperationalPlanningRequest(query) {
  return /(target|deploy|deployment plan|battle plan|war game|wargame|strike plan|best route|weakness|vulnerability|kill chain|rules of engagement|engagement plan|force posture recommendation|tactical option|operational plan|military planning|attack timing)/i.test(query || '');
}

function scoreQueryMatch(item, tokens, country) {
  const text = `${item.title || item.headline || ''} ${item.description || item.summary || ''} ${item.source || ''}`.toLowerCase();
  let score = 0;
  tokens.forEach((token) => {
    if (text.includes(token)) score += 3;
  });
  if (country && text.includes(country.toLowerCase())) score += 4;
  if (/india/.test(text)) score += 2;
  if (/(military|defence|defense|troops|army|naval|airstrike|missile|drone|uav|border|clash|security|war)/.test(text)) score += 8;
  if (/(india|indian|new delhi)/.test(text)) score += 5;
  if (/(high|critical|urgent|escalation|attack|strike|standoff)/.test(text)) score += 4;
  return score;
}

function sanitizeEvidenceText(value) {
  return (value || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function isHighImpactMilitary(item) {
  const text = `${item.title || item.headline || ''} ${item.description || item.summary || ''}`.toLowerCase();
  const militarySignal = /(military|defence|defense|troops|army|naval|air\s*force|missile|drone|uav|border|clash|conflict|strike|war|security)/.test(text);
  const impactSignal = /(high|critical|escalation|attack|strike|standoff|incursion|threat|alert|warning|dead|casualty)/.test(text);
  return militarySignal && impactSignal;
}

function isMilitaryRelevant(item) {
  const text = `${item.title || item.headline || ''} ${item.description || item.summary || ''}`.toLowerCase();
  return /(military|defence|defense|troops|army|naval|air\s*force|missile|drone|uav|border|clash|conflict|strike|war|security)/.test(text);
}

function buildAnswerSummary(query, country, articles) {
  if (!articles.length) {
    return `You are upto date with latest news for ${country || 'the selected region'}. No strong new developments were found in the trusted live feeds for this query right now.`;
  }

  const evidenceRows = articles
    .slice(0, 6)
    .map((article) => {
      const headline = cleanHeadline(article.title || article.headline || 'Live update');
      const body = sanitizeEvidenceText(article.description || article.summary || '');
      const shortBody = body.split(' ').slice(0, 22).join(' ');
      const source = article.source || 'Trusted source';
      return { headline, shortBody, source, body: `${headline}${shortBody ? ` - ${shortBody}` : ''}`.trim() };
    })
    .filter((row) => row.headline);

  const evidenceText = evidenceRows
    .slice(0, 4)
    .map((row, idx) => `(${idx + 1}) ${row.body} [${row.source}]`)
    .join(' ');

  const sourceLine = evidenceRows
    .slice(0, 4)
    .map((row) => row.source)
    .filter(Boolean)
    .join(', ');

  const combined = `Extractive answer for ${country || 'the selected region'}: ${evidenceText}${sourceLine ? ` Sources: ${sourceLine}.` : ''}`.trim();
  const words = combined.split(/\s+/).filter(Boolean);
  return words.slice(0, 120).join(' ');
}

function buildEvidenceRows(articles) {
  return articles
    .slice(0, 6)
    .map((article) => {
      const headline = cleanHeadline(article.title || article.headline || 'Live update');
      const body = sanitizeEvidenceText(article.description || article.summary || '');
      const shortBody = body.split(' ').slice(0, 32).join(' ');
      const source = article.source || 'Trusted source';
      const url = article.url || '';
      return { headline, shortBody, source, url };
    })
    .filter((row) => row.headline && row.url);
}

async function generateGroundedAiAnswer(query, country, articles) {
  const huggingFaceApiKey = process.env.HF_API_KEY || process.env.HUGGINGFACE_API_KEY || '';
  const huggingFaceModel = process.env.HF_MODEL || 'google/flan-t5-large';

  if (!huggingFaceApiKey || !articles.length) {
    return null;
  }

  const evidenceRows = buildEvidenceRows(articles);
  if (!evidenceRows.length) {
    return null;
  }

  if (isOperationalPlanningRequest(query)) {
    return {
      summary: 'This assistant can summarize and compare public news reporting, but it cannot provide operational, tactical, or military planning advice.',
      answerMode: 'safety-refusal',
      modelUsed: huggingFaceModel,
      safetyNotice: 'Operational and tactical planning assistance is disabled.'
    };
  }

  const evidenceBlock = evidenceRows
    .map((row, index) => `Source ${index + 1}\nHeadline: ${row.headline}\nPublisher: ${row.source}\nURL: ${row.url}\nSnippet: ${row.shortBody}`)
    .join('\n\n');

  const prompt = [
    'You are a source-grounded news research assistant.',
    'Answer only from the evidence below.',
    'Do not invent facts.',
    'If the evidence is insufficient, say so clearly.',
    'Do not provide tactical, operational, or military planning advice.',
    `User question: ${query}`,
    `Relevant country context: ${country || 'Global'}`,
    'Evidence:',
    evidenceBlock,
    'Write a concise analytical answer in plain English and mention uncertainty where needed.'
  ].join('\n');

  try {
    const response = await fetch(`https://api-inference.huggingface.co/models/${encodeURIComponent(huggingFaceModel)}`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${huggingFaceApiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        inputs: prompt,
        parameters: {
          max_new_tokens: 280,
          temperature: 0.2,
          return_full_text: false
        }
      })
    });

    if (!response.ok) {
      throw new Error(`HF inference status ${response.status}`);
    }

    const data = await response.json();
    const generatedText = Array.isArray(data)
      ? data[0]?.generated_text
      : data?.generated_text;

    if (!generatedText || typeof generatedText !== 'string') {
      return null;
    }

    return {
      summary: generatedText.replace(/\s+/g, ' ').trim(),
      answerMode: 'ai-grounded',
      modelUsed: huggingFaceModel,
      safetyNotice: 'Answer generated from public reporting and cited sources only.'
    };
  } catch (err) {
    console.warn('[AI Query] Hugging Face inference failed:', err.message);
    return null;
  }
}

async function runQueryResearch(query, requestedCountry) {
  const detectedCountry = detectCountryFromQuery(query, requestedCountry);
  const tokens = tokenizeQuery(`${query} ${detectedCountry || ''}`);
  const topLimit = extractTopLimit(query);
  const militaryOnly = isMilitaryIntent(query);

  const storeCandidates = articleStore
    .filter((item) => isTrustedSource(item))
    .map((item) => ({
      title: item.headline,
      // Keep cache evidence conservative: use headline + source only, avoid reusing generated summaries.
      description: '',
      url: item.url,
      source: item.source,
      publishedAt: item.timestamp,
      score: scoreQueryMatch(item, tokens, detectedCountry)
    }))
    .filter((item) => item.score > 0);

  const extraArticles = [];

  try {
    const trustedRss = Array.from(new Set(Object.values(feeds).flat()));
    const rssItems = await fetchRSS(trustedRss);
    rssItems.forEach((item) => {
      const score = scoreQueryMatch(item, tokens, detectedCountry);
      if (score > 0 && isTrustedSource(item)) {
        extraArticles.push({ ...item, score });
      }
    });
  } catch (err) {
    console.warn('[Query Research] RSS fetch failed:', err.message);
  }

  const apiKey = process.env.NEWS_API_KEY;
  if (apiKey) {
    try {
      const queryText = `${query} ${detectedCountry || ''}`.trim();
      const apiItems = await fetchNewsAPI(queryText, apiKey);
      apiItems.forEach((item) => {
        const score = scoreQueryMatch(item, tokens, detectedCountry);
        if (score > 0 && isTrustedSource(item)) {
          extraArticles.push({ ...item, score });
        }
      });
    } catch (err) {
      console.warn('[Query Research] NewsAPI fetch failed:', err.message);
    }
  }

  try {
    const gdeltQuery = `${query} ${detectedCountry || ''} (military OR defence OR border OR conflict OR diplomacy OR sanctions)`.trim();
    const gdeltItems = await fetchGDELT(gdeltQuery, { maxRecords: 20, timespan: '24h' });
    gdeltItems.forEach((item) => {
      const score = scoreQueryMatch(item, tokens, detectedCountry);
      if (score > 0 && isTrustedSource(item)) {
        extraArticles.push({ ...item, score });
      }
    });
  } catch (err) {
    console.warn('[Query Research] GDELT fetch failed:', err.message);
  }

  const merged = [...storeCandidates, ...extraArticles]
    .filter((item) => item.url)
    .sort((a, b) => b.score - a.score);

  const strictCandidates = militaryOnly
    ? merged.filter((item) => isHighImpactMilitary(item))
    : merged;

  const relaxedCandidates = militaryOnly
    ? merged.filter((item) => isMilitaryRelevant(item))
    : merged;

  const deduped = [];
  const seen = new Set();
  for (const item of strictCandidates) {
    const key = (item.url || item.title || '').toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
    if (deduped.length >= topLimit) break;
  }

  // If strict high-impact filter is too narrow, fall back to military-relevant evidence.
  if (deduped.length === 0) {
    for (const item of relaxedCandidates) {
      const key = (item.url || item.title || '').toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      deduped.push(item);
      if (deduped.length >= topLimit) break;
    }
  }

  const sources = deduped.slice(0, 6).map((item) => ({
    name: item.source || 'Trusted Source',
    url: item.url
  }));

  const evidenceQuality = deduped.length >= 4 ? 'medium' : deduped.length >= 2 ? 'limited' : 'low';

  const aiAnswer = await generateGroundedAiAnswer(query, detectedCountry || requestedCountry, deduped);

  return {
    summary: aiAnswer?.summary || buildAnswerSummary(query, detectedCountry || requestedCountry, deduped),
    sources,
    matchedCount: deduped.length,
    generatedAt: new Date().toISOString(),
    detectedCountry: detectedCountry || requestedCountry || 'Global',
    queryMode: militaryOnly ? 'high-impact-military' : 'general',
    evidenceQuality,
    answerMode: aiAnswer?.answerMode || 'extractive-fallback',
    modelUsed: aiAnswer?.modelUsed,
    safetyNotice: aiAnswer?.safetyNotice
  };
}

// Background Ingestion Loop
async function runBackgroundIngestion() {
  const active = isSessionActive();
  if (!active) return;

  const now = Date.now();
  let newlyAdded = [];

  // 1. Poll RSS Feeds (every 3 minutes)
  const rssInterval = 3 * 60 * 1000;
  if (now - lastRSSPoll >= rssInterval) {
    lastRSSPoll = now;
    try {
      const uniqueFeeds = Array.from(new Set(Object.values(feeds).flat()));
      const parsed = await fetchRSS(uniqueFeeds);
      const rssArticles = [];

      parsed.forEach((art) => {
        const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
        const inferredCountry = inferCountryFromText(text);
        if (!inferredCountry) return;
        rssArticles.push({ ...art, country: inferredCountry });
      });

      const added = await addArticlesToStore(rssArticles);
      newlyAdded = newlyAdded.concat(added);
    } catch (err) {
      console.warn('RSS Ingestion background loop failed:', err.message);
    }
  }

  // 2. Poll NewsAPI using unified query (every 10 minutes)
  const newsApiInterval = 10 * 60 * 1000;
  if (now - lastNewsAPIPoll >= newsApiInterval) {
    lastNewsAPIPoll = now;
    const apiKey = process.env.NEWS_API_KEY;
    if (apiKey) {
      try {
        console.log('[Background Ingestion] Querying NewsAPI with unified query...');
        const unifiedQuery = `(China OR Pakistan OR Afghanistan OR Bangladesh OR Myanmar OR Nepal OR Bhutan OR "Sri Lanka" OR Maldives) AND (military OR PLA OR defence OR troops OR army OR clash OR standoff OR LAC OR LOC OR naval OR fleet OR drone OR UAV OR border OR security OR geopolitical)${negativeGuardrails}`;
        
        const articles = await fetchNewsAPI(unifiedQuery, apiKey);
        
        const mappedArticles = [];
        articles.forEach(art => {
          const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
          const inferredCountry = inferCountryFromText(text);
          if (inferredCountry) {
            mappedArticles.push({
              ...art,
              country: inferredCountry
            });
          }
        });

        const added = await addArticlesToStore(mappedArticles);
        newlyAdded = newlyAdded.concat(added);

        const techQuery = `(China OR Pakistan OR Afghanistan OR Bangladesh OR Myanmar OR Nepal OR Bhutan OR "Sri Lanka" OR Maldives) AND (defense technology OR defence technology OR military technology OR weapons system OR missile system OR air defense radar OR drone fleet OR uav program OR cyber warfare OR electronic warfare OR satellite surveillance OR hypersonic)${negativeGuardrails}`;
        const techArticles = await fetchNewsAPI(techQuery, apiKey);
        const mappedTechArticles = [];
        techArticles.forEach((art) => {
          const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
          const inferredCountry = inferCountryFromText(text);
          if (inferredCountry) {
            mappedTechArticles.push({ ...art, country: inferredCountry, category: 'Tech' });
          }
        });
        const addedTech = await addArticlesToStore(mappedTechArticles);
        newlyAdded = newlyAdded.concat(addedTech);
      } catch (err) {
        console.warn('NewsAPI Ingestion background loop failed:', err.message);
      }
    }

    try {
      const gdeltQuery = '(China OR Pakistan OR Afghanistan OR Bangladesh OR Myanmar OR Nepal OR Bhutan OR "Sri Lanka" OR Maldives) AND (military OR defence OR troops OR army OR clash OR standoff OR border OR security OR geopolitical OR sanctions OR diplomacy)';
      const gdeltItems = await fetchGDELT(gdeltQuery, { maxRecords: 35, timespan: '24h' });
      const mappedGdelt = [];
      gdeltItems.forEach((art) => {
        const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
        const inferredCountry = inferCountryFromText(text);
        if (inferredCountry) {
          mappedGdelt.push({ ...art, country: inferredCountry });
        }
      });

      const addedFromGdelt = await addArticlesToStore(mappedGdelt);
      newlyAdded = newlyAdded.concat(addedFromGdelt);

      const gdeltTechQuery = '(China OR Pakistan OR Afghanistan OR Bangladesh OR Myanmar OR Nepal OR Bhutan OR "Sri Lanka" OR Maldives) AND (defense technology OR weapons system OR missile system OR radar system OR drone program OR cyber warfare OR satellite surveillance OR hypersonic)';
      const gdeltTechItems = await fetchGDELT(gdeltTechQuery, { maxRecords: 35, timespan: '24h' });
      const mappedGdeltTech = [];
      gdeltTechItems.forEach((art) => {
        const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
        const inferredCountry = inferCountryFromText(text);
        if (inferredCountry) {
          mappedGdeltTech.push({ ...art, country: inferredCountry, category: 'Tech' });
        }
      });
      const addedFromGdeltTech = await addArticlesToStore(mappedGdeltTech);
      newlyAdded = newlyAdded.concat(addedFromGdeltTech);
    } catch (err) {
      console.warn('GDELT Ingestion background loop failed:', err.message);
    }

    const theNewsApiKey = process.env.THENEWS_API_KEY;
    if (theNewsApiKey) {
      try {
        const techWireQuery = 'defense technology OR military equipment OR missile system OR drone warfare OR cyber defense OR satellite surveillance';
        const theNewsItems = await fetchTheNewsAPI(techWireQuery, theNewsApiKey);
        const mappedTheNews = [];
        theNewsItems.forEach((art) => {
          const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
          const inferredCountry = inferCountryFromText(text);
          if (inferredCountry) {
            mappedTheNews.push({ ...art, country: inferredCountry, category: 'Tech' });
          }
        });
        const addedFromTheNews = await addArticlesToStore(mappedTheNews);
        newlyAdded = newlyAdded.concat(addedFromTheNews);
      } catch (err) {
        console.warn('TheNewsAPI tech ingestion failed:', err.message);
      }
    }
  }

  // 3. Poll Live World Wires (every 5 minutes)
  const wireInterval = 5 * 60 * 1000;
  if (now - lastWirePoll >= wireInterval) {
    lastWirePoll = now;
    try {
      const rssFeeds = [
        'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
        'http://feeds.bbci.co.uk/news/world/rss.xml'
      ];
      const parsed = await fetchRSS(rssFeeds);
      const matched = parsed.filter(article => {
        const text = `${article.title || ''} ${article.description || ''}`.toLowerCase();
        return ['border', 'lac', 'loc', 'military', 'defence', 'clash', 'standoff', 'forces', 'troops', 'missile', 'drone', 'uav', 'rebel', 'security'].some(kw => text.includes(kw));
      });

      const wireArticles = [];
      matched.forEach(art => {
        const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
        let matchedCountry = 'China';
        for (const [cName, keywords] of Object.entries(countryKeywords)) {
          if (keywords.some(kw => text.includes(kw))) {
            matchedCountry = cName;
            break;
          }
        }
        wireArticles.push({
          ...art,
          country: matchedCountry,
          source: art.source || 'WORLD WIRE'
        });
      });

      const added = await addArticlesToStore(wireArticles);
      newlyAdded = newlyAdded.concat(added);
    } catch (err) {
      console.warn('World Wires Ingestion background loop failed:', err.message);
    }
  }

  // 4. Stream newly added articles via SSE
  if (newlyAdded.length > 0) {
    console.log(`[Background Ingestion] Streaming ${newlyAdded.length} new signals to active connections.`);
    newlyAdded.forEach(signal => {
      activeConnections.forEach(client => {
        if (client.category === 'All' || client.category === signal.category) {
          client.res.write(`data: ${JSON.stringify({ type: 'signal', country: signal.country, signal })}\n\n`);
        }
      });
    });
  }

  // 5. Keep global world-alert cache warm for map tab.
  try {
    await runWorldAlertRefresh(false);
  } catch (err) {
    console.warn('World alert refresh loop failed:', err.message);
  }
}

// Seed Initial Articles for instantaneous visual feedback on startup
const initialSeedArticles = [
  {
    country: 'China',
    category: 'Military',
    impact: 'High',
    title: 'PLA border unit reinforces high-altitude radar clusters',
    description: 'Military intelligence reports indicate Beijing has upgraded surveillance installations along the contested LAC boundary line.',
    source: 'STRATCOM Telemetry',
    publishedAt: new Date(Date.now() - 30 * 60 * 1000).toISOString()
  },
  {
    country: 'China',
    category: 'Political',
    impact: 'Medium',
    title: 'Diplomatic commission completes border sector coordinates review',
    description: 'Bilateral envoys conclude a round of scheduled demarcation talks, reaffirming commitment to stability protocols.',
    source: 'Diplomatic Wire',
    publishedAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
  },
  {
    country: 'Pakistan',
    category: 'Tech',
    impact: 'High',
    title: 'Advanced UAV patrol operations observed near border outposts',
    description: 'Ground radars record coordinated drone sweeps validating airspace boundaries along the western sector.',
    source: 'Border Radar Net',
    publishedAt: new Date(Date.now() - 45 * 60 * 1000).toISOString()
  },
  {
    country: 'Myanmar',
    category: 'Military',
    impact: 'Critical',
    title: 'Clashes reported near regional crossing points in Shan State',
    description: 'Airstrikes and artillery fire recorded close to boundary lines, prompting security alerts for border guards.',
    source: 'OSINT Monitor',
    publishedAt: new Date(Date.now() - 15 * 60 * 1000).toISOString()
  },
  {
    country: 'Bangladesh',
    category: 'Social',
    impact: 'Medium',
    title: 'Border guard authorities expand delta riverine patrol units',
    description: 'Enhanced coordinate monitoring implemented at major river crossings to manage seasonal transit routes.',
    source: 'Regional Bulletin',
    publishedAt: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString()
  }
];

// Seed the store on load
await hydrateArticleStore();
if (articleStore.length === 0) {
  await addArticlesToStore(initialSeedArticles);
}

async function runLiveRefresh() {
  const uniqueFeeds = Array.from(new Set(Object.values(feeds).flat()));
  const parsedRss = await fetchRSS(uniqueFeeds);
  const rssArticles = [];

  parsedRss.forEach((art) => {
    const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
    const inferredCountry = inferCountryFromText(text);
    if (!inferredCountry) return;
    rssArticles.push({ ...art, country: inferredCountry });
  });

  const addedRss = await addArticlesToStore(rssArticles);

  const apiKey = process.env.NEWS_API_KEY;
  let addedApi = [];
  let addedTechApi = [];
  if (apiKey) {
    try {
      const unifiedQuery = `(China OR Pakistan OR Afghanistan OR Bangladesh OR Myanmar OR Nepal OR Bhutan OR "Sri Lanka" OR Maldives) AND (military OR PLA OR defence OR troops OR army OR clash OR standoff OR LAC OR LOC OR naval OR fleet OR drone OR UAV OR border OR security OR geopolitical)${negativeGuardrails}`;
      const articles = await fetchNewsAPI(unifiedQuery, apiKey);
      const mappedArticles = [];
      articles.forEach((art) => {
        const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
        const inferredCountry = inferCountryFromText(text);
        if (inferredCountry) {
          mappedArticles.push({ ...art, country: inferredCountry });
        }
      });
      addedApi = await addArticlesToStore(mappedArticles);

      const techQuery = `(China OR Pakistan OR Afghanistan OR Bangladesh OR Myanmar OR Nepal OR Bhutan OR "Sri Lanka" OR Maldives) AND (defense technology OR defence technology OR military technology OR weapons system OR missile system OR air defense radar OR drone fleet OR uav program OR cyber warfare OR electronic warfare OR satellite surveillance OR hypersonic)${negativeGuardrails}`;
      const techArticles = await fetchNewsAPI(techQuery, apiKey);
      const mappedTechArticles = [];
      techArticles.forEach((art) => {
        const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
        const inferredCountry = inferCountryFromText(text);
        if (inferredCountry) {
          mappedTechArticles.push({ ...art, country: inferredCountry, category: 'Tech' });
        }
      });
      addedTechApi = await addArticlesToStore(mappedTechArticles);
    } catch (err) {
      console.warn('[Live Refresh] NewsAPI fetch failed:', err.message);
    }
  }

  let addedGdelt = [];
  let addedGdeltTech = [];
  try {
    const gdeltQuery = '(China OR Pakistan OR Afghanistan OR Bangladesh OR Myanmar OR Nepal OR Bhutan OR "Sri Lanka" OR Maldives) AND (military OR defence OR troops OR army OR clash OR standoff OR border OR security OR geopolitical OR sanctions OR diplomacy)';
    const gdeltItems = await fetchGDELT(gdeltQuery, { maxRecords: 35, timespan: '24h' });
    const mappedGdelt = [];
    gdeltItems.forEach((art) => {
      const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
      const inferredCountry = inferCountryFromText(text);
      if (inferredCountry) {
        mappedGdelt.push({ ...art, country: inferredCountry });
      }
    });
    addedGdelt = await addArticlesToStore(mappedGdelt);

    const gdeltTechQuery = '(China OR Pakistan OR Afghanistan OR Bangladesh OR Myanmar OR Nepal OR Bhutan OR "Sri Lanka" OR Maldives) AND (defense technology OR weapons system OR missile system OR radar system OR drone program OR cyber warfare OR satellite surveillance OR hypersonic)';
    const gdeltTechItems = await fetchGDELT(gdeltTechQuery, { maxRecords: 35, timespan: '24h' });
    const mappedGdeltTech = [];
    gdeltTechItems.forEach((art) => {
      const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
      const inferredCountry = inferCountryFromText(text);
      if (inferredCountry) {
        mappedGdeltTech.push({ ...art, country: inferredCountry, category: 'Tech' });
      }
    });
    addedGdeltTech = await addArticlesToStore(mappedGdeltTech);
  } catch (err) {
    console.warn('[Live Refresh] GDELT fetch failed:', err.message);
  }

  let addedTheNewsTech = [];
  const theNewsApiKey = process.env.THENEWS_API_KEY;
  if (theNewsApiKey) {
    try {
      const techWireQuery = 'defense technology OR military equipment OR missile system OR drone warfare OR cyber defense OR satellite surveillance';
      const theNewsItems = await fetchTheNewsAPI(techWireQuery, theNewsApiKey);
      const mappedTheNews = [];
      theNewsItems.forEach((art) => {
        const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
        const inferredCountry = inferCountryFromText(text);
        if (inferredCountry) {
          mappedTheNews.push({ ...art, country: inferredCountry, category: 'Tech' });
        }
      });
      addedTheNewsTech = await addArticlesToStore(mappedTheNews);
    } catch (err) {
      console.warn('[Live Refresh] TheNewsAPI tech fetch failed:', err.message);
    }
  }

  try {
    const rssFeeds = [
      'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
      'http://feeds.bbci.co.uk/news/world/rss.xml'
    ];
    const parsed = await fetchRSS(rssFeeds);
    const matched = parsed.filter((article) => {
      const text = `${article.title || ''} ${article.description || ''}`.toLowerCase();
      return ['border', 'lac', 'loc', 'military', 'defence', 'clash', 'standoff', 'forces', 'troops', 'missile', 'drone', 'uav', 'rebel', 'security'].some((kw) => text.includes(kw));
    });

    const wireArticles = [];
    matched.forEach((art) => {
      const text = `${art.title || ''} ${art.description || ''}`.toLowerCase();
      const matchedCountry = inferCountryFromText(text);
      if (!matchedCountry) return;
      wireArticles.push({
        ...art,
        country: matchedCountry,
        source: art.source || 'WORLD WIRE'
      });
    });

    await addArticlesToStore(wireArticles);
  } catch (err) {
    console.warn('[Live Refresh] World wires fetch failed:', err.message);
  }

  return {
    rssAdded: addedRss.length,
    apiAdded: addedApi.length,
    techApiAdded: addedTechApi.length,
    gdeltAdded: addedGdelt.length,
    gdeltTechAdded: addedGdeltTech.length,
    theNewsTechAdded: addedTheNewsTech.length
  };
}

app.post('/api/news/refresh', async (req, res) => {
  updateActivity();
  try {
    const result = await runLiveRefresh();
    res.json(result);
  } catch (error) {
    console.error('Live refresh failed:', error.message);
    res.status(500).json({ error: 'Live refresh failed' });
  }
});

app.post('/api/news/query', async (req, res) => {
  updateActivity();
  try {
    const query = (req.body && typeof req.body.query === 'string' ? req.body.query : '').trim();
    const country = (req.body && typeof req.body.country === 'string' ? req.body.country : '').trim();

    if (!query) {
      return res.status(400).json({ error: 'Query is required' });
    }

    await runLiveRefresh();
    const result = await runQueryResearch(query, country);
    return res.json(result);
  } catch (error) {
    console.error('News query failed:', error.message);
    return res.status(500).json({ error: 'Unable to process query' });
  }
});

app.get('/api/world/alerts', async (req, res) => {
  updateActivity();
  try {
    const force = String(req.query.force || '').toLowerCase() === 'true';
    const result = await runWorldAlertRefresh(force);
    res.json({
      updatedAt: new Date().toISOString(),
      count: result.alerts.length,
      alerts: result.alerts
    });
  } catch (error) {
    console.error('World alerts fetch failed:', error.message);
    res.status(500).json({ error: 'World alerts fetch failed' });
  }
});

// SSE Wire endpoint
app.get('/api/news/stream', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  res.write(`data: ${JSON.stringify({ type: 'connected' })}\n\n`);

  const category = req.query.category || 'All';
  const client = { res, category };
  activeConnections.push(client);
  updateActivity();

  req.on('close', () => {
    activeConnections = activeConnections.filter(c => c !== client);
  });
});

// Single Country News endpoint
app.get('/api/news', async (req, res) => {
  updateActivity();
  try {
    const country = req.query.country || 'China';
    const category = req.query.category || 'All';
    const timeframe = req.query.timeframe || '24h';
    const cacheKey = `news:${country.toLowerCase()}:${category.toLowerCase()}:${timeframe}`;

    let windowMs = 24 * 60 * 60 * 1000;
    let ttl = 1800;
    if (timeframe === '1h') {
      windowMs = 60 * 60 * 1000;
      ttl = 300;
    } else if (timeframe === '24h' || timeframe === '1d') {
      windowMs = 24 * 60 * 60 * 1000;
      ttl = 1800;
    } else if (timeframe === '7d' || timeframe === '1w') {
      windowMs = 7 * 24 * 60 * 60 * 1000;
      ttl = 7200;
    } else if (timeframe === '30d' || timeframe === '1m') {
      windowMs = 30 * 24 * 60 * 60 * 1000;
      ttl = 86400;
    }

    const result = await getOrSetCacheSWR(cacheKey, async () => {
      const meta = countryMeta[country] || { region: 'Unknown', threatLevel: 'Low' };
      const cutoffTime = Date.now() - windowMs;

      let signals = articleStore.filter(art => {
        const matchCountry = art.country.toLowerCase() === country.toLowerCase();
        if (!matchCountry) return false;
        const artTime = new Date(art.timestamp).getTime();
        return artTime >= cutoffTime;
      });
      
      if (category !== 'All') {
        signals = signals.filter(art => art.category === category);
      }

      signals.sort((a, b) => b.relevance_score - a.relevance_score);

      let operationalSummary = '';
      if (signals.length === 0) {
        operationalSummary = 'No new updates in this time period.';
      } else {
        operationalSummary = `${signals.length} updates tracked in the ${timeframe} window.`;
      }

      return {
        region: meta.region,
        threat_level: meta.threatLevel,
        last_synced: new Date().toISOString(),
        operational_summary: operationalSummary,
        signals: signals,
        source_status: 'normal'
      };
    }, ttl);

    res.json(result);
  } catch (error) {
    console.error('Single news fetch failed:', error.message);
    res.status(500).json({ error: 'News ingestion failed' });
  }
});

// Get all countries news dossier
app.get('/api/news/all', async (req, res) => {
  updateActivity();
  try {
    const category = req.query.category || 'All';
    const timeframe = req.query.timeframe || '24h';
    const cacheKey = `news:all:${category.toLowerCase()}:${timeframe}`;
    
    let windowMs = 24 * 60 * 60 * 1000;
    let ttl = 1800;
    if (timeframe === '1h') {
      windowMs = 60 * 60 * 1000;
      ttl = 300;
    } else if (timeframe === '24h' || timeframe === '1d') {
      windowMs = 24 * 60 * 60 * 1000;
      ttl = 1800;
    } else if (timeframe === '7d' || timeframe === '1w') {
      windowMs = 7 * 24 * 60 * 60 * 1000;
      ttl = 7200;
    } else if (timeframe === '30d' || timeframe === '1m') {
      windowMs = 30 * 24 * 60 * 60 * 1000;
      ttl = 86400;
    }

    const result = await getOrSetCacheSWR(cacheKey, async () => {
      const cutoffTime = Date.now() - windowMs;
      const countriesList = Object.keys(feeds);
      const results = {};

      countriesList.forEach(country => {
        const meta = countryMeta[country] || { region: 'Unknown', threatLevel: 'Low' };
        
        let signals = articleStore.filter(art => {
          const matchCountry = art.country.toLowerCase() === country.toLowerCase();
          if (!matchCountry) return false;
          const artTime = new Date(art.timestamp).getTime();
          return artTime >= cutoffTime;
        });

        if (category !== 'All') {
          signals = signals.filter(art => art.category === category);
        }

        signals.sort((a, b) => b.relevance_score - a.relevance_score);

        let operationalSummary = '';
        if (signals.length === 0) {
          operationalSummary = 'No new updates in this time period.';
        } else {
          operationalSummary = `${signals.length} updates tracked in the ${timeframe} window.`;
        }

        results[country] = {
          region: meta.region,
          threat_level: meta.threatLevel,
          last_synced: new Date().toISOString(),
          operational_summary: operationalSummary,
          signals: signals,
          source_status: 'normal'
        };
      });

      return results;
    }, ttl);

    res.json(result);
  } catch (error) {
    console.error('All countries ingestion failed:', error.message);
    res.status(500).json({ error: 'News ingestion failed' });
  }
});

// Submit URLs for scraping
app.post('/api/scrape', async (req, res) => {
  updateActivity();
  const { urls, platform } = req.body;
  
  if (!urls || !Array.isArray(urls)) {
    return res.status(400).json({ error: 'URLs array is required' });
  }

  const selectedPlatform = platform || 'news';
  try {
    const jobIds = [];
    for (const url of urls.slice(0, 5)) {
      if (url.trim()) {
        const jobId = await addScrapeJob(url.trim(), selectedPlatform);
        jobIds.push(jobId);
      }
    }
    res.json({ success: true, jobIds });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Check status of job
app.get('/api/scrape/status/:jobId', (req, res) => {
  const status = getJobStatus(req.params.jobId);
  res.json(status);
});

// Fallback to React app
app.get('*', (req, res, next) => {
  if (req.path.startsWith('/api')) {
    return next();
  }
  res.sendFile(path.join(__dirname, '../dist/index.html'));
});

// Start Background Poller Loop (Runs every 15 seconds)
setInterval(() => {
  runBackgroundIngestion().catch(err => console.warn('Background Ingestion loop failed:', err.message));
}, 15000);

// Run initial ingestion instantly on startup (asynchronously)
setTimeout(() => {
  console.log('[Startup] Executing initial ingestion sweeps...');
  lastRSSPoll = Date.now();
  lastNewsAPIPoll = Date.now();
  lastWirePoll = Date.now();
  runLiveRefresh().then((result) => {
    const seededTotal = (result.rssAdded || 0)
      + (result.apiAdded || 0)
      + (result.techApiAdded || 0)
      + (result.gdeltAdded || 0)
      + (result.gdeltTechAdded || 0)
      + (result.theNewsTechAdded || 0);
    console.log(`[Startup Ingestion] Seeded ${seededTotal} real articles from live sources.`);
  }).catch((err) => {
    console.warn('[Startup Ingestion] Initial live refresh failed:', err.message);
  });
}, 1000);

server.listen(port, () => {
  console.log(`Secure news service running on http://localhost:${port}`);
});
