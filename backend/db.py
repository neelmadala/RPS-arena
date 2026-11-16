"""Database models (SQLite by default; set DATABASE_URL for Postgres)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


def _now():
    return datetime.now(timezone.utc)


class Match(Base):
    __tablename__ = "matches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator: Mapped[str] = mapped_column(String(42), index=True)
    joiner: Mapped[str | None] = mapped_column(String(42), index=True, nullable=True)
    stake_wei: Mapped[str] = mapped_column(String(80))  # string: wei can exceed 64-bit ints
    status: Mapped[str] = mapped_column(String(16), index=True)
    creator_move: Mapped[str | None] = mapped_column(String(10), nullable=True)
    joiner_move: Mapped[str | None] = mapped_column(String(10), nullable=True)
    winner: Mapped[str | None] = mapped_column(String(42), nullable=True)
    is_draw: Mapped[bool] = mapped_column(Boolean, default=False)
    forfeit: Mapped[bool] = mapped_column(Boolean, default=False)
    reveal_deadline: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_tx: Mapped[str | None] = mapped_column(String(66), nullable=True)
    created_block: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "creator": self.creator,
            "joiner": self.joiner,
            "stake_wei": self.stake_wei,
            "status": self.status,
            "creator_move": self.creator_move,
            "joiner_move": self.joiner_move,
            "winner": self.winner,
            "is_draw": self.is_draw,
            "forfeit": self.forfeit,
            "reveal_deadline": self.reveal_deadline,
            "created_tx": self.created_tx,
            "created_block": self.created_block,
        }


class Player(Base):
    __tablename__ = "players"
    address: Mapped[str] = mapped_column(String(42), primary_key=True)
    elo: Mapped[float] = mapped_column(Float, default=config.ELO_START)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    draws: Mapped[int] = mapped_column(Integer, default=0)

    def to_dict(self) -> dict:
        games = self.wins + self.losses + self.draws
        return {
            "address": self.address,
            "elo": round(self.elo),
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "games": games,
        }


class Meta(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))


def make_session_factory(url: str | None = None):
    url = url or config.DATABASE_URL
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
