# Plex Stash Metadata Agent (Plex v1.43.0.10389+)

A lightweight FastAPI-based metadata provider that enables Plex Media Server to fetch metadata from your local Stash instance for adult content libraries.

## Overview

This agent allows Plex "Movies" libraries to match files and retrieve metadata (titles, performers, tags, thumbnails, ratings) from Stash instead of traditional sources like TMDB.  This matching is based on the filename of the video only, it takes nothing else into consideration. It's designed as a starting example that can be used by others, or fully fleshed out as more information comes out about the new Plex Metadata Agent system is released.  It is my personal hope that this functionality can either be baked into Stash directly, or that plugin support will be modified to allow plugins to respond to URL requests on the Stash server.  If that were to happen then this could easily be converted to a Stash Plugin instead of a standalone application.

**Note:** This API supports "Movie" type libraries.  If you would like to use "TV Show" libraries instead, please let me know when you're done writing it.  However please realize that items such as "Genre" are set at a "Show" level for that library type.  So if you set a "Brazzers" entry as a "Show" then all tags will be assigned to the "Brazzers" level instead of individual scenes within the containing "Show" element.  Sure it uses a landscape image for the scenes that way, but I'd rather have tags per scene personally.  But everyone is different.


### Plex Configuration (Should be done after API is running)

To enable this agent in Plex (v1.43.0.10389+), you need to go into the Plex settings, and under your server you should find `Metadata Agents`.

First create a `Metadata Provider`, which will only take the url:port for this application.  For me it is `http://192.168.0.81:7979`.  If the API is running it should automatically retrieve the specs and name the Agent to "Stash Plex Metadata Provider".

Once the Provider is created, then create a `Metadata Agent`.  You can name it whatever you would like, but select "Stash Plex Metadata Provider" from the dropdown list of options.  You can add the Personal Media as a fallback if you have local images with the files I believe, but I haven't tried that

Once the Agent is created, then you can apply that to a library.  Just create a new Library of type "Movies", call it whatever you would like, add whatever folders, and under `Advanced` select "Plex Movie" as the scanner and whatever you called your Agent as the agent.



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
poster_mode = false
agent_base_url = http://192.168.1.81:7979

[stash]
ip = 192.168.1.71
port = 9999
api_key =
debug = false
cache_ttl = 300

[plex]
url =
token =
```

All settings can be configured via the config file. Environment variables are also supported and take precedence over config file values when set (see [Environment Variables](#environment-variables)).

#### Stash API Key (Authentication)

If your Stash instance has authentication enabled, you need to provide an API key. Generate one in Stash under **Settings > Security > API Key**.

Set it via either:
- **Config file:** Set `api_key` in the `[stash]` section
- **Environment variable:** Set `STASH_API_KEY` (takes precedence over config file)

If your Stash instance does not use authentication, leave `api_key` empty.

#### Response Caching

Metadata responses from Stash are cached in memory with a configurable TTL (default: 300 seconds / 5 minutes). This avoids redundant queries during library scans.

- **Config file:** Set `cache_ttl` in the `[stash]` section (in seconds)
- **Environment variable:** Set `CACHE_TTL` (takes precedence over config file)
- Set to `0` to disable caching entirely.

### Running
```bash
python main.py
```

## Deployment Options

### Docker (Recommended)
```bash
# Using docker-compose (easiest)
docker-compose up -d --build

# Or build and run manually
docker build -t stashplexagent .
docker run -d \
  --name stashplexagent \
  -p 7979:7979 \
  -v ./stashplexagent.config:/app/stashplexagent.config:ro \
  stashplexagent
```

The `docker-compose.yml` mounts `stashplexagent.config` from the project directory into the container. All settings are read from this config file — no environment variables are needed unless you want to override specific values.

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

## Configuration Reference

All settings below can be set in `stashplexagent.config`. Environment variables are also supported as overrides (env vars take precedence when set).

### Config File Settings

| Section | Key | Env Override | Description | Default |
|---------|-----|-------------|-------------|---------|
| `[plexagentserver]` | `host` | — | Listen address | `0.0.0.0` |
| `[plexagentserver]` | `port` | — | Listen port | `7979` |
| `[plexagentserver]` | `num_workers` | — | Number of uvicorn workers | `2` |
| `[plexagentserver]` | `poster_mode` | `POSTER_MODE` | Reformat screenshots into 2:3 posters | `false` |
| `[plexagentserver]` | `agent_base_url` | `AGENT_BASE_URL` | Public URL of this agent (see below) | `http://<host>:<port>` |
| `[stash]` | `ip` | `STASH_HOST`* | Stash server IP | `192.168.1.71` |
| `[stash]` | `port` | `STASH_HOST`* | Stash server port | `9999` |
| `[stash]` | `api_key` | `STASH_API_KEY` | API key for authenticated Stash instances | *(empty)* |
| `[stash]` | `debug` | `DEBUG` | Enable debug logging | `false` |
| `[stash]` | `cache_ttl` | `CACHE_TTL` | Metadata cache lifetime in seconds (`0` to disable) | `300` |
| `[plex]` | `url` | `PLEX_URL` | Plex server URL for poster upload | *(empty)* |
| `[plex]` | `token` | `PLEX_TOKEN` | Plex auth token for poster upload | *(empty)* |

\* `STASH_HOST` env var overrides both `ip` and `port` as a full URL (e.g., `http://192.168.1.71:9999`).

Additional env-only variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `DEV` | Enable development mode with auto-reload (`true`/`false`) | `false` |

**Note:** When using Docker, empty environment variables (e.g., `PLEX_URL=`) will override config file values with blank strings. The recommended approach is to set everything in the config file and not define environment variables at all unless you need to override a specific setting.

#### Image Proxy / `agent_base_url`

Plex proxies thumbnail and artwork URLs through `images.plex.tv`, which cannot reach private LAN addresses like `192.168.x.x`. To work around this, the agent proxies all Stash images through itself. The `agent_base_url` setting tells the agent what URL Plex should use to reach it — this should be the LAN IP or hostname that your Plex server can access (e.g., `http://192.168.1.81:7979`).

#### Poster Mode

When `poster_mode = true`, scene screenshots are reformatted into 2:3 aspect ratio poster images. The original 16:9 screenshot is centered on a black canvas with letterbox bars at the top and bottom, giving you the full video image in a poster format suitable for Plex "Movies" libraries. Requires `agent_base_url` to be set.

#### Plex Poster Upload

Plex routes all poster/thumbnail display through `images.plex.tv`, a cloud CDN that **cannot reach private LAN addresses**. This means poster URLs pointing to `192.168.x.x` will fail to display in Plex clients.

To work around this, the agent can upload posters **directly to your Plex server** via its local API. Set `url` and `token` in the `[plex]` section (and enable `poster_mode`) to activate this feature. The agent will:

1. Serve the metadata response to Plex as usual
2. In the background, search your Plex server for the matched item
3. Upload the 2:3 poster image directly to Plex's local storage
4. Plex then serves the locally-stored poster without needing `images.plex.tv`

For metadata refreshes (item already exists in Plex), the poster uploads immediately. For new items, the agent waits for Plex to finish ingesting the metadata before uploading.

To find your Plex token, see [Finding an authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).

## API Endpoints

- `GET /` - Agent identification for Plex
- `POST /library/metadata/matches` - Match files by filename
- `GET /library/metadata/{ratingKey}` - Get metadata for specific items
- `GET /stash/scene/{id}/screenshot` - Proxied scene screenshot from Stash
- `GET /stash/scene/{id}/poster` - Screenshot reformatted as 2:3 poster
- `GET /stash/performer/{id}/image` - Proxied performer image from Stash
- `GET /stash/group/{id}/front` - Proxied group front image from Stash

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
