import json
import logging
from datetime import datetime, timezone
import uuid
from typing import AsyncGenerator, List, Optional
from sqlalchemy import Column, String, Text, DateTime, TypeDecorator, select, func, Float, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from backend.app.config import settings

logger = logging.getLogger("drishya.database")
logging.basicConfig(level=logging.INFO)

# Base class
class Base(DeclarativeBase):
    pass

# Custom TypeDecorator to store vectors safely as JSON/text in SQLite
class JSONVector(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, list):
            return json.dumps(value)
        return str(value)

    def process_result_value(self, value, dialect) -> Optional[List[float]]:
        if value is None:
            return None
        try:
            return json.loads(value)
        except Exception:
            # handle standard string array representation e.g. '[0.1, 0.2]'
            try:
                cleaned = value.strip("[]").split(",")
                return [float(x) for x in cleaned if x.strip()]
            except Exception:
                return None

# Normalize database URLs for async SQLAlchemy compatibility
database_url = settings.database_url or ""
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)

# Dynamic detection of pgvector capability
DATABASE_IS_POSTGRES = database_url.startswith("postgresql+asyncpg://")

try:
    if DATABASE_IS_POSTGRES:
        from pgvector.sqlalchemy import Vector

        VectorType = Vector(settings.embedding_dimensions)
        logger.info("[Database] Using pgvector for database embeddings.")
    else:
        VectorType = JSONVector()
        logger.info("[Database] Using JSONVector for database embeddings (SQLite/Fallback).")
except ImportError:
    VectorType = JSONVector()
    logger.warning("[Database] pgvector library not installed or not supported. Falling back to JSONVector.")

# Article Model
class Article(Base):
    __tablename__ = "high_impact_articles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    country_code: Mapped[str] = mapped_column(String(3), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    impact_level: Mapped[str] = mapped_column(String(32), default="High Impact")
    department: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding = Column(VectorType, nullable=True)
    source_reputation: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="Unrated")
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0.98)
    sector: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    entities: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # JSON list
    action_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    also_reported_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # JSON list
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# Archive Summary Model
class ArchiveSummary(Base):
    __tablename__ = "archive_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    timeframe: Mapped[str] = mapped_column(String(8), unique=True, nullable=False) # '1M', '6M', '1Y'
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

# User Model
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(64), default="Operator")

# Shared Note Model
class SharedNote(Base):
    __tablename__ = "shared_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(128), default="Strategic Command")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

# Note Version Model
class NoteVersion(Base):
    __tablename__ = "note_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    note_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

# Verified Story Model (Cross-Corroboration Persistence)
class VerifiedStory(Base):
    __tablename__ = "verified_stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    story_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    headline: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="single_source")  # unverified, single_source, cross_referenced, verified
    corroboration_score: Mapped[float] = mapped_column(Float, default=0.0)
    unique_source_count: Mapped[int] = mapped_column(default=0)
    sources_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list of sources
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# Alert Rule Model
class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    keywords: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    country_code: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    minimum_risk: Mapped[str] = mapped_column(String(64), default="Low")
    channels: Mapped[str] = mapped_column(Text, nullable=False, default='["in_app"]')
    frequency: Mapped[str] = mapped_column(String(64), default="immediate")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

# Engine initialization with automatic fallback
engine = None
SessionLocal = None

async def init_db_engine():
    global engine, SessionLocal
    
    db_url = database_url

    # Try Postgres with connection pooling
    if DATABASE_IS_POSTGRES:
        try:
            logger.info(f"[Database] Attempting connection to PostgreSQL at {db_url.split('@')[-1]}")
            engine = create_async_engine(
                db_url,
                echo=False,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
            # Try to connect
            async with engine.connect() as conn:
                await conn.execute(select(1))
            SessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)
            logger.info("[Database] Connected to PostgreSQL with connection pool (pool_size=10, max_overflow=20).")
            return
        except Exception as e:
            logger.warning(f"[Database] PostgreSQL connection failed: {e}. Falling back to SQLite.")

    # SQLite fallback with optimized settings
    sqlite_path = settings.sqlite_url
    logger.info(f"[Database] Initializing SQLite local fallback at {sqlite_path}")
    engine = create_async_engine(
        sqlite_path,
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    SessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)

async def seed_data_if_empty(session: AsyncSession):
    # Check if empty
    result = await session.execute(select(func.count(Article.id)))
    count = result.scalar()
    if count >= 2000:
        return
        
    logger.info("[Database] Seeding database with high density high impact realistic articles for all countries...")
    
    # Delete old seeding to ensure clean, high-density data without duplicate key errors
    from sqlalchemy import delete
    await session.execute(delete(Article))
    await session.commit()
    
    from pathlib import Path
    
    countries_map = {}
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    COUNTRIES_JSON_PATH = PROJECT_ROOT / "node_modules" / "world-countries" / "countries.json"
    if COUNTRIES_JSON_PATH.exists():
        try:
            with open(COUNTRIES_JSON_PATH, "r", encoding="utf-8") as f:
                countries_data = json.load(f)
                for c in countries_data:
                    cca2 = c.get("cca2", "").upper()
                    name = c.get("name", {}).get("common", "")
                    if cca2 and name:
                        countries_map[name] = cca2
        except Exception as e:
            logger.error(f"[Database] Failed to load countries.json: {e}")
            
    if not countries_map:
        countries_map = {
            "China": "CN", "Pakistan": "PK", "Afghanistan": "AF", "Bangladesh": "BD",
            "Myanmar": "MM", "Nepal": "NP", "Bhutan": "BT", "Sri Lanka": "LK", "Maldives": "MV",
            "India": "IN", "United States": "US", "Russia": "RU", "Iran": "IR", "Israel": "IL",
            "Taiwan": "TW", "Ukraine": "UA", "Japan": "JP", "South Korea": "KR", "United Kingdom": "GB",
            "France": "FR", "Germany": "DE", "Syria": "SY", "Yemen": "YE", "North Korea": "KP",
            "Saudi Arabia": "SA", "Iraq": "IQ", "Libya": "LY", "Somalia": "SO", "Sudan": "SD",
            "Venezuela": "VE"
        }

    departments = [
        "Military & Defense",
        "Economic & Financial",
        "Social Affairs & Welfare",
        "Political & Diplomatic",
        "Technology & Cyber"
    ]
    
    import random
    
    templates = {
        "Military & Defense": [
            "Joint safety drills and troop movements completed near the border with {country}",
            "Special guard units conduct mountain patrol sweeps near the {country} boundary line",
            "Guard post upgrades and defense preparations increase along the {country} border routes",
            "Border guards establish new watch posts near the {country} line"
        ],
        "Economic & Financial": [
            "New border highway and trade route construction expands transit with {country}",
            "Sea port upgrades secure trade shipping lanes near {country}",
            "Cooperative funding deal signed to build trade hubs with {country}",
            "Customs clearing posts upgrade capacity at the {country} border crossings",
            "Trade flows grow as new shared agreements ease tax restrictions with {country}"
        ],
        "Social Affairs & Welfare": [
            "Local housing and community support programs expand near {country} border",
            "Medical aid stations established to assist crossing points with {country}",
            "Disagreements between local citizens and patrols are reported near {country}",
            "Emergency food supply distribution sweeps verify secure conditions along the {country} line",
            "Cultural exchanges aim to foster peaceful border relations with {country}"
        ],
        "Political & Diplomatic": [
            "High-level security meeting sets border tax collection rules with {country}",
            "Diplomatic teams sign a cooperative plan for shared checkpoint rules with {country}",
            "Commanders verify border marking updates during meetings with {country}",
            "Joint command coordination center launched to monitor border developments with {country}",
            "Peace talks progress as senior officials schedule discussions with {country}"
        ],
        "Technology & Cyber": [
            "Computer security center blocks hacking attempts targeting systems near {country}",
            "Satellite tracking stations upgrade equipment to map communication signals near {country}",
            "New data analysis platforms deploy along key sectors near the {country} border",
            "Mobile network signals established to maintain active communications with {country}",
            "Signal blockers are tested along the {country} border"
        ]
    }
    
    from datetime import timedelta
    
    articles_to_create = []
    texts_to_embed = []
    
    for country, cc in countries_map.items():
        for idx, dept in enumerate(departments):
            for art_num in range(2):
                title_templates = templates[dept]
                temp = random.choice(title_templates)
                title = temp.format(country=country) + f" (News Alert #{100 + idx * 17 + art_num * 13 + random.randint(1, 9)})"
                summary = f"A short update on local {dept.lower()} activity near the {country} border."
                content = (
                    f"A verified update details recent developments near the {country} border. "
                    f"Local teams report standard activity, and the area remains under regular watch. "
                    f"More updates will follow as they are reported."
                )
                pub_time = datetime.now(timezone.utc) - timedelta(minutes=random.randint(1, 55) + art_num * 45)
                
                sources_map = {
                    "Military & Defense": ["reuters.com", "apnews.com", "aljazeera.com"],
                    "Economic & Financial": ["bloomberg.com", "reuters.com", "dw.com"],
                    "Social Affairs & Welfare": ["bbc.com", "france24.com", "dw.com"],
                    "Political & Diplomatic": ["theguardian.com", "nytimes.com", "aljazeera.com"],
                    "Technology & Cyber": ["techcrunch.com", "wired.com", "theverge.com"]
                }
                src_list = sources_map.get(dept, ["bbc.com"])
                source = random.choice(src_list)

                search_query = f"{country}+{dept.split()[0]}"
                if source == "reuters.com":
                    url = f"https://www.reuters.com/site-search/?query={search_query}#art-{idx}-{art_num}"
                elif source == "apnews.com":
                    url = f"https://apnews.com/search?q={search_query}#art-{idx}-{art_num}"
                elif source == "aljazeera.com":
                    url = f"https://www.aljazeera.com/search/{search_query}#art-{idx}-{art_num}"
                elif source == "bloomberg.com":
                    url = f"https://www.bloomberg.com/search?query={search_query}#art-{idx}-{art_num}"
                elif source == "dw.com":
                    url = f"https://www.dw.com/en/search?q={search_query}#art-{idx}-{art_num}"
                elif source == "bbc.com":
                    url = f"https://www.bbc.com/search?q={search_query}#art-{idx}-{art_num}"
                elif source == "france24.com":
                    url = f"https://www.france24.com/en/search?q={search_query}#art-{idx}-{art_num}"
                elif source == "theguardian.com":
                    url = f"https://www.theguardian.com/search?q={search_query}#art-{idx}-{art_num}"
                elif source == "nytimes.com":
                    url = f"https://www.nytimes.com/search?query={search_query}#art-{idx}-{art_num}"
                elif source == "techcrunch.com":
                    url = f"https://techcrunch.com/?s={search_query}#art-{idx}-{art_num}"
                elif source == "wired.com":
                    url = f"https://www.wired.com/search?q={search_query}#art-{idx}-{art_num}"
                elif source == "theverge.com":
                    url = f"https://www.theverge.com/search?q={search_query}#art-{idx}-{art_num}"
                else:
                    url = f"https://www.google.com/search?q={source}+{search_query}#art-{idx}-{art_num}"

                if idx == 0:
                    impact = "High Impact"
                elif idx in (1, 3):
                    impact = "Medium Impact"
                else:
                    impact = "Normal Impact"

                articles_to_create.append({
                    "title": title,
                    "headline": title,
                    "summary": summary,
                    "content": content,
                    "url": url,
                    "source": source,
                    "country_code": cc,
                    "published_at": pub_time,
                    "impact_level": impact,
                    "department": dept
                })
                texts_to_embed.append(f"{title} {summary}")

    # Generate embeddings in batch
    try:
        from backend.app.services.classifier import get_embeddings_cached
        embeddings = await get_embeddings_cached(texts_to_embed)
    except Exception as e:
        logger.error(f"[Database] Failed to compute embeddings during seeding: {e}")
        embeddings = [None] * len(articles_to_create)

    is_postgres = session.bind.dialect.name == "postgresql"
    seeded_count = 0
    for art_dict, emb in zip(articles_to_create, embeddings):
        db_article = Article(
            title=art_dict["title"],
            headline=art_dict["headline"],
            summary=art_dict["summary"],
            content=art_dict["content"],
            url=art_dict["url"],
            source=art_dict["source"],
            country_code=art_dict["country_code"],
            published_at=art_dict["published_at"],
            impact_level=art_dict["impact_level"],
            department=art_dict["department"]
        )
        if emb:
            if is_postgres:
                db_article.embedding = emb
            else:
                db_article.embedding = json.dumps(emb)
        session.add(db_article)
        seeded_count += 1

    await session.commit()
    logger.info(f"[Database] Seeding finished. Added {seeded_count} articles across {len(countries_map)} countries.")

async def create_tables():
    if engine is None:
        await init_db_engine()
    async with engine.begin() as conn:
        # If postgres, enable pgvector extension
        if engine.url.drivername.startswith("postgresql"):
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            except Exception as e:
                logger.warning(f"[Database] Failed to enable pgvector extension: {e}")
        await conn.run_sync(Base.metadata.create_all)
        
        # Check and run columns migrations dynamically
        # Each column check gets its own transaction so a failed probe
        # doesn't poison the remaining checks (Postgres transaction-abort semantics).
        columns_to_add = [
            ("sector", "VARCHAR(256)"),
            ("entities", "TEXT"),
            ("action_type", "VARCHAR(128)"),
            ("also_reported_by", "TEXT")
        ]
        for col_name, col_type in columns_to_add:
            # Separate connection/transaction per column
            async with engine.begin() as col_conn:
                try:
                    await col_conn.execute(text(f"SELECT {col_name} FROM high_impact_articles LIMIT 1"))
                    continue  # column exists
                except Exception:
                    pass
            async with engine.begin() as col_conn:
                try:
                    await col_conn.execute(text(f"ALTER TABLE high_impact_articles ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                    logger.info(f"[Database] Column '{col_name}' ensured.")
                except Exception as alter_err:
                    logger.error(f"[Database] Failed to add column '{col_name}': {alter_err}")
                    
        # Create performance indexes for common query patterns
        indexes = [
            ("idx_articles_country_code", "high_impact_articles", "country_code"),
            ("idx_articles_published_at", "high_impact_articles", "published_at"),
            ("idx_articles_url", "high_impact_articles", "url"),
            ("idx_articles_impact_level", "high_impact_articles", "impact_level"),
            ("idx_articles_country_published", "high_impact_articles", "country_code, published_at"),
            ("idx_notes_created_at", "shared_notes", "created_at"),
            ("idx_alert_rules_enabled", "alert_rules", "enabled"),
        ]
        for idx_name, table, columns in indexes:
            try:
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})"))
            except Exception as idx_err:
                logger.debug(f"[Database] Index {idx_name} creation skipped: {idx_err}")

        logger.info("[Database] Tables created and migrations checked successfully.")


    # Seed default user accounts in demo mode
    if settings.enable_demo_seed_data:
        async with SessionLocal() as session:
            try:
                import bcrypt
                demo_credentials = [
                    {"username": "admin@intel.local", "role": "Admin", "password": "Admin@2026!"},
                    {"username": "analyst@intel.local", "role": "Analyst", "password": "Analyst@2026!"},
                    {"username": "operator@intel.local", "role": "Operator", "password": "Operator@2026!"},
                ]
                for cred in demo_credentials:
                    check_stmt = select(User).where(User.username == cred["username"])
                    check_res = await session.execute(check_stmt)
                    existing_user = check_res.scalars().first()
                    hashed = bcrypt.hashpw(cred["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                    if not existing_user:
                        user_obj = User(
                            username=cred["username"],
                            password_hash=hashed,
                            role=cred["role"]
                        )
                        session.add(user_obj)
                    else:
                        existing_user.password_hash = hashed
                        existing_user.role = cred["role"]
                await session.commit()
                logger.info("[Database] Demo user accounts seeded successfully.")
            except Exception as e:
                logger.error(f"[Database] Demo user seeding failed: {e}")
        
    # Run seeding if empty and enable_demo_seed_data is True
    if settings.enable_demo_seed_data:
        async with SessionLocal() as session:
            try:
                await seed_data_if_empty(session)
            except Exception as e:
                logger.error(f"[Database] Seeding failed: {e}")

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if SessionLocal is None:
        await init_db_engine()
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
