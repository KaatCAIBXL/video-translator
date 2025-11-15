from pydantic import BaseModel
from typing import List, Dict, Optional

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
    available_subtitles: List[str]
    available_dubs: List[str]
    available_audio: List[str]
