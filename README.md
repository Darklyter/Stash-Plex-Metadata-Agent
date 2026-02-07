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
docker-compose up -d

# Or build and run manually
docker build -t stashplexagent .
docker run -d \
  --name stashplexagent \
  -p 7979:7979 \
  -e STASH_HOST=http://192.168.1.71:9999 \
  -e STASH_API_KEY=your-api-key-here \
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

| Variable | Description | Default |
|----------|-------------|---------|
| `STASH_HOST` | Override Stash server URL from config file | `http://<ip>:<port>` from config |
| `STASH_API_KEY` | Stash API key for authenticated instances | *(empty)* |
| `DEBUG` | Enable debug logging (`true`/`false`) | `false` |
| `DEV` | Enable development mode with auto-reload (`true`/`false`) | `false` |
| `CACHE_TTL` | Metadata cache lifetime in seconds (`0` to disable) | `300` |
| `AGENT_BASE_URL` | Public URL of this agent (used in image proxy URLs sent to Plex) | `http://127.0.0.1:7979` |
| `POSTER_MODE` | Reformat screenshots into 2:3 poster images with black bars (`true`/`false`) | `false` |
| `PLEX_URL` | Plex Media Server URL for direct poster upload (e.g., `http://192.168.1.76:32400`) | *(empty)* |
| `PLEX_TOKEN` | Plex authentication token (`X-Plex-Token`) for poster upload | *(empty)* |

#### Image Proxy / `AGENT_BASE_URL`

Plex proxies thumbnail and artwork URLs through `images.plex.tv`, which cannot reach private LAN addresses like `192.168.x.x`. To work around this, the agent proxies all Stash images through itself. The `AGENT_BASE_URL` variable tells the agent what URL Plex should use to reach it — this should be the LAN IP or hostname that your Plex server can access (e.g., `http://192.168.1.81:7979`).

#### Poster Mode

When `POSTER_MODE=true` (or `poster_mode = true` in config), scene screenshots are reformatted into 2:3 aspect ratio poster images. The original 16:9 screenshot is centered on a black canvas with letterbox bars at the top and bottom, giving you the full video image in a poster format suitable for Plex "Movies" libraries. Requires `AGENT_BASE_URL` to be set.

#### Plex Poster Upload / `PLEX_URL` + `PLEX_TOKEN`

Plex routes all poster/thumbnail display through `images.plex.tv`, a cloud CDN that **cannot reach private LAN addresses**. This means poster URLs pointing to `192.168.x.x` will fail to display in Plex clients.

To work around this, the agent can upload posters **directly to your Plex server** via its local API. When both `PLEX_URL` and `PLEX_TOKEN` are set (and `POSTER_MODE` is enabled), the agent will:
1. Serve the metadata response to Plex as usual
2. In the background, search your Plex server for the matched item
3. Upload the 2:3 poster image directly to Plex's local storage
4. Plex then serves the locally-stored poster without needing `images.plex.tv`

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
