import json
import logging
from datetime import datetime, timezone
import uuid
from typing import AsyncGenerator, List, Optional
from sqlalchemy import Column, String, Text, DateTime, TypeDecorator, select, func, Float
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

# Engine initialization with automatic fallback
engine = None
SessionLocal = None

async def init_db_engine():
    global engine, SessionLocal
    
    db_url = database_url

    # Try Postgres
    if DATABASE_IS_POSTGRES:
        try:
            logger.info(f"[Database] Attempting connection to PostgreSQL at {db_url.split('@')[-1]}")
            engine = create_async_engine(db_url, echo=False)
            # Try to connect
            async with engine.connect() as conn:
                await conn.execute(select(1))
            SessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)
            logger.info("[Database] Connected to PostgreSQL successfully.")
            return
        except Exception as e:
            logger.warning(f"[Database] PostgreSQL connection failed: {e}. Falling back to SQLite.")

    # SQLite fallback
    sqlite_path = settings.sqlite_url
    logger.info(f"[Database] Initializing SQLite local fallback at {sqlite_path}")
    engine = create_async_engine(sqlite_path, echo=False)
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
            "Verified deployment of active border guards and troop divisions along the {country} frontier",
            "Joint tactical drills and armor maneuvers completed by frontier commands near {country}",
            "Special ops units conduct high-altitude patrol sweeps at the {country} demarcation lines",
            "Bunker reinforcements and artillery defense setups increase along the strategic {country} corridors",
            "Frontier troops establish new high-altitude monitoring outposts near the {country} line"
        ],
        "Economic & Financial": [
            "New border highway and trade route construction expands commercial transit with {country}",
            "Deepwater port infrastructure upgrades secure strategic transport lanes near {country}",
            "Bilateral investment framework signed to fund major logistical hubs with {country}",
            "Customs clearing facilities upgrade processing capacity at {country} border posts",
            "Trade volume increases as new bilateral agreements ease tariff restrictions with {country}"
        ],
        "Social Affairs & Welfare": [
            "Local resettlement and border governance programs expand near {country} frontier",
            "Humanitarian medical aid stations established to assist crossing points with {country}",
            "Friction between civilian border populations and local patrols rises near {country}",
            "Emergency food supply distribution sweeps verify secure conditions along the {country} line",
            "Cultural exchange programs aim to foster peaceful border relations with {country}"
        ],
        "Political & Diplomatic": [
            "High-level security-coordination summit sets border tax collection rules with {country}",
            "Diplomatic delegations sign memorandum for shared checkpoint security with {country}",
            "Commanders verify border demarcation protocol updates during meetings with {country}",
            "Joint command coordination center launched to monitor border developments with {country}",
            "Peace talks progress as senior officials schedule bilateral discussions with {country}"
        ],
        "Technology & Cyber": [
            "National cyber security center blocks major hacking attempts targeting key systems near {country}",
            "Satellite tracking stations upgrade telemetry arrays to map satellite signals near {country}",
            "AI-powered intelligence monitoring platforms deploy along strategic sectors near {country}",
            "Tactical telecom network signals established to maintain active communications with {country}",
            "Electronic warfare divisions deploy signal jammer systems along the {country} border"
        ]
    }
    
    from datetime import timedelta
    
    seeded_count = 0

    for country, cc in countries_map.items():
        for idx, dept in enumerate(departments):
            # Create 1 unique article per department (5 total per country)
            title_templates = templates[dept]
            temp = random.choice(title_templates)
            title = temp.format(country=country) + f" (Intel Alert #{100 + idx * 17 + random.randint(1, 9)})"
            content = (
                f"Factual intelligence report detailing operational telemetry sweeps near the {country} frontier. "
                f"Command reports high-readiness posture. Incident remains active under surveillance. "
                f"Further updates are scheduled as the situation develops."
            )
            pub_time = datetime.now(timezone.utc) - timedelta(minutes=random.randint(1, 55))
            
            sources_map = {
                "Military & Defense": ["reuters.com", "apnews.com", "aljazeera.com"],
                "Economic & Financial": ["bloomberg.com", "reuters.com", "dw.com"],
                "Social Affairs & Welfare": ["bbc.com", "france24.com", "dw.com"],
                "Political & Diplomatic": ["theguardian.com", "nytimes.com", "aljazeera.com"],
                "Technology & Cyber": ["techcrunch.com", "wired.com", "theverge.com"]
            }
            src_list = sources_map.get(dept, ["bbc.com"])
            source = random.choice(src_list)

            url = f"https://demo.drishya.local/article/{cc.lower()}/{dept.lower().split()[0]}-{idx}-{random.randint(10000, 99999)}"

            # 1 High, 2 Medium, 2 Normal
            if idx == 0:
                impact = "High Impact"
            elif idx in (1, 3):
                impact = "Medium Impact"
            else:
                impact = "Normal Impact"

            db_article = Article(
                title=title,
                headline=title,
                summary=f"Intelligence briefing regarding localized {dept.lower()} activity near the {country} border.",
                content=content,
                url=url,
                source=source,
                country_code=cc,
                published_at=pub_time,
                impact_level=impact,
                department=dept
            )
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
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception as e:
                logger.warning(f"[Database] Failed to enable pgvector extension: {e}")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("[Database] Tables created successfully.")

    # Always ensure user accounts are seeded
    async with SessionLocal() as session:
        try:
            import bcrypt
            old_credentials = [
                {"username": "operator@intel.local", "password": "Ops@2026", "role": "Operator"},
                {"username": "analyst@intel.local", "password": "Intel@2026", "role": "Analyst"},
                {"username": "admin@intel.local", "password": "Admin@2026", "role": "Admin"}
            ]
            for cred in old_credentials:
                check_stmt = select(User).where(User.username == cred["username"])
                check_res = await session.execute(check_stmt)
                if not check_res.scalars().first():
                    logger.info(f"[Database] Seeding secure user account: {cred['username']}")
                    hashed = bcrypt.hashpw(cred["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                    user_obj = User(
                        username=cred["username"],
                        password_hash=hashed,
                        role=cred["role"]
                    )
                    session.add(user_obj)
            await session.commit()
        except Exception as e:
            logger.error(f"[Database] User seeding failed: {e}")
        
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
