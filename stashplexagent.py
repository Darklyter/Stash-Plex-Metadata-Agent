import io
import re
import os
import json
import logging
import configparser
import time
import requests
import urllib.parse
from datetime import datetime, timezone
from fastapi import BackgroundTasks, FastAPI, Request, Response
from PIL import Image

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("stashplexagent")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger.addHandler(handler)

app = FastAPI()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), "stashplexagent.config")
config.read(config_path)

# Stash connection (config file values can be overridden by env vars)
stash_ip = config.get("stash", "ip", fallback="192.168.1.71")
stash_port = config.get("stash", "port", fallback="9999")
stash_host = os.getenv("STASH_HOST", f"http://{stash_ip}:{stash_port}")

# Stash API key – optional, needed when Stash has authentication enabled
stash_api_key = os.getenv(
    "STASH_API_KEY",
    config.get("stash", "api_key", fallback=""),
)

# Debug / log level
debug_enabled = (
    os.getenv("DEBUG", "false").lower() == "true"
    or config.getboolean("stash", "debug", fallback=False)
)
logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)

# Cache TTL in seconds (0 = disabled)
CACHE_TTL = int(os.getenv("CACHE_TTL", config.get("stash", "cache_ttl", fallback="300")))

# Base URL for this agent – used to build image proxy URLs that Plex can reach.
_cfg_base_url = config.get("plexagentserver", "agent_base_url", fallback="")
if not _cfg_base_url:
    _agent_host = config.get("plexagentserver", "host", fallback="0.0.0.0")
    _agent_port = config.get("plexagentserver", "port", fallback="7979")
    if _agent_host == "0.0.0.0":
        _agent_host = "127.0.0.1"
    _cfg_base_url = f"http://{_agent_host}:{_agent_port}"
agent_base_url = os.getenv("AGENT_BASE_URL", _cfg_base_url)

# Poster mode – reformat 16:9 screenshots into 2:3 poster images with black bars.
# Requires AGENT_BASE_URL to be set so Plex can reach the proxy endpoint.
poster_mode = (
    os.getenv("POSTER_MODE", "false").lower() == "true"
    or config.getboolean("plexagentserver", "poster_mode", fallback=False)
)

# Plex server connection – optional, enables direct poster upload to PMS.
# Bypasses images.plex.tv which cannot reach private LAN addresses.
plex_url = os.getenv("PLEX_URL", config.get("plex", "url", fallback="")).rstrip("/")
plex_token = os.getenv("PLEX_TOKEN", config.get("plex", "token", fallback=""))
plex_upload_enabled = bool(plex_url and plex_token and poster_mode)
if plex_upload_enabled:
    logger.info("Plex poster upload enabled → %s", plex_url)

# ---------------------------------------------------------------------------
# Simple TTL cache
# ---------------------------------------------------------------------------
_cache: dict[str, tuple[float, dict]] = {}


def _cache_get(key: str) -> dict | None:
    """Return cached value if it exists and hasn't expired."""
    if CACHE_TTL <= 0:
        return None
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > CACHE_TTL:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: dict) -> None:
    if CACHE_TTL > 0:
        _cache[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.debug("Request: %s %s", request.method, request.url)
    response = await call_next(request)

    if debug_enabled and not request.url.path.startswith("/stash/"):
        # Capture and log the response body, then re-wrap it
        # (skip image proxy paths to avoid logging binary data)
        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk if isinstance(chunk, bytes) else chunk.encode()
        try:
            parsed = json.loads(response_body)
            logger.debug(
                "Response to Plex (%s %s) [%d]:\n%s",
                request.method,
                request.url.path,
                response.status_code,
                json.dumps(parsed, indent=2),
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.debug("Response body (raw, %d bytes)", len(response_body))
        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    return response


# ---------------------------------------------------------------------------
# Stash helpers
# ---------------------------------------------------------------------------
def _sanitize_graphql_string(value: str) -> str:
    """Escape characters that could break a GraphQL string literal."""
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("\n", "\\n")
    value = value.replace("\r", "\\r")
    return value


def _build_stash_headers() -> dict[str, str]:
    """Build HTTP headers for Stash requests, including API key if set."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if stash_api_key:
        headers["ApiKey"] = stash_api_key
    return headers


SCENE_QUERY_TEMPLATE = """query {{
  findScenes(scene_filter: {{ {filter} }}) {{
    scenes {{
      id
      code
      title
      date
      urls
      rating100
      details
      director
      created_at
      tags {{ id name }}
      studio {{ id name image_path parent_studio {{ id name }} }}
      performers {{ id name image_path }}
      groups {{ group {{ id name front_image_path }} scene_index }}
      scene_markers {{ id title seconds primary_tag {{ name }} }}
      files {{ path basename duration width height video_codec audio_codec frame_rate bit_rate size }}
    }}
  }}
}}"""


def _self_url(request_or_none=None) -> str:
    """Return the base URL that Plex should use to reach *this* agent.

    During a request context we could inspect the Host header, but the
    simplest reliable approach is to build it from our configured listen
    address.  The agent_host module-level variable is set once at startup.
    """
    return agent_base_url


def parse_stash_response(filter_clause: str) -> dict | None:
    """Query Stash and convert the response into a Plex MediaContainer dict."""
    cache_key = f"filter:{filter_clause}"
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for %s", cache_key)
        return cached

    graphql_query = SCENE_QUERY_TEMPLATE.format(filter=filter_clause)

    if debug_enabled:
        logger.debug("GraphQL Query: %s", graphql_query)
        logger.debug("Stash Host: %s", stash_host)
        encoded_query = urllib.parse.quote(graphql_query)
        logger.debug("Clickable GraphQL URL (encoded): %s/graphql?query=%s", stash_host, encoded_query)

    try:
        response = requests.post(
            f"{stash_host}/graphql",
            json={"query": graphql_query},
            headers=_build_stash_headers(),
            timeout=10,
        )
        response.raise_for_status()
        jsondata = response.json()

        if debug_enabled:
            logger.debug("Stash Response: %s", jsondata)

    except requests.exceptions.RequestException as e:
        logger.error("Failed to connect to Stash: %s", e)
        if debug_enabled:
            logger.debug("Attempted URL: %s/graphql", stash_host)
        return None
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return None

    scenes = (
        jsondata.get("data", {})
        .get("findScenes", {})
        .get("scenes")
    )

    if not scenes or len(scenes) == 0:
        logger.debug("No scenes found for filter: %s", filter_clause)
        return None

    # Build Plex MediaContainer with all matching scenes
    metadata_list = []
    for scene in scenes:
        moviedata: dict = {}

        # Artwork – use poster proxy (2:3 with black bars) when poster_mode is on
        if poster_mode:
            moviedata["art"] = f"{agent_base_url}/stash/scene/{scene['id']}/poster"
            moviedata["thumb"] = f"{agent_base_url}/stash/scene/{scene['id']}/poster"
        else:
            moviedata["art"] = f"{agent_base_url}/stash/scene/{scene['id']}/screenshot"
            moviedata["thumb"] = f"{agent_base_url}/stash/scene/{scene['id']}/screenshot"

        # Identifiers
        moviedata["guid"] = f"plex://movie/stash-video-{scene['id']}"
        moviedata["key"] = f"/library/metadata/stash-video-{scene['id']}"
        moviedata["ratingKey"] = f"stash-video-{scene['id']}"
        moviedata["type"] = "movie"

        # Core metadata (null-safe)
        moviedata["title"] = scene.get("title") or scene.get("code") or ""
        moviedata["summary"] = scene.get("details") or ""
        moviedata["originallyAvailableAt"] = scene.get("date")

        # Tagline – use production code if available and different from title
        code = scene.get("code") or ""
        if code and code != moviedata["title"]:
            moviedata["tagline"] = code

        date_str = scene.get("date") or ""
        if len(date_str) >= 4:
            try:
                moviedata["year"] = int(date_str[:4])
            except ValueError:
                pass

        # addedAt – when scene was added to Stash (ISO -> epoch integer)
        created_at = scene.get("created_at") or ""
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                moviedata["addedAt"] = int(dt.timestamp())
            except (ValueError, TypeError):
                pass

        # Studio (include parent studio / network name when available)
        studio = scene.get("studio")
        if studio and isinstance(studio, dict):
            studio_name = studio.get("name", "")
            parent = studio.get("parent_studio")
            if parent and isinstance(parent, dict):
                parent_name = parent.get("name", "")
                if parent_name and parent_name != studio_name:
                    moviedata["studio"] = f"{studio_name} ({parent_name})"
                else:
                    moviedata["studio"] = studio_name
            else:
                moviedata["studio"] = studio_name

        # Rating (Stash uses 0-100, Plex uses 0-10 float)
        rating100 = scene.get("rating100")
        if rating100 is not None:
            try:
                moviedata["rating"] = round(int(rating100) / 10.0, 1)
            except (ValueError, TypeError):
                pass

        # Director
        director = scene.get("director") or ""
        if director:
            moviedata["Director"] = [{"tag": director}]

        # Tags -> Genres
        for tag in scene.get("tags") or []:
            tag_name = tag.get("name")
            if tag_name:
                moviedata.setdefault("Genre", []).append({"tag": tag_name})

        # Performers -> Roles
        for performer in scene.get("performers") or []:
            perf_name = performer.get("name")
            if perf_name:
                role: dict = {"tag": perf_name}
                perf_id = performer.get("id")
                if perf_id:
                    role["thumb"] = f"{stash_host}/performer/{perf_id}/image"
                moviedata.setdefault("Role", []).append(role)

        # Groups -> Collections
        for group_entry in scene.get("groups") or []:
            group = group_entry.get("group")
            if group and group.get("name"):
                moviedata.setdefault("Collection", []).append({"tag": group["name"]})

        # Scene markers -> Chapters
        markers = scene.get("scene_markers") or []
        if markers:
            chapters = []
            for marker in sorted(markers, key=lambda m: m.get("seconds", 0)):
                chapter_title = marker.get("title") or ""
                primary_tag = marker.get("primary_tag")
                if not chapter_title and primary_tag:
                    chapter_title = primary_tag.get("name", "")
                chapters.append({
                    "tag": chapter_title,
                    "index": len(chapters) + 1,
                    "startTimeOffset": int(marker.get("seconds", 0) * 1000),
                })
            if chapters:
                moviedata["Chapter"] = chapters

        # Media info from files[]
        files = scene.get("files") or []
        if files:
            f = files[0]
            duration_s = f.get("duration")
            media: dict = {}

            if duration_s is not None:
                try:
                    duration_ms = int(float(duration_s) * 1000)
                    media["duration"] = duration_ms
                    moviedata["duration"] = duration_ms
                except (ValueError, TypeError):
                    pass

            width = f.get("width")
            height = f.get("height")
            if width:
                media["width"] = width
            if height:
                media["height"] = height

            video_codec = f.get("video_codec") or ""
            if video_codec:
                media["videoCodec"] = video_codec

            audio_codec = f.get("audio_codec") or ""
            if audio_codec:
                media["audioCodec"] = audio_codec

            bit_rate = f.get("bit_rate")
            if bit_rate:
                media["bitrate"] = bit_rate

            frame_rate = f.get("frame_rate")
            if frame_rate:
                # Plex expects frame rate as a string label
                fr = float(frame_rate)
                if abs(fr - 23.976) < 0.5:
                    media["videoFrameRate"] = "24p"
                elif abs(fr - 24.0) < 0.5:
                    media["videoFrameRate"] = "24p"
                elif abs(fr - 25.0) < 0.5:
                    media["videoFrameRate"] = "PAL"
                elif abs(fr - 29.97) < 0.5:
                    media["videoFrameRate"] = "NTSC"
                elif abs(fr - 30.0) < 0.5:
                    media["videoFrameRate"] = "30p"
                elif abs(fr - 50.0) < 0.5:
                    media["videoFrameRate"] = "50p"
                elif abs(fr - 59.94) < 0.5 or abs(fr - 60.0) < 0.5:
                    media["videoFrameRate"] = "60p"
                else:
                    media["videoFrameRate"] = f"{int(fr)}p"

            # Plex nests file info under Media.Part
            part: dict = {}
            file_path = f.get("path") or ""
            if file_path:
                part["file"] = file_path
            file_size = f.get("size")
            if file_size:
                part["size"] = file_size
            if part:
                media["Part"] = [part]

            # Video resolution label
            if height:
                if height >= 2160:
                    media["videoResolution"] = "4k"
                elif height >= 1080:
                    media["videoResolution"] = "1080"
                elif height >= 720:
                    media["videoResolution"] = "720"
                elif height >= 480:
                    media["videoResolution"] = "480"
                else:
                    media["videoResolution"] = "sd"

            if media:
                moviedata["Media"] = [media]

        metadata_list.append(moviedata)

    movie = {
        "MediaContainer": {
            "offset": 0,
            "totalSize": len(metadata_list),
            "identifier": "tv.plex.agents.custom.stash",
            "size": len(metadata_list),
            "Metadata": metadata_list,
        }
    }

    _cache_set(cache_key, movie)
    return movie


def query_stash_by_filename(filename: str) -> dict | None:
    if not filename:
        return None
    safe_name = _sanitize_graphql_string(filename)
    filter_clause = f'path: {{value: "\\"{safe_name}\\"", modifier: INCLUDES}}'
    return parse_stash_response(filter_clause)


def query_stash_by_ratingKey(ratingKey: str) -> dict | None:
    if not ratingKey:
        return None
    match = re.search(r"-(\d+)$", ratingKey)
    if not match:
        return None
    scene_id = match.group(1)
    filter_clause = f"id: {{value: {scene_id}, modifier: EQUALS}}"
    return parse_stash_response(filter_clause)


# ---------------------------------------------------------------------------
# Plex endpoints
# ---------------------------------------------------------------------------
@app.get("/")
async def root(response: Response):
    response.headers["X-Plex-Client-Identifier"] = "stash.plex.provider.metadata"
    return {
        "MediaProvider": {
            "identifier": "tv.plex.agents.custom.stash",
            "title": "Stash Plex Metadata Provider",
            "version": "1.1.0",
            "Types": [
                {
                    "type": 1,
                    "Scheme": [{"scheme": "tv.plex.agents.custom.stash"}],
                },
            ],
            "Feature": [
                {"type": "metadata", "key": "/library/metadata"},
                {"type": "match", "key": "/library/metadata/matches"},
            ],
        }
    }


@app.post("/library/metadata/matches")
async def library_metadata_matches(request: Request):
    body = await request.json()
    if debug_enabled:
        logger.debug("Match request body:\n%s", json.dumps(body, indent=2))

    # Plex may request certain elements be excluded from the response
    exclude_elements = {
        e.strip() for e in (body.get("excludeElements") or "").split(",") if e.strip()
    }

    filename = body.get("filename")
    if filename:
        result = query_stash_by_filename(filename)
        if result and exclude_elements:
            for item in result.get("MediaContainer", {}).get("Metadata", []):
                for element in exclude_elements:
                    item.pop(element, None)
        if result:
            return result
    return {"MediaContainer": {"offset": 0, "totalSize": 0, "identifier": "tv.plex.agents.custom.stash", "size": 0, "Metadata": []}}


@app.get("/library/metadata/{ratingKey}")
async def get_metadata(ratingKey: str, background_tasks: BackgroundTasks):
    logger.info("Fetching metadata for ratingKey: %s", ratingKey)
    result = query_stash_by_ratingKey(ratingKey)
    if result and plex_upload_enabled:
        metadata_list = result.get("MediaContainer", {}).get("Metadata", [])
        if metadata_list:
            title = metadata_list[0].get("title", "")
            match = re.search(r"-(\d+)$", ratingKey)
            if match and title:
                background_tasks.add_task(
                    _upload_poster_to_plex, match.group(1), title
                )
    if result:
        return result
    return {"MediaContainer": {"offset": 0, "totalSize": 0, "identifier": "tv.plex.agents.custom.stash", "size": 0, "Metadata": []}}


@app.get("/library/metadata/{ratingKey}/extras")
async def get_metadata_extras(ratingKey: str):
    return {"MediaContainer": {"offset": 0, "totalSize": 0, "identifier": "tv.plex.agents.custom.stash", "size": 0, "Metadata": []}}


# ---------------------------------------------------------------------------
# Image proxy – Plex fetches images through us so it doesn't need LAN access
# to Stash directly (images.plex.tv cannot reach private addresses).
# ---------------------------------------------------------------------------
@app.get("/stash/scene/{scene_id}/screenshot")
async def proxy_scene_screenshot(scene_id: str):
    """Proxy a scene screenshot from Stash."""
    return _proxy_stash_image(f"{stash_host}/scene/{scene_id}/screenshot")


@app.get("/stash/performer/{performer_id}/image")
async def proxy_performer_image(performer_id: str):
    """Proxy a performer image from Stash."""
    return _proxy_stash_image(f"{stash_host}/performer/{performer_id}/image")


@app.get("/stash/group/{group_id}/front")
async def proxy_group_front_image(group_id: str):
    """Proxy a group front image from Stash."""
    return _proxy_stash_image(f"{stash_host}/group/{group_id}/front_image")


@app.get("/stash/scene/{scene_id}/poster")
async def proxy_scene_poster(scene_id: str):
    """Fetch screenshot from Stash and reformat to 2:3 poster with black bars."""
    poster_bytes = _generate_poster_bytes(scene_id)
    if poster_bytes is None:
        return Response(status_code=502, content=b"Image processing error")
    return Response(
        content=poster_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=86400",
            "Content-Length": str(len(poster_bytes)),
        },
    )


def _proxy_stash_image(stash_url: str) -> Response:
    """Fetch an image from Stash and return it as a Response."""
    headers = _build_stash_headers()
    # Remove Content-Type for image fetch – let Stash decide
    headers.pop("Content-Type", None)
    try:
        resp = requests.get(stash_url, headers=headers, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Image proxy failed for %s: %s", stash_url, e)
        return Response(status_code=502, content=b"Bad Gateway")

    content_type = resp.headers.get("Content-Type", "image/jpeg")
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


POSTER_WIDTH = 600   # px – produces a 600x900 poster (2:3 aspect ratio)
POSTER_HEIGHT = 900


def _generate_poster_bytes(scene_id: str) -> bytes | None:
    """Fetch scene screenshot from Stash and return 2:3 poster JPEG bytes."""
    stash_url = f"{stash_host}/scene/{scene_id}/screenshot"
    headers = _build_stash_headers()
    headers.pop("Content-Type", None)
    try:
        resp = requests.get(stash_url, headers=headers, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Poster fetch failed for scene %s: %s", scene_id, e)
        return None

    try:
        src = Image.open(io.BytesIO(resp.content))
        src_w, src_h = src.size
        scale = POSTER_WIDTH / src_w
        scaled_h = int(src_h * scale)
        src = src.resize((POSTER_WIDTH, scaled_h), Image.LANCZOS)
        poster = Image.new("RGB", (POSTER_WIDTH, POSTER_HEIGHT), (0, 0, 0))
        y_offset = (POSTER_HEIGHT - scaled_h) // 2
        poster.paste(src, (0, y_offset))
        buf = io.BytesIO()
        poster.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        logger.error("Poster generation failed for scene %s: %s", scene_id, e)
        return None


# ---------------------------------------------------------------------------
# Plex poster upload – pushes posters directly to PMS so they're stored
# locally, bypassing images.plex.tv which can't reach private LAN addresses.
# ---------------------------------------------------------------------------
_uploaded_posters: set[str] = set()


def _get_pms_movie_section_keys() -> list[str]:
    """Get keys of all movie-type library sections from PMS."""
    try:
        resp = requests.get(
            f"{plex_url}/library/sections",
            params={"X-Plex-Token": plex_token},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        keys = []
        for directory in data.get("MediaContainer", {}).get("Directory", []):
            if directory.get("type") == "movie":
                keys.append(directory["key"])
        logger.debug("PMS movie library sections: %s", keys)
        return keys
    except Exception as e:
        logger.warning("Failed to get PMS library sections: %s", e)
        return []


def _search_pms_sections(section_keys: list[str], title: str, guid: str) -> str | None:
    """Search PMS library sections once for an item matching the given GUID."""
    for key in section_keys:
        try:
            resp = requests.get(
                f"{plex_url}/library/sections/{key}/all",
                params={
                    "type": 1,
                    "title": title,
                    "X-Plex-Token": plex_token,
                },
                headers={"Accept": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("MediaContainer", {}).get("Metadata", [])
            if debug_enabled:
                logger.debug(
                    "PMS section %s search for '%s': %d results",
                    key, title, len(items),
                )
            for item in items:
                if item.get("guid") == guid:
                    return item.get("ratingKey")
                for g in item.get("Guid", []):
                    if g.get("id") == guid:
                        return item.get("ratingKey")
        except Exception as e:
            logger.warning("PMS section %s search failed: %s", key, e)
    return None


def _upload_poster_to_plex(scene_id: str, title: str):
    """Background task: find the item in PMS and upload the poster directly."""
    if scene_id in _uploaded_posters:
        return

    guid = f"plex://movie/stash-video-{scene_id}"
    section_keys = _get_pms_movie_section_keys()
    if not section_keys:
        logger.error("No movie library sections found in PMS")
        return

    # Try immediately — item already exists for refreshes
    pms_key = _search_pms_sections(section_keys, title, guid)
    if pms_key:
        logger.info("PMS item found immediately for scene %s (refresh)", scene_id)
    else:
        # New item — wait for PMS to finish ingesting, then retry
        logger.debug("PMS item not found yet for scene %s, waiting for PMS to ingest...", scene_id)
        time.sleep(5)
        for attempt in range(8):
            pms_key = _search_pms_sections(section_keys, title, guid)
            if pms_key:
                break
            if attempt < 7:
                logger.debug("PMS item not found (attempt %d/8), retrying in 5s...", attempt + 1)
                time.sleep(5)

    if not pms_key:
        logger.error("PMS item not found for scene %s (GUID: %s)", scene_id, guid)
        return

    logger.debug("Found PMS ratingKey %s for scene %s", pms_key, scene_id)

    poster_bytes = _generate_poster_bytes(scene_id)
    if poster_bytes is None:
        return

    try:
        resp = requests.post(
            f"{plex_url}/library/metadata/{pms_key}/posters",
            params={"X-Plex-Token": plex_token},
            data=poster_bytes,
            headers={"Content-Type": "application/octet-stream"},
            timeout=30,
        )
        resp.raise_for_status()
        logger.info("Uploaded poster to PMS for scene %s (PMS key: %s)", scene_id, pms_key)
        _uploaded_posters.add(scene_id)
    except Exception as e:
        logger.error("Failed to upload poster to PMS for scene %s: %s", scene_id, e)


@app.get("/health")
async def health():
    return {"status": "ok"}


# For direct execution during development only
if __name__ == "__main__":
    import uvicorn
    server_host = config.get("plexagentserver", "host", fallback="0.0.0.0")
    server_port = int(config.get("plexagentserver", "port", fallback="7979"))
    uvicorn.run(app, host=server_host, port=server_port, reload=True)
