import sqlalchemy
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from sqlalchemy.ext.declarative import declarative_base 
from dotenv import load_dotenv
import os
from logging_config import get_logger
logger = get_logger("storage")

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
engine = create_engine(DATABASE_URL,
                       pool_pre_ping=True,
pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    phone_number = Column(String, unique=True, nullable=False)
    current_step = Column(String, default="IDLE")
    notification_topic = Column(String)
    last_message_id = Column(String)

class RegistrationLead(Base):
    __tablename__ = "registration_leads"
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    phone_number = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, server_default=sqlalchemy.sql.func.now())

class SentContent(Base):
    __tablename__ = "sent_content"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    url_hash = Column(String, nullable=False)
    summary = Column(String)
    sent_at = Column(DateTime, server_default=sqlalchemy.sql.func.now())