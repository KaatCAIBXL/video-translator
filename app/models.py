from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class Segment(BaseModel):
    start: float
    end: float
    text: str

class TranslationSegment(Segment):
    language: str

class VideoMetadata(BaseModel):
    id: str
    filename: str
    original_language: str
    sentence_pairs: List[Segment]
    translations: Dict[str, List[TranslationSegment]]  # bv. "en": [...]

class VideoListItem(BaseModel):
    id: str
    filename: str
    available_subtitles: List[str] = Field(default_factory=list)
    available_dubs: List[str] = Field(default_factory=list)
    available_dub_audios: List[str] = Field(default_factory=list)
    available_combined_subtitles: List[str] = Field(default_factory=list)
    folder_path: Optional[str] = None  # Path to folder containing this video
    is_private: bool = False  # Only visible to editors

class FolderItem(BaseModel):
    name: str
    path: str
    is_private: bool = False
    parent_path: Optional[str] = None
