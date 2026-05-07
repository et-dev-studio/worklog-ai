from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.paths_service import db_url

engine = create_engine(db_url(), future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def get_session() -> Session:
    return SessionLocal()
