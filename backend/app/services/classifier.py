from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.database import Article
from backend.app.observability import metrics

logger = logging.getLogger("drishya.classifier")


class MemoryLiveStream:
    def __init__(self) -> None:
        self.articles: List[dict] = []
        self.subscribers: List[Any] = []

    def publish(self, article_data: dict) -> None:
        self.articles.append(article_data)
        if len(self.articles) > 100:
            self.articles.pop(0)
        for sub in list(self.subscribers):
            try:
                sub(article_data)
            except Exception:
                continue

    def subscribe(self, callback: Any) -> None:
        self.subscribers.append(callback)


memory_stream = MemoryLiveStream()

async def _get_redis():
    """Return the shared Redis pool (or False if Redis dedup is disabled)."""
    if not settings.enable_redis_dedup:
        return False
    from backend.app.redis_pool import get_redis_pool
    return await get_redis_pool()


async def _bump_archive_version() -> None:
    pool = await _get_redis()
    if not pool:
        return
    try:
        await pool.incr("drishya:archive:version")
    except Exception:
        return


def compute_source_reputation(source: Optional[str]) -> str:
    if not source:
        return "Unrated"
    src = source.lower().strip()
    
    # Wire agencies, major global and regional outlets
    verified = [
        # Wire agencies
        "reuters.com", "apnews.com", "reuters", "apnews",
        # Major global outlets
        "aljazeera.com", "bbc.com", "bbc.co.uk", "dw.com", "dw.com",
        "france24.com", "theguardian.com", "nytimes.com", "bloomberg.com",
        "washingtonpost.com", "cnn.com", "cnn.com", "wsj.com",
        "ft.com", "economist.com", "foreignaffairs.com",
        # Indian wire services
        "pti", "press trust of india", "ptinews.com",
        "ani", "asian news international", "aninews.in",
        "thehindu.com", "the hindu", "indianexpress.com",
        "timesofindia", "times of india", "indiatimes.com",
        "livemint.com", "ndtv.com", "ndtv.com", "hindustantimes.com",
        # Chinese state media
        "xinhua", "xinhuanet.com", "scmp.com", "south china morning post",
        "globaltimes.cn", "cgtn.com", "chinadaily.com.cn",
        # Pakistani outlets
        "app.com.pk", "associated press of pakistan",
        "dawn.com", "dawn news", "geo.tv", "thenews.com.pk",
        # Russian/Soviet
        "interfax.com", "tass.com", "tass", "rt.com",
        # Iranian
        "irna.ir", "irna", "presstv.ir",
        # Korean
        "yna.co.kr", "koreaherald.com", "koreatimes.co.kr",
        # Japanese
        "japantimes.co.jp", "kyodonews.net",
        # European
        "dpa.com", "afp.com", "anadolu.com.tr",
        # Others
        "straitstimes.com", "bangkokpost.com", "todayonline.com",
    ]
    for v in verified:
        if v in src:
            return "Verified Source"
            
    # Known aggregators
    aggregators = [
        "yahoo.com", "msn.com", "google.com", "news.google.com", "reddit.com",
        "feedburner", "rss", "aggregator"
    ]
    for a in aggregators:
        if a in src:
            return "Developing"
            
    # Unknown/new domains
    return "Unverified"



_transformer = None
def get_transformer():
    global _transformer
    if _transformer is None:
        from sentence_transformers import SentenceTransformer
        _transformer = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    return _transformer


_embedding_cache = {}

async def get_embeddings_cached(texts: List[str]) -> List[List[float]]:
    redis_conn = await _get_redis()
    embeddings = [None] * len(texts)
    missing_indices = []
    missing_texts = []
    
    for idx, text in enumerate(texts):
        # Key on text SHA-256 hash
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        
        # 1. Check in-memory cache
        if text_hash in _embedding_cache:
            embeddings[idx] = _embedding_cache[text_hash]
            continue
            
        # 2. Check Redis cache
        if redis_conn:
            try:
                val = await redis_conn.get(f"drishya:emb:{text_hash}")
                if val:
                    vec = json.loads(val)
                    embeddings[idx] = vec
                    _embedding_cache[text_hash] = vec  # Sync to in-memory
                    continue
            except Exception:
                pass
                
        # 3. Add to missing list
        missing_indices.append(idx)
        missing_texts.append(text)
        
    # 4. Generate missing embeddings
    if missing_texts:
        try:
            transformer = get_transformer()
            computed = transformer.encode(missing_texts, convert_to_numpy=True).tolist()
            for local_idx, vec in enumerate(computed):
                orig_idx = missing_indices[local_idx]
                embeddings[orig_idx] = vec
                text_hash = hashlib.sha256(missing_texts[local_idx].encode("utf-8")).hexdigest()
                # Save to in-memory
                _embedding_cache[text_hash] = vec
                # Save to Redis with 7-day TTL
                if redis_conn:
                    try:
                        await redis_conn.setex(f"drishya:emb:{text_hash}", 604800, json.dumps(vec))
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"[Classifier] Failed to compute batch embeddings: {e}")
            for orig_idx in missing_indices:
                embeddings[orig_idx] = [0.0] * settings.embedding_dimensions
                
    return embeddings


DEPT_CENTROIDS = {
    "Military & Defense": [
        "military troop deployment navy combat missile attack army military base exercise troops air force soldiers carrier strike weapons border clash",
        "clashes skirmish casualties gunfire shelling troop movements artillery defense system defense ministry jets fighters drone strike war conflict"
    ],
    "Economic & Financial": [
        "economic growth trade agreement inflation gdp tariffs interest rates trade deal financial market stocks bonds central bank investments business port",
        "currency devaluation fiscal policy economic cooperation imports exports industrial production supply chain trade deficit commerce recession budget"
    ],
    "Social Affairs & Welfare": [
        "humanitarian aid refugee relief migration human rights protests civil unrest social welfare healthcare public education community displacement",
        "disaster response epidemic disease outbreaks labor union strike citizen rights religious freedom housing food security social assistance census"
    ],
    "Political & Diplomatic": [
        "diplomatic relations bilateral summit ambassador treaty signing geopolitical talks state visit embassy opening administration foreign policy minister",
        "elections political parties parliament legislation policy debate government formation coalition leadership transition constitutional reform diplomatic protest"
    ],
    "Technology & Cyber": [
        "cyberattack ransomware malware hacking computer networks database breach artificial intelligence machine learning semiconductor chips technology innovation",
        "telecom 5g network fiber optics digital surveillance encryption data privacy software system cloud computing cyber espionage high-tech hardware drone tech"
    ]
}

IMPACT_CENTROIDS = {
    "High Impact": [
        "war conflict invasion troop deployment casualties missiles nuclear attack defense mobilization border clash air strike declaration of war emergency coup martial law security threat navy fleet airspace violation tactical",
        "extreme security threat defense operations military escalation weapons nuclear capabilities troops combat military drills critical border standoff aircraft interception submarine"
    ],
    "Medium Impact": [
        "trade deals bilateral summits summits talks trade tariff cooperation agreements embassy protest political meeting state visit political reform ministers election cabinet change",
        "foreign relations cooperation agreement international summit policy reform border crossing trade partnership investment projects infrastructure diplomatic talks"
    ],
    "Normal Impact": [
        "weather forecast sports tournament entertainment celebrities movie reviews stock price changes cultural festivals travel guide tourism museum opening local news daily routines consumer product releases features games",
        "daily weather forecast domestic league cricket football quiz show music release food recipe lifestyle tips holiday destinations science trivia tech gadget review"
    ]
}


def extract_first_real_sentence(text: str) -> str:
    if not text:
        return "No summary available."
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    banned_phrases = [
        "Factual OSINT telemetry signal",
        "Surveillance networks report normal stability",
        "Strategic deployment of border patrol sweeps",
        "Monitoring feeds detect active parameters",
        "Strategic deployment of tactical operations"
    ]
    for s in sentences:
        if not any(phrase.lower() in s.lower() for phrase in banned_phrases):
            words = s.split()
            if len(words) > 35:
                return " ".join(words[:35]) + "..."
            return s
    return "No summary available."


def clean_news_output(raw_title: str, raw_snippet: str) -> dict:
    # 1. Strip synthetic alert tags
    clean_title = re.sub(r"\(Telemetry Alert #\d+\)", "", raw_title).strip()
    clean_title = re.sub(r"\b(Telemetry|Intel)\s+Alert\s*#\s*\d+\s*[-:]?\s*", "", clean_title, flags=re.IGNORECASE)
    
    # 2. Prevent static boilerplate sentences from populating the DB
    banned_phrases = [
        "Factual OSINT telemetry signal",
        "Surveillance networks report normal stability",
        "Strategic deployment of border patrol sweeps",
        "Monitoring feeds detect active parameters",
        "Strategic deployment of tactical operations"
    ]
    
    summary = raw_snippet
    for phrase in banned_phrases:
        if phrase.lower() in summary.lower():
            # Replace placeholder with actual first sentence of scraped article
            summary = extract_first_real_sentence(raw_snippet)
            break

    return {
        "title": clean_title,
        "summary": summary
    }


class ImpactClassifier:
    def __init__(self) -> None:
        self.impact_labels = ["High Impact", "Medium Impact", "Normal Impact"]
        self.dept_labels = [
            "Military & Defense",
            "Economic & Financial",
            "Social Affairs & Welfare",
            "Political & Diplomatic",
            "Technology & Cyber",
        ]
        # Pre-compiled regex patterns for O(1) classification
        self.label_keywords = {
            "High Impact": re.compile(r"\b(troop|deployment|missile|clash|invasion|drill|sanction|nuclear|navy|air force|border conflict|skirmish|casualty|coup|strike|war|bombing|airstrike|offensive|ceasefire|hostage|attack|shelling|frontline|battle|combat|killed|dead|mobilization|emergency|martial law)s?\b", re.IGNORECASE),
            "Medium Impact": re.compile(r"\b(bilateral|agreement|trade deal|tariff|summit|protest|refugee|inflation|corruption|embassy|drone|port|aid|negotiation|treaty|accord|sanctions|embargo|diplomatic|election|vote|parliament|legislation|reform|resignation|impeach|embassy|minister|president)s?\b", re.IGNORECASE),
            "Normal Impact": re.compile(r"\b(quiz|sport|cricket|entertainment|weather|stock price|tourism|festival|culture|celebrity|movie|music|game|recipe|fashion|lifestyle|fitness|travel guide|restaurant|hotel|vacation)s?\b", re.IGNORECASE),
        }
        self.dept_keywords = {
            "Military & Defense": re.compile(r"\b(pla|loc|lac|military|troop|air force|navy|missile|radar|defense|border post|uav|drone|arms|exercise|drill|clash|patrol|war|combat|weapon|tank|artillery|submarine|fighter jet|bombing|airstrike|special forces|infantry|frontline|siege|blockade|occupation|invasion|ceasefire|nuclear|surveillance|reconnaissance|intelligence|espionage|peacekeeping|military base|airbase|naval base|logistics|defense cooperation|joint exercise|military drill|force projection|deterrence|escalation|arms race|proliferation|disarmament|peace process|peace talks)s?\b", re.IGNORECASE),
            "Economic & Financial": re.compile(r"\b(economic|trade|finance|tariff|port|investment|infrastructure|road|highway|corridor|inflation|currency|gdp|aid|trade war|trade deal|free trade|customs|import|export|supply chain|manufacturing|energy|oil|gas|mining|agriculture|tourism|banking|insurance|construction|telecom|shipping|airline|aviation|railway|bridge|power plant|renewable|climate|environment|sustainability|digital economy|e-commerce|fintech|blockchain|cryptocurrency|biotech|pharma|healthcare|education|research|innovation|startup|stock market|bond market|forex|commodity|gold|silver|copper|iron|steel|lithium|rare earth)s?\b", re.IGNORECASE),
            "Social Affairs & Welfare": re.compile(r"\b(social|refugee|community|migration|protest|settlement|civilian|health|disease|aid|disaster|religion|citizenship|humanitarian|relief|shelter|food|water|sanitation|education|school|hospital|clinic|medical|pharmaceutical|vaccine|pandemic|epidemic|quarantine|lockdown|curfew|riots|unrest|demonstration|rally|march|strike|boycott|human rights|civil rights|freedom|liberty|democracy|justice|equality|diversity|inclusion|activism|NGO|charity|volunteer|population|immigration|diaspora|asylum|deportation|resettlement|integration|multiculturalism)s?\b", re.IGNORECASE),
            "Political & Diplomatic": re.compile(r"\b(political|diplomat|embassy|border crossing|government|summit|treaty|talks|meeting|minister|president|signing|election|vote|parliament|legislation|reform|coup|resignation|impeach|constitution|amendment|bill|law|decree|judicial|ruling|verdict|trial|court|judge|ambassador|envoy|representative|negotiator|peacekeeper|ceasefire|diplomatic|sanctions|embargo|alliance|coalition|partnership)s?\b", re.IGNORECASE),
            "Technology & Cyber": re.compile(r"\b(cyber|ransomware|malware|hacker|cyberattack|semiconductor|chip|ai|artificial intelligence|robotics|quantum|satellite|surveillance|5G|internet|data|cloud|computing|software|hardware|algorithm|machine learning|blockchain|cryptocurrency|IoT|automation|biotech|pharma|space program|satellite|missile technology|hypersonic|stealth|drone technology)s?\b", re.IGNORECASE),
        }
        self._dept_centroids = None
        self._impact_centroids = None

    def get_centroids(self):
        if self._dept_centroids is None:
            import numpy as np
            transformer = get_transformer()
            self._dept_centroids = {}
            for dept, exemplars in DEPT_CENTROIDS.items():
                vectors = transformer.encode(exemplars, convert_to_numpy=True)
                mean_vector = vectors.mean(axis=0)
                norm = np.linalg.norm(mean_vector)
                self._dept_centroids[dept] = mean_vector / norm if norm > 0 else mean_vector
        return self._dept_centroids

    def get_impact_centroids(self):
        if self._impact_centroids is None:
            import numpy as np
            transformer = get_transformer()
            self._impact_centroids = {}
            for imp, exemplars in IMPACT_CENTROIDS.items():
                vectors = transformer.encode(exemplars, convert_to_numpy=True)
                mean_vector = vectors.mean(axis=0)
                norm = np.linalg.norm(mean_vector)
                self._impact_centroids[imp] = mean_vector / norm if norm > 0 else mean_vector
        return self._impact_centroids

    def classify_regex(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        text_lower = text.lower()
        scores = Counter()
        for label, pattern in self.label_keywords.items():
            scores[label] += len(pattern.findall(text_lower))

        dept_scores = Counter()
        for label, pattern in self.dept_keywords.items():
            dept_scores[label] += len(pattern.findall(text_lower))

        impact = None
        if scores["High Impact"] >= 1:
            impact = "High Impact"
        elif scores["Medium Impact"] > 0:
            impact = "Medium Impact"
        elif scores["Normal Impact"] > 0:
            impact = "Normal Impact"

        dept = dept_scores.most_common(1)[0][0] if dept_scores and dept_scores.most_common(1)[0][1] > 0 else None
        return impact, dept

    def classify_centroid(self, text: str) -> Tuple[str, str]:
        try:
            import numpy as np
            centroids = self.get_centroids()
            transformer = get_transformer()
            text_vec = transformer.encode(text, convert_to_numpy=True)
            text_norm = np.linalg.norm(text_vec)
            if text_norm > 0:
                text_vec = text_vec / text_norm
            else:
                return "Normal Impact", "Political & Diplomatic"

            best_dept = "Political & Diplomatic"
            best_dept_score = -1.0
            for dept, centroid in centroids.items():
                score = float(np.dot(text_vec, centroid))
                if score > best_dept_score:
                    best_dept_score = score
                    best_dept = dept

            impact_centroids = self.get_impact_centroids()
            best_impact = "Normal Impact"
            best_impact_score = -1.0
            for imp, centroid in impact_centroids.items():
                score = float(np.dot(text_vec, centroid))
                if score > best_impact_score:
                    best_impact_score = score
                    best_impact = imp

            return best_impact, best_dept
        except Exception as e:
            logger.warning(f"Centroid classification failed, falling back: {e}")
            return "Normal Impact", "Political & Diplomatic"

    async def classify_llm_fallback(self, title: str, content: str) -> Tuple[str, str]:
        metrics.state.classification_llm_fallback_total += 1
        
        prompt = f"""
        Analyze this article and classify it.
        Categories: "Military & Defense", "Economic & Financial", "Social Affairs & Welfare", "Political & Diplomatic", "Technology & Cyber".
        Impact: "High Impact", "Medium Impact", "Normal Impact".
        
        Return JSON format with keys "impact" and "department". Example:
        {{"impact": "High Impact", "department": "Military & Defense"}}
        
        ARTICLE TITLE: {title}
        ARTICLE CONTENT: {content[:1000]}
        """
        
        summary = ""
        if settings.llm_provider == "ollama" and settings.ollama_base_url:
            try:
                from backend.app.services.summarizer import call_ollama
                summary = await call_ollama(prompt, "You are a classification assistant.")
            except Exception:
                pass
        if not summary and settings.openai_api_key:
            try:
                from backend.app.services.summarizer import call_openai
                summary = await call_openai(prompt, "You are a classification assistant.")
            except Exception:
                pass
        if not summary and settings.google_api_key:
            try:
                from backend.app.services.summarizer import call_gemini
                summary = await call_gemini(prompt, "You are a classification assistant.")
            except Exception:
                pass
                
        if summary:
            try:
                match = re.search(r"\{.*?\}", summary, re.DOTALL)
                if match:
                    parsed = json.loads(match.group(0))
                    imp = parsed.get("impact")
                    dept = parsed.get("department")
                    if imp in self.impact_labels and dept in self.dept_labels:
                        return imp, dept
            except Exception as e:
                logger.warning(f"Failed to parse LLM classification: {e}")
                
        metrics.state.classification_regex_fallback_total += 1
        return self.classify_centroid(f"{title} {content}")

    def classify(self, title: str, content: str) -> Tuple[str, str]:
        text = f"{title} {content[:1200]}".lower()
        imp, dept = self.classify_regex(text)
        if not imp or not dept:
            imp_sem, dept_sem = self.classify_centroid(text)
            imp = imp or imp_sem
            dept = dept or dept_sem
        return imp, dept

    def classify_with_confidence(self, title: str, content: str) -> Tuple[str, str, float]:
        """Classify with a confidence score (0.0-1.0)."""
        text = f"{title} {content[:1200]}".lower()
        imp, dept = self.classify_regex(text)
        
        # Confidence based on match strength
        imp_score = 0.0
        dept_score = 0.0
        if imp:
            imp_matches = len(self.label_keywords[imp].findall(text))
            imp_score = min(1.0, imp_matches / 3.0)  # 3+ matches = full confidence
        if dept:
            dept_matches = len(self.dept_keywords[dept].findall(text))
            dept_score = min(1.0, dept_matches / 3.0)
        
        # If regex failed, use centroid (lower confidence)
        if not imp or not dept:
            imp_sem, dept_sem = self.classify_centroid(text)
            imp = imp or imp_sem
            dept = dept or dept_sem
            imp_score = max(imp_score, 0.5)  # centroid gets 0.5 baseline
            dept_score = max(dept_score, 0.5)
        
        confidence = (imp_score + dept_score) / 2.0
        return imp, dept, confidence


    async def extract_intelligence(self, title: str, content: str, country_code: str) -> dict:
        """
        Extracts structured intelligence according to the Production Intelligence Formatter.
        If LLM is available, queries it with prompt constraints.
        Otherwise, falls back to local rule-based heuristic parsing.
        """
        from backend.app.services.ingestion import ISO_COUNTRIES
        country_name = ISO_COUNTRIES.get(country_code, country_code)
        
        prompt = f"""
        ### System Instructions: Production Intelligence Formatter

        You are a concise, factual news editor for a real-time geopolitical intelligence dashboard.

        Your goal is to parse raw news articles into clean, high-signal alerts. You must preserve real facts, dates, locations, and actions while eliminating synthetic filler, robotic labels, and unnecessary jargon.

        ---

        ### BANNED PATTERNS & JARGON (NEVER USE)
        1. Synthetic IDs & Counters: Never invent codes or tags like "Telemetry Alert #200", "OSINT Signal #42", "Feed ID 901".
        2. Boilerplate Filler: Never output generic phrases such as:
           - "Factual OSINT telemetry signal indicating..."
           - "Surveillance networks report normal stability."
           - "Strategic deployment of tactical operations..."
           - "Monitoring feeds detect active parameters..."
        3. Unverified Speculation: Do not invent units, equipment, or troop numbers if they are not explicitly present in the source text.

        ---

        ### WRITING RULES
        - Title: Write a direct, clear headline stating who did what and where (Max 10 words). Use standard active voice.
        - Summary: Exactly 1 to 2 sentences describing the core development and its immediate impact (Max 35 words).
        - Location: The specific border, sector, town, or body of water mentioned (e.g., "Eastern Ladakh (LAC)", "Tawang Sector", "Line of Control").
        - Entities: Extracted names of official bodies, units, nations, or leaders directly mentioned.

        ---

        ### OUTPUT FORMAT (Strict JSON)
        {{
          "clean_title": "Direct factual headline without IDs or filler words",
          "summary": "1-2 sentence concise factual summary of what actually happened.",
          "location": "Specific border sector or geographic point",
          "category": "Military | Diplomacy | Infrastructure | Trade | Cyber",
          "impact": "HIGH | MEDIUM | LOW",
          "source_domain": "e.g. reuters.com",
          "entities": ["List", "of", "extracted", "official", "entities"]
        }}

        ARTICLE TITLE: {title}
        ARTICLE CONTENT: {content[:4000]}
        COUNTRY CONTEXT: {country_name} ({country_code})
        """
        
        extracted_data = None
        
        # 1. Try LLM extraction
        llm_prov = settings.llm_provider
        if not llm_prov:
            if settings.google_api_key:
                llm_prov = "gemini"
            elif settings.openai_api_key:
                llm_prov = "openai"
            elif settings.ollama_base_url:
                llm_prov = "ollama"
                
        if llm_prov:
            llm_response = ""
            try:
                if llm_prov == "ollama" and settings.ollama_base_url:
                    from backend.app.services.summarizer import call_ollama
                    llm_response = await call_ollama(prompt, "You are a border security intelligence editor.")
                elif llm_prov == "openai" and settings.openai_api_key:
                    from backend.app.services.summarizer import call_openai
                    llm_response = await call_openai(prompt, "You are a border security intelligence editor.")
                elif llm_prov == "gemini" and settings.google_api_key:
                    from backend.app.services.summarizer import call_gemini
                    llm_response = await call_gemini(prompt, "You are a border security intelligence editor.")
            except Exception as e:
                logger.warning(f"[Classifier] LLM extraction call failed: {e}")
                
            if llm_response:
                try:
                    match = re.search(r"\{.*?\}", llm_response, re.DOTALL)
                    if match:
                        parsed = json.loads(match.group(0))
                        required_keys = ["clean_title", "summary", "location", "category", "impact", "source_domain"]
                        if all(k in parsed for k in required_keys):
                            # Map clean output properties to DB fields
                            impact_map = {
                                "high": "High Impact", "medium": "Medium Impact", "low": "Normal Impact",
                                "normal": "Normal Impact"
                            }
                            category_map = {
                                "military": "Military & Defense",
                                "diplomacy": "Political & Diplomatic",
                                "infrastructure": "Military & Defense",
                                "trade": "Economic & Financial",
                                "cyber": "Technology & Cyber"
                            }
                            
                            entities_list = parsed.get("entities") or []
                            if not isinstance(entities_list, list):
                                entities_list = [str(entities_list)]
                            else:
                                entities_list = [str(x) for x in entities_list]
                                
                            extracted_data = {
                                "title": parsed["clean_title"],
                                "sector": parsed["location"],
                                "department": category_map.get(parsed["category"].lower(), "Political & Diplomatic"),
                                "impact_level": impact_map.get(parsed["impact"].lower(), "Normal Impact"),
                                "tactical_summary": parsed["summary"],
                                "entities": entities_list,
                                "action_type": "Surveillance" # Default fallback action
                            }
                            
                            # Heuristically classify action type for DB
                            sum_lower = parsed["summary"].lower()
                            if any(k in sum_lower for k in ["drill", "exercise", "maneuver"]):
                                extracted_data["action_type"] = "Drill"
                            elif any(k in sum_lower for k in ["buildup", "deploy", "movement", "transfer"]):
                                extracted_data["action_type"] = "Deployment"
                            elif any(k in sum_lower for k in ["clash", "skirmish", "incident", "fired"]):
                                extracted_data["action_type"] = "Border Incident"
                            elif any(k in sum_lower for k in ["summit", "meet", "talks", "diplomatic"]):
                                extracted_data["action_type"] = "Diplomatic Meeting"
                            elif any(k in sum_lower for k in ["road", "highway", "bridge", "port", "construction"]):
                                extracted_data["action_type"] = "Infrastructure"
                                
                            logger.info(f"[Classifier] Production Intelligence Extractor successful: {parsed['clean_title']}")
                except Exception as parse_err:
                    logger.warning(f"[Classifier] Failed to parse LLM response: {parse_err}")

        # 2. Rule-Based Fallback Heuristic
        if not extracted_data:
            logger.info(f"[Classifier] Running clean rule-based heuristic extraction fallback for: {title}")
            
            cleaned = clean_news_output(title, content)
            clean_title = cleaned["title"]
            tactical_summary = cleaned["summary"]
            
            words = clean_title.split()
            if len(words) > 10:
                clean_title = " ".join(words[:10]) + "..."
            if not clean_title or clean_title.lower() == "untitled":
                clean_title = f"Security Update near {country_name} border"

            impact_val, dept_val = self.classify(title, content)

            sector_name = f"{country_name} Border"
            content_lower = content.lower()
            if country_code == "CN":
                if "galwan" in content_lower: sector_name = "Galwan Valley (LAC)"
                elif "doklam" in content_lower: sector_name = "Doklam Sector (LAC)"
                elif "depsang" in content_lower: sector_name = "Depsang Plains (LAC)"
                elif "arunachal" in content_lower: sector_name = "Arunachal Sector (LAC)"
                elif "chumbi" in content_lower: sector_name = "Chumbi Valley (LAC)"
                elif "siliguri" in content_lower: sector_name = "Siliguri Corridor"
                elif "ladakh" in content_lower: sector_name = "Eastern Ladakh (LAC)"
                else: sector_name = "Northern Sector (LAC)"
            elif country_code == "PK":
                if "kashmir" in content_lower: sector_name = "Kashmir Sector (LOC)"
                elif "gwadar" in content_lower: sector_name = "Gwadar Port Sector"
                elif "siachen" in content_lower: sector_name = "Siachen Glacier (LOC)"
                elif "creek" in content_lower: sector_name = "Sir Creek Sector"
                else: sector_name = "Western Sector (LOC)"
            elif country_code == "AF":
                sector_name = "Khyber Pass Sector"
            elif country_code == "MM":
                sector_name = "Southeastern Land Boundary"
            elif country_code == "BD":
                sector_name = "Eastern Sector border"
            elif country_code == "LK":
                sector_name = "Palk Strait Sector"
            elif country_code == "MV":
                sector_name = "Indian Ocean Maldives Exclusive Zone"
            elif country_code == "IN":
                if "lac" in content_lower: sector_name = "LAC Frontier"
                elif "loc" in content_lower: sector_name = "LOC Frontier"
                else: sector_name = "Border Defense Zone"

            entity_list = []
            known_entities = [
                "PLA", "IAF", "Indian Army", "Pakistan Army", "Su-30MKI", "Rafale",
                "Western Theater Command", "Northern Command", "LOC", "LAC", "UAV", "drone",
                "Taliban", "Border Security Force", "BSF", "Coast Guard", "Ministry of Defense"
            ]
            for ent in known_entities:
                if re.search(r'\b' + re.escape(ent) + r'\b', content, re.IGNORECASE):
                    entity_list.append(ent)
            matches = re.findall(r'\b[A-Z][a-zA-Z0-9]{2,}\b', content)
            for m in matches:
                if m not in entity_list and m not in ("The", "And", "For", "This", country_name):
                    entity_list.append(m)
                if len(entity_list) >= 8:
                    break
            
            action_type = "Surveillance"
            if any(k in content_lower for k in ["drill", "exercise", "maneuver", "tactical training"]):
                action_type = "Drill"
            elif any(k in content_lower for k in ["buildup", "deploy", "movement", "transfer", "convoy"]):
                action_type = "Deployment"
            elif any(k in content_lower for k in ["clash", "skirmish", "incident", "dispute", "standoff", "fired"]):
                action_type = "Border Incident"
            elif any(k in content_lower for k in ["summit", "meet", "talks", "diplomatic", "bilateral"]):
                action_type = "Diplomatic Meeting"
            elif any(k in content_lower for k in ["road", "highway", "bridge", "infrastructure", "port", "construction"]):
                action_type = "Infrastructure"
            elif any(k in content_lower for k in ["radar", "satellite", "uav", "drone", "patrol", "reconnaissance"]):
                action_type = "Surveillance"

            extracted_data = {
                "title": clean_title,
                "sector": sector_name,
                "department": dept_val,
                "impact_level": impact_val,
                "tactical_summary": tactical_summary,
                "entities": entity_list[:6],
                "action_type": action_type
            }

        return extracted_data

    async def rewrite_headline_to_tactical_brief(self, headline: str, country_name: str) -> str:
        if settings.ollama_base_url:
            try:
                from backend.app.services.summarizer import call_ollama
                prompt = (
                    f"Rewrite this news headline into a 'Tactical Brief'. It should be a single, short, clear, "
                    f"active-voice summary of the action and location (e.g. 'Troop movement detected near Tawang Sector'). "
                    f"Do not use conversational filler, do not use quotes, and do not use the words 'Tactical Brief'. "
                    f"Output only the rewritten headline.\n\n"
                    f"HEADLINE: {headline}\n"
                    f"COUNTRY: {country_name}"
                )
                rewritten = await call_ollama(prompt, "You are a concise military and news editor.")
                if rewritten and rewritten.strip():
                    clean_brief = rewritten.strip().replace('"', '').replace("'", "")
                    if clean_brief.lower().startswith("tactical brief:"):
                        clean_brief = clean_brief[15:].strip()
                    return clean_brief
            except Exception as e:
                logger.warning(f"[Classifier] Ollama headline rewrite failed: {e}")
        # Fallback to local rewrite if Ollama is unavailable
        return f"Tactical Brief: {headline}"

    async def route_article(self, article_data: Dict[str, Any]) -> Tuple[str, str]:
        title = article_data.get("title", "")
        content = article_data.get("content", "")
        cc = article_data.get("country_code", "")
        
        intel = await self.extract_intelligence(title, content, cc)
        
        # Override original placeholder summaries and headlines with structured, clean data
        article_data["title"] = intel["title"]
        
        # Generate the Tactical Brief using Ollama
        from backend.app.services.ingestion import ISO_COUNTRIES
        country_name = ISO_COUNTRIES.get(cc, cc)
        tactical_brief = await self.rewrite_headline_to_tactical_brief(intel["title"], country_name)
        article_data["headline"] = tactical_brief
        
        article_data["summary"] = intel["tactical_summary"]
        article_data["impact_level"] = intel["impact_level"]
        article_data["department"] = intel["department"]
        article_data["sector"] = intel["sector"]
        article_data["entities"] = intel["entities"]
        article_data["action_type"] = intel["action_type"]
        
        return intel["impact_level"], intel["department"]


    async def is_duplicate(self, db: AsyncSession, article: Dict[str, Any]) -> bool:
        url = article.get("url")
        if not url:
            return False
            
        # 1. Try Redis deduplication if enabled
        # NOTE: We only CHECK here — the key is set AFTER successful DB persist
        # (see confirm_persisted() below) to prevent false-positive dedup
        # when the downstream DB insert fails.
        redis_conn = await _get_redis()
        if redis_conn:
            url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
            key = f"drishya:dedup:{url_hash}"
            try:
                exists = await redis_conn.exists(key)
                if exists:
                    return True
            except Exception as e:
                logger.debug(f"[Classifier] Redis dedup check failed: {e}")

        # 2. Database fallback check
        try:
            stmt = select(Article.id).where(Article.url == url)
            res = await db.execute(stmt)
            if res.scalar_one_or_none() is not None:
                return True
        except Exception as e:
            logger.warning(f"[Classifier] DB duplicate check failed: {e}")

        # 3. Database-level Fuzzy Title Deduplication Check (last 24 hours)
        title = article.get("title")
        if title:
            try:
                from datetime import datetime, timezone, timedelta
                import difflib
                one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
                
                # Fetch recent titles from the database
                stmt = select(Article.title).where(Article.published_at >= one_day_ago)
                res = await db.execute(stmt)
                recent_titles = res.scalars().all()
                
                normalized_title = "".join(c for c in title.lower() if c.isalnum())
                for r_title in recent_titles:
                    r_norm = "".join(c for c in r_title.lower() if c.isalnum())
                    if normalized_title == r_norm or difflib.SequenceMatcher(None, title.lower(), r_title.lower()).ratio() > 0.85:
                        logger.info(f"[Classifier] Dropping fuzzy title duplicate in DB: '{title}' matched '{r_title}'")
                        return True
            except Exception as e:
                logger.warning(f"[Classifier] Fuzzy title duplicate check failed: {e}")

        return False

    async def _publish_realtime(self, payload: Dict[str, Any]) -> None:
        memory_stream.publish(payload)
        redis_conn = await _get_redis()
        if not redis_conn:
            return
        try:
            await redis_conn.publish("live_stream", json.dumps(payload))
        except Exception as exc:
            logger.debug("[Classifier] Live stream publish skipped: %s", exc)

    async def confirm_persisted(self, url: str) -> None:
        """Confirm a URL has been persisted to DB — only then set the Redis dedup key.
        This prevents the optimistic-dedup bug where articles are marked 'seen'
        before they actually make it into the database.
        """
        redis_conn = await _get_redis()
        if not redis_conn or not url:
            return
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        try:
            await redis_conn.setex(f"drishya:dedup:{url_hash}", 604800, "1")
        except Exception:
            pass

    async def save_article(self, db: AsyncSession, article_data: Dict[str, Any], embedding: Optional[List[float]] = None) -> bool:
        return (await self.persist_high_impact_batch(db, [article_data], embeddings=[embedding] if embedding else None)) > 0

    async def persist_high_impact_batch(
        self,
        db: AsyncSession,
        articles: List[Dict[str, Any]],
        embeddings: Optional[List[Optional[List[float]]]] = None,
    ) -> int:
        inserted = 0
        rows: List[Article] = []
        for idx, article_data in enumerate(articles):
            impact, dept = await self.route_article(article_data)
            
            source = article_data.get("source")
            reputation = compute_source_reputation(source)
            confidence = article_data.get("confidence_score") or 0.98
            
            # Lazy Embedding generation only for High and Medium impact articles
            is_high_or_medium = impact in ("High Impact", "Medium Impact")
            cand_embedding = None
            if is_high_or_medium:
                if embeddings and idx < len(embeddings) and embeddings[idx] is not None:
                    cand_embedding = embeddings[idx]
                else:
                    try:
                        text_to_encode = f"{article_data['title']} {article_data.get('summary', '') or article_data['content'][:300]}"
                        computed_embs = await get_embeddings_cached([text_to_encode])
                        cand_embedding = computed_embs[0] if computed_embs else None
                    except Exception as e:
                        logger.error(f"[Classifier] Failed to compute single embedding: {e}")

            # Check near-duplicates inside same country bucket
            # On Postgres with pgvector: use efficient SQL nearest-neighbor query
            # On SQLite: fall back to Python brute-force cosine similarity
            near_dup_found = False
            if cand_embedding:
                try:
                    from sqlalchemy import select
                    country_code = article_data["country_code"]
                    
                    # Try pgvector SQL path first (Postgres)
                    is_postgres = db.bind.dialect.name == "postgresql" if hasattr(db, 'bind') else False
                    if is_postgres and HAS_PGVECTOR:
                        try:
                            from pgvector.sqlalchemy import Vector
                            stmt_nn = (
                                select(Article)
                                .where(Article.country_code == country_code)
                                .where(Article.embedding.isnot(None))
                                .order_by(Article.embedding.cosine_distance(cand_embedding))
                                .limit(5)
                            )
                            res_nn = await db.execute(stmt_nn)
                            for existing_art in res_nn.scalars().all():
                                # pgvector returns distance; we need to check similarity
                                # cosine_distance = 1 - cosine_similarity
                                ex_vec_val = existing_art.embedding
                                if not ex_vec_val:
                                    continue
                                if isinstance(ex_vec_val, str):
                                    ex_vec_list = json.loads(ex_vec_val)
                                elif isinstance(ex_vec_val, (list, tuple)):
                                    ex_vec_list = list(ex_vec_val)
                                else:
                                    ex_vec_list = list(ex_vec_val)
                                if len(ex_vec_list) != len(cand_embedding):
                                    continue
                                import numpy as np
                                cand_vec = np.array(cand_embedding, dtype=np.float32)
                                ex_vec = np.array(ex_vec_list, dtype=np.float32)
                                cand_norm = np.linalg.norm(cand_vec)
                                ex_norm = np.linalg.norm(ex_vec)
                                if cand_norm > 0 and ex_norm > 0:
                                    sim = float(np.dot(cand_vec / cand_norm, ex_vec / ex_norm))
                                    if sim > 0.86:
                                        near_dup_found = True
                                        break
                        except Exception as pg_err:
                            logger.debug(f"[Classifier] pgvector near-dup failed, falling back to numpy: {pg_err}")
                            is_postgres = False  # fall through to numpy path

                    # SQLite fallback: Python brute-force cosine similarity
                    if not near_dup_found and not is_postgres:
                        import numpy as np
                        stmt_ex = select(Article).where(Article.country_code == country_code)
                        res_ex = await db.execute(stmt_ex)
                        existing_articles = res_ex.scalars().all()

                        cand_vec = np.array(cand_embedding, dtype=np.float32)
                        cand_norm = np.linalg.norm(cand_vec)
                        if cand_norm > 0:
                            cand_vec_norm = cand_vec / cand_norm
                            for existing_art in existing_articles:
                                ex_vec_val = existing_art.embedding
                                if not ex_vec_val:
                                    continue

                                if isinstance(ex_vec_val, str):
                                    try:
                                        ex_vec_list = json.loads(ex_vec_val)
                                    except Exception:
                                        continue
                                elif isinstance(ex_vec_val, (list, tuple)):
                                    ex_vec_list = ex_vec_val
                                else:
                                    try:
                                        ex_vec_list = list(ex_vec_val)
                                    except Exception:
                                        continue

                                if len(ex_vec_list) != len(cand_vec):
                                    continue

                                ex_vec = np.array(ex_vec_list, dtype=np.float32)
                                ex_norm = np.linalg.norm(ex_vec)
                                if ex_norm <= 0:
                                    continue

                                ex_vec_norm = ex_vec / ex_norm
                                similarity = float(np.dot(cand_vec_norm, ex_vec_norm))
                                if similarity > 0.86:
                                    near_dup_found = True

                                    # Lead Article Election
                                    def get_rep_priority(rep: str) -> int:
                                        if rep == "Verified Source":
                                            return 3
                                        if rep == "Developing":
                                            return 2
                                        if rep == "Unverified":
                                            return 1
                                        return 0

                                    cand_pri = get_rep_priority(reputation)
                                    ex_pri = get_rep_priority(existing_art.source_reputation)
                                
                                # Prepare also_reported_by lists
                                existing_also_reported_by = []
                                if existing_art.also_reported_by:
                                    try:
                                        existing_also_reported_by = json.loads(existing_art.also_reported_by)
                                        if not isinstance(existing_also_reported_by, list):
                                            existing_also_reported_by = []
                                    except Exception:
                                        existing_also_reported_by = []
                                
                                if cand_pri > ex_pri:
                                    # Candidate is elected as the lead article!
                                    # Demote existing URL to also_reported_by
                                    if existing_art.url not in existing_also_reported_by:
                                        existing_also_reported_by.append(existing_art.url)
                                        
                                    # Update existing article row with candidate data
                                    existing_art.title = article_data["title"]
                                    existing_art.headline = article_data.get("headline") or article_data["title"]
                                    existing_art.summary = article_data.get("summary")
                                    existing_art.content = article_data["content"]
                                    existing_art.url = article_data["url"]
                                    existing_art.source = source
                                    existing_art.source_reputation = reputation
                                    existing_art.embedding = json.dumps(cand_embedding) if isinstance(existing_art.embedding, str) else cand_embedding
                                    existing_art.sector = article_data.get("sector")
                                    existing_art.entities = json.dumps(article_data.get("entities") or [])
                                    existing_art.action_type = article_data.get("action_type")
                                    
                                    # Boost confidence
                                    current_conf = existing_art.confidence_score or 0.98
                                    existing_art.confidence_score = min(1.00, current_conf + 0.05)
                                    existing_art.also_reported_by = json.dumps(existing_also_reported_by)
                                    
                                    db.add(existing_art)
                                    logger.info(f"[Classifier] Near-duplicate detected. New candidate {article_data['url']} elected as lead. Old lead {existing_art.url} demoted to also_reported_by list.")
                                else:
                                    # Existing remains lead article
                                    # Append candidate URL to also_reported_by
                                    if article_data["url"] not in existing_also_reported_by:
                                        existing_also_reported_by.append(article_data["url"])
                                        
                                    existing_art.also_reported_by = json.dumps(existing_also_reported_by)
                                    
                                    # Boost confidence
                                    current_conf = existing_art.confidence_score or 0.98
                                    existing_art.confidence_score = min(1.00, current_conf + 0.05)
                                    
                                    db.add(existing_art)
                                    logger.info(f"[Classifier] Near-duplicate detected. Existing lead {existing_art.url} remains. New candidate {article_data['url']} appended to also_reported_by list.")
                                
                                metrics.state.dedup_near_duplicate_dropped_total += 1
                                break
                except Exception as ex_err:
                    logger.error(f"[Classifier] Near-duplicate check error: {ex_err}")
                    
            if near_dup_found:
                continue

            # Send real-time updates for all ingested articles
            await self._publish_realtime(
                {
                    "title": article_data["title"],
                    "headline": article_data.get("headline") or article_data["title"],
                    "summary": article_data.get("summary") or article_data["title"],
                    "content": article_data["content"],
                    "url": article_data["url"],
                    "source": source,
                    "country_code": article_data["country_code"],
                    "published_at": article_data["published_at"].isoformat() if isinstance(article_data["published_at"], datetime) else str(article_data["published_at"]),
                    "impact_level": impact,
                    "department": dept,
                    "source_reputation": reputation,
                    "confidence_score": confidence,
                    "sector": article_data.get("sector"),
                    "entities": article_data.get("entities") or [],
                    "action_type": article_data.get("action_type"),
                    "also_reported_by": article_data.get("also_reported_by") or []
                }
            )

            summary_text = article_data.get("summary")
            if not summary_text or not summary_text.strip():
                content_text = article_data.get("content") or ""
                summary_text = content_text.strip()[:180] + "..." if len(content_text.strip()) > 180 else content_text.strip()
            if not summary_text:
                summary_text = "Tactical intelligence briefing restricted."

            # Build row
            entities_json = json.dumps(article_data.get("entities") or [])
            also_rep_json = json.dumps(article_data.get("also_reported_by") or [])

            rows.append(
                Article(
                    title=article_data["title"],
                    headline=article_data.get("headline") or article_data["title"],
                    summary=summary_text,
                    content=article_data["content"],
                    url=article_data["url"],
                    source=source,
                    country_code=article_data["country_code"],
                    published_at=article_data["published_at"],
                    impact_level=impact,
                    department=dept,
                    embedding=(cand_embedding if cand_embedding else None),
                    source_reputation=reputation,
                    confidence_score=confidence,
                    sector=article_data.get("sector"),
                    entities=entities_json,
                    action_type=article_data.get("action_type"),
                    also_reported_by=also_rep_json,
                )
            )


        if rows:
            # Remove any records that already exist in the database by URL to avoid batch integrity errors.
            urls = [row.url for row in rows if row.url]
            if urls:
                try:
                    existing_result = await db.execute(select(Article.url).where(Article.url.in_(urls)))
                    existing_urls = {row[0] for row in existing_result.all()}
                except Exception:
                    existing_urls = set()
            else:
                existing_urls = set()

            rows = [row for row in rows if row.url and row.url not in existing_urls]

            if rows:
                try:
                    from sqlalchemy import insert
                    mappings = [
                        {
                            "id": row.id,
                            "title": row.title,
                            "headline": row.headline,
                            "summary": row.summary,
                            "content": row.content,
                            "url": row.url,
                            "source": row.source,
                            "country_code": row.country_code,
                            "published_at": row.published_at,
                            "impact_level": row.impact_level,
                            "department": row.department,
                            "embedding": row.embedding,
                            "source_reputation": row.source_reputation,
                            "confidence_score": row.confidence_score,
                            "sector": row.sector,
                            "entities": row.entities,
                            "action_type": row.action_type,
                            "also_reported_by": row.also_reported_by,
                            "created_at": row.created_at,
                        }
                        for row in rows
                    ]
                    await db.execute(insert(Article), mappings)
                    await db.commit()
                    inserted = len(rows)
                    metrics.state.classification_batches_total += 1
                    await _bump_archive_version()
                    # Confirm dedup keys in Redis ONLY after successful DB persist
                    for row in rows:
                        if row.url:
                            await self.confirm_persisted(row.url)
                    logger.info("[Classifier] Batch saved %s high-impact articles.", inserted)
                except Exception as exc:
                    await db.rollback()
                    logger.warning("[Classifier] Batch insert skipped or partially failed: %s", exc)
                    inserted = 0
            else:
                logger.debug("[Classifier] No new high-impact articles to save after deduplication.")

        return inserted


async def classify_and_store_batch(
    db: AsyncSession,
    articles: List[Dict[str, Any]],
    embeddings: Optional[List[Optional[List[float]]]] = None,
) -> Dict[str, int]:
    classifier = ImpactClassifier()
    high_impact = 0
    streamed = 0
    skipped_duplicates = 0
    unique_articles: List[Dict[str, Any]] = []
    unique_embeddings: List[Optional[List[float]]] = []

    for index, article in enumerate(articles):
        if await classifier.is_duplicate(db, article):
            skipped_duplicates += 1
            metrics.state.ingestion_duplicates_total += 1
            continue
        unique_articles.append(article)
        if embeddings:
            unique_embeddings.append(embeddings[index] if index < len(embeddings) else None)

    # Phase 1: Full-Text scraping for unique articles
    if unique_articles:
        import httpx
        from backend.app.services.ingestion import scrape_full_text
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                scrape_tasks = [scrape_full_text(client, art.get("url")) for art in unique_articles]
                scraped_contents = await asyncio.gather(*scrape_tasks)
            for idx, content in enumerate(scraped_contents):
                if content:
                    unique_articles[idx]["content"] = content
        except Exception as e:
            logger.error(f"[Classifier] Full-text scraping error: {e}")

    high_impact = await classifier.persist_high_impact_batch(
        db,
        unique_articles,
        embeddings=unique_embeddings if embeddings else None,
    )
    streamed = len(unique_articles) - high_impact
    metrics.state.ingestion_articles_total += len(unique_articles)

    # ── Cross-Corroboration Pipeline ──
    try:
        from backend.app.services.credibility import corroboration_engine
        for article in unique_articles:
            corroboration_engine.process_article(article)
        logger.info(
            "[Classifier] Corroboration engine processed %d articles, "
            "verified stories: %d",
            len(unique_articles),
            len(corroboration_engine.get_verified_stories()),
        )
    except Exception as e:
        logger.error(f"[Classifier] Corroboration pipeline error: {e}")

    return {
        "processed": len(unique_articles),
        "high_impact": high_impact,
        "streamed": streamed,
        "duplicates": skipped_duplicates,
    }

