from fastapi import FastAPI

app = FastAPI(
    title="Nexa Media Engine",
    version="1.0.0"
)

@app.get("/")
async def root():
    return {
        "service": "Nexa Media Engine",
        "status": "running"
    }

@app.get("/health")
async def health():
    return {
        "status": "ok"
    }