import re
import os
import configparser
import requests
import urllib.parse
import uvicorn
from pydantic import BaseModel
from typing import Optional, List
from fastapi import FastAPI, Request, Header, Query, Body, Response

app = FastAPI()

# Load configuration
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), "stashplexagent.config")
config.read(config_path)

# Stash configuration (config file values can be overridden by the STASH_HOST env var)
stash_ip = config.get("stash", "ip", fallback="192.168.1.71")
stash_port = config.get("stash", "port", fallback="9999")
stash_host = os.getenv("STASH_HOST", f"http://{stash_ip}:{stash_port}")

# Debug configuration
debug_enabled = os.getenv("DEBUG", "false").lower() == "true" or config.getboolean("stash", "debug", fallback=False)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"Request: {request.method} {request.url}")
    response = await call_next(request)
    return response

def parse_stash_response(filter):
    file_query = "query { findScenes(scene_filter: { <FILTER> }) { scenes { id, code, title, date, urls, rating100, details, tags { id name }, studio { id name }, performers { id name } } } }"
    file_query = file_query.replace("<FILTER>", filter)
    
    if debug_enabled:
        print(f"[DEBUG] GraphQL Query: {file_query}")
        print(f"[DEBUG] Stash Host: {stash_host}")
        # Create clickable GraphQL links
        encoded_query = urllib.parse.quote(file_query)
        clickable_url_encoded = f"{stash_host}/graphql?query={encoded_query}"
        clickable_url_raw = f"{stash_host}/graphql?query={file_query}"
        print(f"[DEBUG] Clickable GraphQL URL (encoded): {clickable_url_encoded}")
        print(f"[DEBUG] Clickable GraphQL URL (raw): {clickable_url_raw}")
    
    try:
        # Use POST request with JSON payload (standard for GraphQL)
        response = requests.post(
            f'{stash_host}/graphql',
            json={'query': file_query},
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        response.raise_for_status()
        jsondata = response.json()
        
        if debug_enabled:
            print(f"[DEBUG] Stash Response: {jsondata}")
            
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to connect to Stash: {e}")
        if debug_enabled:
            print(f"[DEBUG] Attempted URL: {stash_host}/graphql")
        return None
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        return None

    if jsondata.get("data") and jsondata["data"].get("findScenes"):
        scenes = jsondata["data"]["findScenes"]["scenes"]
        if scenes and len(scenes) == 1:
            movie = {}
            movie['MediaContainer'] = {}
            movie['MediaContainer']['offset'] = 0
            movie['MediaContainer']['totalSize'] = 1
            movie['MediaContainer']['identifier'] = "tv.plex.agents.custom.stash"
            movie['MediaContainer']['size'] = 1
            movie['MediaContainer']['Metadata'] = []
            scene = scenes[0]
            moviedata = {}
            moviedata['art'] = f"{stash_host}/scene/{scene['id']}/screenshot"
            moviedata['guid'] = f"plex://movie/stash-video-{scene['id']}"
            moviedata['key'] = f"/library/metadata/stash-video-{scene['id']}"
            moviedata['ratingKey'] = f"stash-video-{scene['id']}"
            moviedata['studio'] = scene.get('studio', {}).get('name')
            moviedata['type'] = "movie"
            moviedata['title'] = scene.get('title')
            moviedata['summary'] = scene.get('details')
            moviedata['originallyAvailableAt'] = scene.get('date')
            moviedata['year'] = int(scene.get('date', '0000')[:4]) if scene.get('date') else None
            for tag in scene.get('tags', []):
                genre = {}
                genre['tag'] = tag['name']
                moviedata.setdefault('Genre', []).append(genre)
            for performer in scene.get('performers', []):
                role = {}
                role['tag'] = performer['name']
                role['thumb'] = f"{stash_host}/performer/{performer['id']}/image"
                moviedata.setdefault('Role', []).append(role)
            movie['MediaContainer']['Metadata'].append(moviedata)
            return movie    

def query_stash_by_filename(filename):
    if filename:
        filename = filename.replace('"', r'\"')
        # Don't URL encode since we're using POST with JSON payload
        filter =  "path: {value: \"\\\"<FILENAME>\\\"\",modifier: INCLUDES}".replace("<FILENAME>", filename)
        movie = parse_stash_response(filter)
        if movie:
            return movie
    return None

def query_stash_by_ratingKey(ratingKey):
    if ratingKey:
        sceneid = re.search(r'-(\d+)$', ratingKey)
        if sceneid:
            sceneid = sceneid.group(1)
            filter = "id: {value: <SCENEID>,modifier: EQUALS}".replace("<SCENEID>", sceneid)
            movie = parse_stash_response(filter)
            if movie:
                return movie
    return None

@app.get("/")
async def root(response: Response):
    response.headers["X-Plex-Client-Identifier"] = "stash.plex.provider.metadata"
    return {
        "MediaProvider": {
            "identifier": "tv.plex.agents.custom.stash",
            "title": "Stash Plex Metadata Provider",
            "version": "1.0.0",
            "Types": [
                {
                    "type": 1,
                    "Scheme": [
                        {"scheme": "tv.plex.agents.custom.stash"}
                    ]
                },
            ],
            "Feature": [
                {"type": "metadata", "key": "/library/metadata"},
                {"type": "match", "key": "/library/metadata/matches"}
            ]
        }
    }

@app.post("/library/metadata/matches")
async def library_metadata_matches(request: Request):
    body = await request.json()
    filename = body.get("filename")
    if filename:
        result = query_stash_by_filename(filename)
        return result
    return None

@app.get("/library/metadata/{ratingKey}")
async def get_metadata(ratingKey: str):
    print(f"Fetching metadata for ratingKey: {ratingKey}")
    result = query_stash_by_ratingKey(ratingKey)
    return result

# For direct execution during development only
if __name__ == "__main__":
    # Load server config for direct execution
    server_host = config.get("plexagentserver", "host", fallback="0.0.0.0")
    server_port = int(config.get("plexagentserver", "port", fallback="7979"))
    uvicorn.run(app, host=server_host, port=server_port, reload=True)