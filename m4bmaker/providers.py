import json
import re
import urllib.parse
import urllib.request
from typing import Optional

from m4bmaker.models import BookMetadata, Chapter

AUDNEX_REGIONS = ["us", "jp", "uk", "de", "ca", "au", "fr", "it", "in", "es"]


def parse_audible_input(text: str) -> tuple[str, Optional[str]]:
    """Extract a 10-char ASIN and region code from an Audible URL or raw ASIN.

    Examples:
        'B0841ZVX5R' -> ('B0841ZVX5R', None)
        'https://www.audible.co.jp/pd/B0841ZVX5R' -> ('B0841ZVX5R', 'jp')
        'https://www.audible.de/pd/B0...' -> ('B0...', 'de')
    """
    clean = text.strip()
    m_asin = re.search(r"\b([B0-9A-Z]{10})\b", clean)
    asin = m_asin.group(1) if m_asin else clean

    url_lower = clean.lower()
    region = None
    if "audible.co.jp" in url_lower:
        region = "jp"
    elif "audible.co.uk" in url_lower:
        region = "uk"
    elif "audible.de" in url_lower:
        region = "de"
    elif "audible.ca" in url_lower:
        region = "ca"
    elif "audible.com.au" in url_lower:
        region = "au"
    elif "audible.fr" in url_lower:
        region = "fr"
    elif "audible.it" in url_lower:
        region = "it"
    elif "audible.in" in url_lower:
        region = "in"
    elif "audible.es" in url_lower:
        region = "es"
    elif "audible.com" in url_lower:
        region = "us"

    return asin, region


def fetch_audnexus_data(
    asin: str, offset_seconds: float = 0.0, region: str = "auto"
) -> tuple[Optional[BookMetadata], Optional[list[Chapter]], Optional[str]]:
    metadata = None
    chapters = None
    cover_url = None
    headers = {"User-Agent": "m4bmaker/1.0"}

    parsed_asin, parsed_region = parse_audible_input(asin)
    actual_asin = parsed_asin or asin.strip()

    if region and region != "auto":
        regions_to_try = [region]
    elif parsed_region:
        regions_to_try = [parsed_region]
    else:
        regions_to_try = AUDNEX_REGIONS

    found_region: Optional[str] = None
    book_data = None

    for r in regions_to_try:
        try:
            book_url = f"https://api.audnex.us/books/{actual_asin}?region={r}"
            req = urllib.request.Request(book_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                book_data = json.loads(response.read().decode("utf-8"))
                found_region = r
                break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            continue
        except Exception:
            continue

    if book_data:
        title = book_data.get("title", "")
        author = ", ".join(a.get("name", "") for a in book_data.get("authors", []))
        narrator = ", ".join(n.get("name", "") for n in book_data.get("narrators", []))
        genres = book_data.get("genres", [])
        genre = genres[0].get("name", "") if genres else ""
        metadata = BookMetadata(title=title, author=author, narrator=narrator, genre=genre)
        cover_url = book_data.get("image", "")

    active_region = found_region or ("us" if region == "auto" else region)
    try:
        chapters_url = f"https://api.audnex.us/books/{actual_asin}/chapters?region={active_region}"
        req = urllib.request.Request(chapters_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            chap_data = json.loads(response.read().decode("utf-8"))
        raw_chapters = chap_data.get("chapters", [])
        if raw_chapters:
            chapters = []
            for i, c in enumerate(raw_chapters, start=1):
                start_time = max(0.0, (c.get("startOffsetMs", 0) / 1000.0) + offset_seconds)
                chapters.append(
                    Chapter(
                        index=i,
                        start_time=start_time,
                        title=c.get("title", f"Chapter {i}"),
                        source_file=None,
                    )
                )
    except Exception:
        pass

    return metadata, chapters, cover_url


def fetch_google_books_data(
    volume_id: str, api_key: str
) -> tuple[Optional[BookMetadata], Optional[list[Chapter]], Optional[str]]:
    metadata = None
    cover_url = None
    headers = {"User-Agent": "m4bmaker/1.0"}

    if not api_key:
        return None, None, None

    try:
        url = f"https://www.googleapis.com/books/v1/volumes/{volume_id}?key={api_key}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
        info = data.get("volumeInfo", {})
        title = info.get("title", "")
        authors = info.get("authors", [])
        author = ", ".join(authors) if authors else ""
        categories = info.get("categories", [])
        genre = categories[0] if categories else ""
        metadata = BookMetadata(title=title, author=author, narrator="", genre=genre)
        
        images = info.get("imageLinks", {})
        cover_url = images.get("extraLarge") or images.get("large") or images.get("thumbnail", "")
        cover_url = cover_url.replace("zoom=1", "zoom=0").replace("http:", "https:")
    except Exception:
        pass

    return metadata, None, cover_url


def fetch_itunes_data(
    itunes_id: str
) -> tuple[Optional[BookMetadata], Optional[list[Chapter]], Optional[str]]:
    metadata = None
    cover_url = None
    headers = {"User-Agent": "m4bmaker/1.0"}

    try:
        url = f"https://itunes.apple.com/lookup?id={itunes_id}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
        results = data.get("results", [])
        if results:
            info = results[0]
            title = info.get("collectionName", info.get("trackName", ""))
            author = info.get("artistName", "")
            genre = info.get("primaryGenreName", "")
            metadata = BookMetadata(title=title, author=author, narrator="", genre=genre)
            cover_url = info.get("artworkUrl100", "").replace("100x100bb", "600x600bb")
    except Exception:
        pass

    return metadata, None, cover_url
