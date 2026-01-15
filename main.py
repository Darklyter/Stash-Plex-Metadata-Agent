"""Compatibility entrypoint: start the ASGI app with settings from stashplexagent.config

Run with:
    python main.py

This will read `stashplexagent.config` and start uvicorn for `stashplexagent:app`.
"""
import os
import configparser
import uvicorn

# Read config
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), "stashplexagent.config")
config.read(config_path)

# server section name: plexagentserver
server_host = config.get("plexagentserver", "host", fallback="0.0.0.0")
server_port = int(config.get("plexagentserver", "port", fallback="7979"))
num_workers = int(config.get("plexagentserver", "num_workers", fallback="2"))

if __name__ == "__main__":
    # Allow STASH_HOST env var to override the configured stash host
    # Check for development mode
    dev_mode = os.getenv("DEV", "false").lower() == "true"
    
    if dev_mode:
        # Development mode: single worker with auto-reload
        print("[DEV MODE] Starting with auto-reload enabled")
        uvicorn.run("stashplexagent:app", host=server_host, port=server_port, reload=True)
    else:
        # Production mode: multiple workers, no reload
        uvicorn.run("stashplexagent:app", host=server_host, port=server_port, workers=num_workers)
from fastapi import FastAPI, Request
import uvicorn
import os
import configparser

app = FastAPI()

# Load configuration for server host/port
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), "stashplexagent.config")
config.read(config_path)
server_host = config.get("plexagentserver", "host", fallback="0.0.0.0")
server_port = int(config.get("plexagentserver", "port", fallback="7979"))

@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"Request: {request.method} {request.url}")
    response = await call_next(request)
    return response

@app.get("/")
async def root():
    return {"message": "Plex Stash Agent API"}

if __name__ == "__main__":
    uvicorn.run("main:app", host=server_host, port=server_port, reload=True)
