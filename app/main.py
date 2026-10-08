from fastapi import FastAPI

from app.routers import bookings, extraction, introductions, knowledge, matching, sessions

app = FastAPI(title="Kindred Concierge (Assessment)")

app.include_router(knowledge.router)
app.include_router(matching.router)
app.include_router(introductions.router)
app.include_router(bookings.router)
app.include_router(sessions.router)
app.include_router(extraction.router)


@app.get("/health")
def health():
    return {"status": "ok"}
