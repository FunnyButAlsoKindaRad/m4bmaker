import json
import urllib.request
from typing import Optional

from m4bmaker.models import BookMetadata, Chapter


def fetch_audnexus_data(
    asin: str, offset_seconds: float = 0.0
) -> tuple[Optional[BookMetadata], Optional[list[Chapter]]]:
    """Fetch metadata and chapters from the Audnexus API."""
    metadata = None
    chapters = None

    headers = {"User-Agent": "m4bmaker/1.0"}

    # Fetch metadata
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

        metadata = BookMetadata(
            title=title, author=author, narrator=narrator, genre=genre
        )
    except Exception as e:
        print(f"Failed to fetch metadata from Audnexus: {e}")

    # Fetch chapters
    try:
        chapters_url = f"https://api.audnex.us/books/{asin}/chapters"
        req = urllib.request.Request(chapters_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            chap_data = json.loads(response.read().decode())

        chapters = []
        for i, c in enumerate(chap_data.get("chapters", []), start=1):
            start_time = max(0.0, (c.get("startOffsetMs", 0) / 1000.0) + offset_seconds)
            title = c.get("title", f"Chapter {i}")
            chapters.append(
                Chapter(
                    index=i,
                    start_time=start_time,
                    title=title,
                    source_file=None,
                )
            )
    except Exception as e:
        print(f"Failed to fetch chapters from Audnexus: {e}")

    return metadata, chapters
