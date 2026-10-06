import json
import urllib.request
import urllib.parse
from typing import Optional

from m4bmaker.models import BookMetadata, Chapter


def fetch_audnexus_data(
    asin: str, offset_seconds: float = 0.0
) -> tuple[Optional[BookMetadata], Optional[list[Chapter]], Optional[str]]:
    metadata = None
    chapters = None
    cover_url = None
    headers = {"User-Agent": "m4bmaker/1.0"}

    try:
        book_url = f"https://api.audnex.us/books/{asin}"
        req = urllib.request.Request(book_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            book_data = json.loads(response.read().decode())
        title = book_data.get("title", "")
        author = ", ".join(a.get("name", "") for a in book_data.get("authors", []))
        narrator = ", ".join(n.get("name", "") for n in book_data.get("narrators", []))
        genres = book_data.get("genres", [])
        genre = genres[0].get("name", "") if genres else ""
        metadata = BookMetadata(title=title, author=author, narrator=narrator, genre=genre)
        cover_url = book_data.get("image", "")
    except Exception:
        pass

    try:
        chapters_url = f"https://api.audnex.us/books/{asin}/chapters"
        req = urllib.request.Request(chapters_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            chap_data = json.loads(response.read().decode())
        chapters = []
        for i, c in enumerate(chap_data.get("chapters", []), start=1):
            start_time = max(0.0, (c.get("startOffsetMs", 0) / 1000.0) + offset_seconds)
            chapters.append(Chapter(index=i, start_time=start_time, title=c.get("title", f"Chapter {i}"), source_file=None))
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
