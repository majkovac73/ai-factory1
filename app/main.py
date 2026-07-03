from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI
from app.api.tasks import router
from app.db.database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.include_router(router)