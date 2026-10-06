import json
import glob
from pathlib import Path
from datetime import datetime
from m4bmaker.models import Book, BookMetadata, Chapter


def save_state(book: Book, folder: Path, prefs_state: dict) -> Path:
    """Save the current working state to a versioned JSON file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    state_file = folder / f"m4bmaker_state_{timestamp}.json"
    
    chapters_data = []
    for ch in book.chapters:
        chapters_data.append({
            "index": ch.index,
            "title": ch.title,
            "start_time": ch.start_time,
            "source_file": str(ch.source_file.name) if ch.source_file else None
        })

    data = {
        "version": 1,
        "timestamp": timestamp,
        "metadata": {
            "title": book.metadata.title,
            "author": book.metadata.author,
            "narrator": book.metadata.narrator,
            "genre": book.metadata.genre,
        },
        "cover": str(book.cover) if book.cover else None,
        "chapters": chapters_data,
        "prefs": prefs_state
    }
    
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
        
    return state_file


def find_latest_state(folder: Path) -> Path | None:
    """Find the most recent state file in the folder."""
    if not folder.is_dir():
        return None
    state_files = glob.glob(str(folder / "m4bmaker_state_*.json"))
    if not state_files:
        return None
    # Sort alphabetically will sort by timestamp YYYYMMDD_HHMMSS
    state_files.sort(reverse=True)
    return Path(state_files[0])


def load_state(state_file: Path, files: list[Path]) -> tuple[BookMetadata, list[Chapter], Path | None, dict]:
    """Load metadata, chapters, cover, and prefs from a state file."""
    with open(state_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    meta = BookMetadata(
        title=data.get("metadata", {}).get("title", ""),
        author=data.get("metadata", {}).get("author", ""),
        narrator=data.get("metadata", {}).get("narrator", ""),
        genre=data.get("metadata", {}).get("genre", ""),
    )
    
    cover = Path(data["cover"]) if data.get("cover") else None
    
    file_map = {f.name: f for f in files}
    
    chapters = []
    for ch_data in data.get("chapters", []):
        src_name = ch_data.get("source_file")
        src_path = file_map.get(src_name) if src_name else None
        chapters.append(Chapter(
            index=ch_data.get("index", 1),
            title=ch_data.get("title", ""),
            start_time=ch_data.get("start_time", 0.0),
            source_file=src_path
        ))
        
    prefs_state = data.get("prefs", {})
    
    return meta, chapters, cover, prefs_state
