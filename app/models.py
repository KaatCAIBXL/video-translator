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
    file_type: str = "video"  # video, audio, or text
    available_subtitles: List[str] = Field(default_factory=list)
    available_dubs: List[str] = Field(default_factory=list)
    available_dub_audios: List[str] = Field(default_factory=list)
    available_combined_subtitles: List[str] = Field(default_factory=list)
    has_transcription: bool = False  # Whether transcribed.txt exists
    folder_path: Optional[str] = None  # Path to folder containing this video
    is_private: bool = False  # Only visible to editors

class FolderItem(BaseModel):
    name: str
    path: str
    is_private: bool = False
    parent_path: Optional[str] = None

class Character(BaseModel):
    id: str
    name: str
    token: str  # Unique token for this character (e.g., "joepenna", "bingo")
    description: str  # Character description (e.g., "lief, lachend, dynamisch")
    class_word: str = "person"  # Class word for Dreambooth (person, dog, etc.)
    status: str = "pending"  # pending, training, completed, failed
    training_images_count: int = 0
    model_path: Optional[str] = None  # Path to trained model
    created_at: str = ""
    error: Optional[str] = None
