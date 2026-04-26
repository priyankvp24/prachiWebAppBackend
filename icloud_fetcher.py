"""
iCloud Shared Album Photo Fetcher
Uses Apple's private sharedstreams JSON API to fetch photos from iCloud Shared Albums.
"""
import re
import os
import requests

DEFAULT_PARTITION = "p01"

HEADERS = {
    "Origin": "https://www.icloud.com",
    "Referer": "https://www.icloud.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def _extract_token(album_url):
    """Extract the album token from an iCloud shared album URL."""
    # Handle URLs like https://www.icloud.com/sharedalbum/#B0ABC123
    match = re.search(r'#([A-Za-z0-9]+)', album_url)
    if match:
        return match.group(1)
    # Fallback: last path/fragment segment
    return album_url.rstrip('/').split('/')[-1].split('#')[-1]


def fetch_icloud_photos_selenium(album_url):
    """Public entry point: fetch photos from an iCloud Shared Album URL."""
    return fetch_icloud_photos_api(album_url)


def fetch_icloud_photos_api(album_url):
    """
    Fetch photos using Apple's private sharedstreams API.

    Flow:
      1. POST /sharedstreams/webstream  → get asset list + correct partition host
      2. POST /sharedstreams/webasseturls → get CDN download URLs
    """
    token = _extract_token(album_url)
    if not token:
        print("Could not extract album token from URL")
        return []

    print(f"Fetching iCloud album token: {token}")

    host = f"https://{DEFAULT_PARTITION}-sharedstreams.icloud.com"
    base_url = f"{host}/{token}/sharedstreams"

    # Step 1: fetch stream metadata
    try:
        resp = requests.post(
            f"{base_url}/webstream",
            headers=HEADERS,
            json={"streamCtag": None},
            timeout=30,
        )

        # Apple may redirect us to a different partition
        if resp.status_code == 330:
            new_host = resp.json().get("X-Apple-MMe-Host")
            if new_host:
                host = f"https://{new_host}"
                base_url = f"{host}/{token}/sharedstreams"
                resp = requests.post(
                    f"{base_url}/webstream",
                    headers=HEADERS,
                    json={"streamCtag": None},
                    timeout=30,
                )

        resp.raise_for_status()
        stream_data = resp.json()
    except Exception as e:
        print(f"Error fetching stream metadata: {e}")
        return []

    photos_meta = stream_data.get("photos", [])
    if not photos_meta:
        print("No photos found in stream metadata")
        return []

    print(f"Found {len(photos_meta)} photos in stream")

    # Step 2: fetch CDN URLs for all photos
    guids = [p["photoGuid"] for p in photos_meta if "photoGuid" in p]
    try:
        resp = requests.post(
            f"{base_url}/webasseturls",
            headers=HEADERS,
            json={"photoGuids": guids},
            timeout=30,
        )
        resp.raise_for_status()
        asset_urls = resp.json().get("items", {})
    except Exception as e:
        print(f"Error fetching asset URLs: {e}")
        asset_urls = {}

    photos = []
    for meta in photos_meta:
        guid = meta.get("photoGuid", "")
        derivatives = meta.get("derivatives", {})

        # Pick the largest derivative available
        best = None
        best_size = 0
        for key, deriv in derivatives.items():
            w = int(deriv.get("width", 0) or 0)
            if w > best_size:
                best_size = w
                best = deriv

        # Resolve the CDN URL: combine url_location (domain) + url_path (path+query)
        checksum = (best or {}).get("checksum", "")
        cdn_entry = asset_urls.get(checksum, {})
        location = cdn_entry.get("url_location", "")
        path = cdn_entry.get("url_path", "")
        full_url = ""
        if location and path:
            full_url = f"https://{location}{path}"
        elif location:
            full_url = f"https://{location}"

        if not full_url:
            continue

        filename = meta.get("caption") or f"photo_{len(photos)+1}.jpg"
        if not any(filename.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".heic")):
            filename += ".jpg"

        photos.append({
            "id": guid or f"icloud_{abs(hash(full_url)) % 1000000}",
            "filename": filename,
            "thumbnail_url": full_url,
            "full_url": full_url,
        })

    print(f"Resolved {len(photos)} photo URLs")
    return photos


def extract_photos_from_html(html):
    """Kept for import compatibility — no longer used for fetching."""
    return []


def get_local_photos(photos_dir, base_url='http://localhost:8080'):
    """
    Get photos from a local directory.
    This can be used as a fallback when iCloud fetching doesn't work.
    """
    photos = []
    
    if not os.path.exists(photos_dir):
        print(f"Local photos directory not found: {photos_dir}")
        return photos
    
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.heic', '.webp', '.gif'}
    
    for filename in os.listdir(photos_dir):
        ext = os.path.splitext(filename)[1].lower()
        if ext in allowed_extensions:
            photo_id = f'local_{abs(hash(filename)) % 1000000}'
            photos.append({
                'id': photo_id,
                'filename': filename,
                'thumbnail_url': f'{base_url}/photos/{filename}',
                'full_url': f'{base_url}/photos/{filename}',
                'is_local': True
            })
    
    print(f"Found {len(photos)} local photos")
    return photos


def extract_photos_from_html_improved(html):
    """
    Improved extraction - alias for extract_photos_from_html for compatibility.
    """
    return extract_photos_from_html(html)