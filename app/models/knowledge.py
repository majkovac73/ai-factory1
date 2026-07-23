import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, String, Text, Index

from app.models.base import Base


class Knowledge(Base):
    """The factory's brain — organized, durable knowledge distilled from everything
    that happens (events, products, research, outcomes). Unlike the raw logs/events
    streams, each row is a decision-ready piece of knowledge the factory consults.

    kind:     observation | decision | lesson | research | outcome
    category: niche | product | market | pipeline | marketing | finance | quality | ops
    subject:  the topic key (a niche name, a format, "descriptions", a product) —
              lessons are upserted per (kind, category, subject) so knowledge
              *evolves* instead of piling up duplicates; timeline kinds append.
    """
    __tablename__ = "knowledge"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String, nullable=False)
    category = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    data = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=False, default=0.5)
    source = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


Index("ix_knowledge_kind_category", Knowledge.kind, Knowledge.category)
Index("ix_knowledge_subject", Knowledge.subject)
