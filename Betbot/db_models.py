# -*- coding: utf-8 -*-
"""
Database models for BetBot
SQLAlchemy ORM models for fixtures, snapshots, and odds
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import os

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./betbot.db")

# Create engine and session
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Fixture(Base):
    """Football fixture/match model."""
    __tablename__ = "fixtures"
    
    id = Column(Integer, primary_key=True, index=True)
    fixture_id = Column(Integer, unique=True, index=True, nullable=False)
    league_id = Column(Integer, index=True)
    league_name = Column(String)
    season = Column(Integer)
    date = Column(DateTime)
    
    home_team_id = Column(Integer)
    home_team_name = Column(String)
    away_team_id = Column(Integer)
    away_team_name = Column(String)
    
    status = Column(String)
    elapsed = Column(Integer)
    
    home_goals = Column(Integer)
    away_goals = Column(Integer)
    
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    
    # Relationships
    snapshots = relationship("Snapshot", back_populates="fixture")
    odds = relationship("OddsLive", back_populates="fixture")


class Snapshot(Base):
    """Match statistics snapshot at a specific minute."""
    __tablename__ = "snapshots"
    
    id = Column(Integer, primary_key=True, index=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.fixture_id"), index=True, nullable=False)
    minute = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    
    # Home team statistics
    home_shots = Column(Integer)
    home_sog = Column(Integer)  # Shots on goal
    home_soff = Column(Integer)  # Shots off goal
    home_corners = Column(Integer)
    home_fouls = Column(Integer)
    home_yellow = Column(Integer)
    home_red = Column(Integer)
    home_saves = Column(Integer)
    home_poss = Column(Float)  # Possession percentage
    
    # Away team statistics
    away_shots = Column(Integer)
    away_sog = Column(Integer)
    away_soff = Column(Integer)
    away_corners = Column(Integer)
    away_fouls = Column(Integer)
    away_yellow = Column(Integer)
    away_red = Column(Integer)
    away_saves = Column(Integer)
    away_poss = Column(Float)
    
    # Score at this minute
    home_score = Column(Integer)
    away_score = Column(Integer)
    
    # Relationship
    fixture = relationship("Fixture", back_populates="snapshots")


class OddsLive(Base):
    """Live betting odds for fixtures."""
    __tablename__ = "odds_live"
    
    id = Column(Integer, primary_key=True, index=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.fixture_id"), index=True, nullable=False)
    bookmaker = Column(String)
    market = Column(String)
    
    timestamp = Column(DateTime, nullable=False)
    minute = Column(Integer)
    
    # Odds values
    home_odds = Column(Float)
    draw_odds = Column(Float)
    away_odds = Column(Float)
    
    # Over/Under
    over_under_line = Column(Float)
    over_odds = Column(Float)
    under_odds = Column(Float)
    
    # Relationship
    fixture = relationship("Fixture", back_populates="odds")


# Create all tables
def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("Database tables created successfully!")
