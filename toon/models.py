from __future__ import annotations

from pydantic import BaseModel, Field


class Dialogue(BaseModel):
    speaker: str  # Character name, "NARRATOR", "SFX", or "UNKNOWN"
    text: str
    bubble_position: str = ""  # e.g. "top-left", "center", "bottom-right"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class PanelExtraction(BaseModel):
    panel_index: int
    image_file: str
    dialogues: list[Dialogue] = Field(default_factory=list)
    scene_description: str = ""


class CharacterProfile(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    speech_style: str = ""
    vocabulary_notes: str = ""
    personality: str = ""
    tone: str = ""
    example_lines: list[str] = Field(default_factory=list)
    # Critical for Vietnamese: pronoun pairs, sentence-ending particles, formality
    vietnamese_voice_guide: str = ""


class TranslatedDialogue(BaseModel):
    original: Dialogue
    translated_text: str
    translator_notes: str = ""
