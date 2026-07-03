from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from app.api.tasks import router
from app.db.database import Base, engine
from config import settings

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)

app.include_router(router)

print(f"Loaded configuration for {settings.APP_NAME} ({settings.ENV})")