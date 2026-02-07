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
    # Check for development mode
    dev_mode = os.getenv("DEV", "false").lower() == "true"
    debug_mode = (
        os.getenv("DEBUG", "false").lower() == "true"
        or config.getboolean("stash", "debug", fallback=False)
    )

    if dev_mode:
        # Development mode: single worker with auto-reload
        print("[DEV MODE] Starting with auto-reload enabled")
        uvicorn.run("stashplexagent:app", host=server_host, port=server_port, reload=True)
    else:
        # Production mode: multiple workers, no reload, suppress access logs unless debug
        uvicorn.run(
            "stashplexagent:app",
            host=server_host,
            port=server_port,
            workers=num_workers,
            access_log=debug_mode,
        )
