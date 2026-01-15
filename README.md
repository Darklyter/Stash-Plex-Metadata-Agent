# Plex Stash Metadata Agent (Plex v1.43.0.10389+)

A lightweight FastAPI-based metadata provider that enables Plex Media Server to fetch metadata from your local Stash instance for adult content libraries.

## Overview

This agent allows Plex "Movies" libraries to match files and retrieve metadata (titles, performers, tags, thumbnails) from Stash instead of traditional sources like TMDB.  This matching is based on the filename of the video only, it takes nothing else into consideration. It's designed as a starting example that can be used by others, or fully fleshed out as more information comes out about the new Plex Metadata Agent system is released.  It is my personal hope that this functionality can either be baked into Stash directly, or that plugin support will be modified to allow plugins to respond to URL requests on the Stash server.  If that were to happen then this could easily be converted to a Stash Plugin instead of a standalone application.

**Note:** This API supports "Movie" type libraries.  If you would like to use "TV Show" libraries instead, please let me know when you're done writing it.  However please realize that items such as "Genre" are set at a "Show" level for that library type.  So if you set a "Brazzers" entry as a "Show" then all tags will be assigned to the "Brazzers" level instead of individual scenes within the containing "Show" element.  Sure it uses a landscape image for the scenes that way, but I'd rather have tags per scene personally.  But everyone is different.

**Another Note:** This does not take any sort of Stash authentication into consideration.  If you use authentication either disable it or submit a PR. 😁


## Quick Start

### Installation
```bash
# Clone or download the project
cd stashplexagent

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration
Edit `stashplexagent.config`:
```ini
[plexagentserver]
host = 0.0.0.0
port = 7979
num_workers = 2

[stash]  
ip = 192.168.1.71
port = 9999
debug = false
```

### Running
```bash
python main.py
```

## Deployment Options

### Docker (Recommended)
```bash
# Using docker-compose (easiest)
docker-compose up -d

# Or build and run manually
docker build -t stashplexagent .
docker run -d \
  --name stashplexagent \
  -p 7979:7979 \
  stashplexagent
```

### Ubuntu systemd Service
1. Copy project to a directory and create virtualenv inside of it
2. Create `/etc/systemd/system/stashplexagent.service`:

```ini
[Unit]
Description=Plex Stash Agent
After=network.target

[Service]
Type=simple
User=stash
Group=stash
WorkingDirectory=/opt/stashplexagent
ExecStart=venv/bin/python main.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

3. Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stashplexagent.service
sudo ufw allow 7979/tcp  # If using firewall
```

## Environment Variables

- `STASH_HOST` - Override Stash server URL from config file (optional)
- `DEBUG` - Enable debug logging (`true`/`false`)  
- `DEV` - Enable development mode with auto-reload (`true`/`false`)

## API Endpoints

- `GET /` - Agent identification for Plex
- `POST /library/metadata/matches` - Match files by filename
- `GET /library/metadata/{ratingKey}` - Get metadata for specific items

## Requirements

- Python 3.10+
- Stash instance with GraphQL API accessible
- Plex Media Server (v1.43.0.10389+)

## Files Structure

- `stashplexagent.py` - Main FastAPI application
- `main.py` - Production launcher with config support
- `stashplexagent.config` - Configuration file
- `requirements.txt` - Python dependencies
- `Dockerfile` & `docker-compose.yml` - Container deployment

## Notes

- Adjust config file paths and user permissions for your environment
- For production use, consider running behind a reverse proxy (nginx) with TLS
- Debug mode shows GraphQL queries and responses for troubleshooting
