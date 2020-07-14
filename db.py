from sqlalchemy import Column, Integer, JSON
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Thumbnail(Base):
    __tablename__ = "Thumbnail"
    id = Column(Integer, primary_key=True, autoincrement=True)
    data = Column(Integer, primary_key=True, autoincrement=True)

    full_id = Column(Integer, primary_key=True, autoincrement=True)
