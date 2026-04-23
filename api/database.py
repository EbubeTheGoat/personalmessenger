from dotenv import load_dotenv
import os
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import DeclarativeBase, sessionmaker  
from sqlalchemy.sql import func
from api.cache import logger

load_dotenv()


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    logger.warning("DATABASE_URL not set. Defaulting to sqlite:///./test.db")
    raise RuntimeError("DATABASE_URL is not set") 

# The Serverless Fix:
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,       # Knocks to see if the connection is alive
    pool_recycle=300,         # Recycles connections older than 5 minutes
    pool_size=5,              # Keeps the pool small for serverless limits
    max_overflow=10
)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    phone_number = Column(String, unique=True, nullable=False)
    current_step = Column(String, default="IDLE")
    notification_topic = Column(String)
    last_message_id = Column(String)


from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from api.database import Base # Ensure it inherits from your project's Base

class RegistrationLead(Base):
    """
    Stores potential users who sign up via the landing page.
    These 'leads' act as a bridge until they start the Telegram bot.
    """
    __tablename__ = "registration_leads"
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    phone_number = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        """Helper for debugging in the Python console."""
        return f"<RegistrationLead(name='{self.full_name}', phone='{self.phone_number}')>"


class SentContent(Base):
    __tablename__ = "sent_content"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    url_hash = Column(String, nullable=False)
    summary = Column(String)
    sent_at = Column(DateTime, server_default=func.now())


Base.metadata.create_all(bind=engine)
