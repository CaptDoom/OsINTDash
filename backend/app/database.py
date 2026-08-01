import json
import logging
from datetime import datetime
import uuid
from typing import AsyncGenerator, List, Optional
from sqlalchemy import Column, String, Text, DateTime, TypeDecorator, select, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

# Archive Summary Model
class ArchiveSummary(Base):
    __tablename__ = "archive_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    timeframe: Mapped[str] = mapped_column(String(8), unique=True, nullable=False) # '1M', '6M', '1Y'
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

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
    if count >= 350:
        return
        
    logger.info("[Database] Seeding database with high density high impact realistic articles...")
    
    # Delete old seeding to ensure clean, high-density data without duplicate key errors
    from sqlalchemy import delete
    await session.execute(delete(Article))
    await session.commit()
    
    countries_list = ["China", "Pakistan", "Afghanistan", "Bangladesh", "Myanmar", "Nepal", "Bhutan", "Sri Lanka", "Maldives"]
    departments = ["Military & Defense", "Economic & Financial", "Social Affairs & Welfare", "Political & Diplomatic"]
    
    import random
    
    templates = {
        "Military & Defense": [
            "Verified deployment of active radar scanning arrays along the {country} border",
            "Joint tactical drills and armor maneuvers completed by frontier commands near {country}",
            "Special ops units conduct high-altitude patrol sweeps at the {country} demarcation lines",
            "UAV drone surveillance sweeps increase along the strategic {country} corridors"
        ],
        "Economic & Financial": [
            "New border highway and trade route construction expands commercial transit with {country}",
            "Deepwater port infrastructure upgrades secure strategic transport lanes near {country}",
            "Bilateral investment framework signed to fund major logistical hubs with {country}",
            "Customs clearing facilities upgrade processing capacity at {country} border posts"
        ],
        "Social Affairs & Welfare": [
            "Local resettlement and border governance programs expand near {country} frontier",
            "Humanitarian medical aid stations established to assist crossing points with {country}",
            "Friction between civilian border populations and local patrols rises near {country}",
            "Emergency food supply distribution sweeps verify secure conditions along the {country} line"
        ],
        "Political & Diplomatic": [
            "High-level security summit coordinates border tax collection rules with {country}",
            "Diplomatic delegations sign memorandum for shared checkpoint security with {country}",
            "Commanders verify border demarcation protocol updates during meetings with {country}",
            "Joint command coordination center launched to monitor communications with {country}"
        ]
    }
    
    from datetime import datetime, timedelta
    
    seeded_count = 0
    country_codes = {
        "China": "CN", "Pakistan": "PK", "Afghanistan": "AF", "Bangladesh": "BD",
        "Myanmar": "MM", "Nepal": "NP", "Bhutan": "BT", "Sri Lanka": "LK", "Maldives": "MV"
    }

    for country in countries_list:
        cc = country_codes.get(country, "GL")
        for dept in departments:
            # Create 10 unique articles per department
            title_templates = templates[dept]
            for i in range(10):
                temp = title_templates[i % len(title_templates)]
                title = temp.format(country=country) + f" (Intel Alert #{100 + i * 17 + random.randint(1, 9)})"
                content = (
                    f"Factual intelligence report detailing operational telemetry sweeps near the {country} frontier. "
                    f"Command reports high-readiness posture. Incident remains active under surveillance. "
                    f"Further updates are scheduled as the situation develops."
                )
                pub_time = datetime.utcnow() - timedelta(hours=random.randint(1, 48), minutes=random.randint(0, 59))
                
                db_article = Article(
                    title=title,
                    headline=title,
                    summary=f"Intelligence briefing regarding localized {dept.lower()} activity near the {country} border.",
                    content=content,
                    url=f"https://intel.local/report/{cc.lower()}/{dept.lower().split()[0]}/{i}/{random.randint(1000, 9999)}",
                    source="INTEL-MESH",
                    country_code=cc,
                    published_at=pub_time,
                    impact_level="High Impact",
                    department=dept
                )
                session.add(db_article)
                seeded_count += 1
                
    await session.commit()
    logger.info(f"[Database] Seeding finished. Added {seeded_count} High Impact articles.")

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
        
    # Run seeding if empty
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
