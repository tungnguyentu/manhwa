from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

DB_PATH: Path = Path("data/toon.db")


def set_db_path(path: Path) -> None:
    global DB_PATH
    DB_PATH = path


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                url_base TEXT NOT NULL DEFAULT '',
                source_language TEXT NOT NULL DEFAULT 'en',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL REFERENCES series(id),
                chapter_num INTEGER NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                scraped_at TEXT,
                extracted_at TEXT,
                translated_at TEXT,
                UNIQUE(series_id, chapter_num)
            );

            CREATE TABLE IF NOT EXISTS panels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL REFERENCES chapters(id),
                panel_index INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                scene_description TEXT NOT NULL DEFAULT '',
                UNIQUE(chapter_id, panel_index)
            );

            CREATE TABLE IF NOT EXISTS dialogues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                panel_id INTEGER NOT NULL REFERENCES panels(id),
                speaker TEXT NOT NULL,
                text TEXT NOT NULL,
                bubble_position TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0
            );

            CREATE TABLE IF NOT EXISTS character_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL REFERENCES series(id),
                name TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                speech_style TEXT NOT NULL DEFAULT '',
                vocabulary_notes TEXT NOT NULL DEFAULT '',
                personality TEXT NOT NULL DEFAULT '',
                tone TEXT NOT NULL DEFAULT '',
                example_lines_json TEXT NOT NULL DEFAULT '[]',
                vietnamese_voice_guide TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(series_id, name)
            );

            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dialogue_id INTEGER NOT NULL REFERENCES dialogues(id),
                translated_text TEXT NOT NULL,
                translator_notes TEXT NOT NULL DEFAULT '',
                profile_version TEXT NOT NULL DEFAULT '',
                target_lang TEXT NOT NULL DEFAULT 'vi',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(dialogue_id, target_lang)
            );

            CREATE TABLE IF NOT EXISTS series_style_guides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL REFERENCES series(id) UNIQUE,
                pronoun_rules TEXT NOT NULL DEFAULT '',
                sentence_particles TEXT NOT NULL DEFAULT '',
                formality_scale TEXT NOT NULL DEFAULT '',
                phrasing_patterns TEXT NOT NULL DEFAULT '',
                narrator_style TEXT NOT NULL DEFAULT '',
                taboo_choices TEXT NOT NULL DEFAULT '',
                example_pairs_json TEXT NOT NULL DEFAULT '[]',
                raw_guide TEXT NOT NULL DEFAULT '',
                source_series_slug TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)


# --- Series ---

def upsert_series(slug: str, title: str = "", url_base: str = "", source_language: str = "en") -> int:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO series (slug, title, url_base, source_language) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET title=excluded.title, url_base=excluded.url_base, "
            "source_language=excluded.source_language",
            (slug, title, url_base, source_language),
        )
        row = conn.execute("SELECT id FROM series WHERE slug=?", (slug,)).fetchone()
        return row["id"]


def get_series_id(slug: str) -> int | None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM series WHERE slug=?", (slug,)).fetchone()
        return row["id"] if row else None


def delete_series(series_id: int) -> None:
    """Delete a series and all related data (chapters, panels, dialogues, translations, profiles, style guide)."""
    with get_conn() as conn:
        # Delete translations
        conn.execute(
            "DELETE FROM translations WHERE dialogue_id IN ("
            "  SELECT d.id FROM dialogues d"
            "  JOIN panels p ON p.id = d.panel_id"
            "  JOIN chapters c ON c.id = p.chapter_id"
            "  WHERE c.series_id=?)", (series_id,)
        )
        # Delete dialogues
        conn.execute(
            "DELETE FROM dialogues WHERE panel_id IN ("
            "  SELECT p.id FROM panels p JOIN chapters c ON c.id = p.chapter_id WHERE c.series_id=?)",
            (series_id,)
        )
        conn.execute("DELETE FROM panels WHERE chapter_id IN (SELECT id FROM chapters WHERE series_id=?)", (series_id,))
        conn.execute("DELETE FROM chapters WHERE series_id=?", (series_id,))
        conn.execute("DELETE FROM character_profiles WHERE series_id=?", (series_id,))
        conn.execute("DELETE FROM series_style_guides WHERE series_id=?", (series_id,))
        conn.execute("DELETE FROM series WHERE id=?", (series_id,))


# --- Chapters ---

def upsert_chapter(series_id: int, chapter_num: int, url: str = "") -> int:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chapters (series_id, chapter_num, url) VALUES (?, ?, ?) "
            "ON CONFLICT(series_id, chapter_num) DO UPDATE SET url=excluded.url",
            (series_id, chapter_num, url),
        )
        row = conn.execute(
            "SELECT id FROM chapters WHERE series_id=? AND chapter_num=?",
            (series_id, chapter_num),
        ).fetchone()
        return row["id"]


def mark_chapter_scraped(chapter_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE chapters SET scraped_at=datetime('now') WHERE id=?", (chapter_id,)
        )


def mark_chapter_extracted(chapter_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE chapters SET extracted_at=datetime('now') WHERE id=?", (chapter_id,)
        )


def clear_extracted(chapter_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE chapters SET extracted_at=NULL WHERE id=?", (chapter_id,))


def mark_chapter_translated(chapter_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE chapters SET translated_at=datetime('now') WHERE id=?", (chapter_id,)
        )


def clear_translated(chapter_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM translations WHERE dialogue_id IN ("
            "  SELECT d.id FROM dialogues d JOIN panels p ON p.id = d.panel_id WHERE p.chapter_id=?"
            ")", (chapter_id,)
        )
        conn.execute("UPDATE chapters SET translated_at=NULL WHERE id=?", (chapter_id,))


def get_chapter(series_id: int, chapter_num: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM chapters WHERE series_id=? AND chapter_num=?",
            (series_id, chapter_num),
        ).fetchone()
        return dict(row) if row else None


def list_chapters(series_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chapters WHERE series_id=? ORDER BY chapter_num",
            (series_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# --- Panels & Dialogues ---

def save_panel(chapter_id: int, panel_index: int, image_path: str, scene_description: str) -> int:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO panels (chapter_id, panel_index, image_path, scene_description) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chapter_id, panel_index) DO UPDATE SET "
            "image_path=excluded.image_path, scene_description=excluded.scene_description",
            (chapter_id, panel_index, image_path, scene_description),
        )
        row = conn.execute(
            "SELECT id FROM panels WHERE chapter_id=? AND panel_index=?",
            (chapter_id, panel_index),
        ).fetchone()
        return row["id"]


def save_dialogue(panel_id: int, speaker: str, text: str, bubble_position: str, confidence: float) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO dialogues (panel_id, speaker, text, bubble_position, confidence) VALUES (?, ?, ?, ?, ?)",
            (panel_id, speaker, text, bubble_position, confidence),
        )
        return cur.lastrowid


def get_dialogues_for_chapter(chapter_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.speaker, d.text, d.bubble_position, d.confidence,
                   p.panel_index, p.scene_description, p.image_path
            FROM dialogues d
            JOIN panels p ON p.id = d.panel_id
            WHERE p.chapter_id = ?
            ORDER BY p.panel_index, d.id
            """,
            (chapter_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_dialogues_for_series(series_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.speaker, d.text, d.bubble_position, d.confidence,
                   p.panel_index, p.scene_description, c.chapter_num
            FROM dialogues d
            JOIN panels p ON p.id = d.panel_id
            JOIN chapters c ON c.id = p.chapter_id
            WHERE c.series_id = ?
            ORDER BY c.chapter_num, p.panel_index, d.id
            """,
            (series_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_panels_for_chapter(chapter_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM translations WHERE dialogue_id IN ("
            "  SELECT d.id FROM dialogues d JOIN panels p ON p.id = d.panel_id WHERE p.chapter_id=?"
            ")",
            (chapter_id,),
        )
        conn.execute(
            "DELETE FROM dialogues WHERE panel_id IN (SELECT id FROM panels WHERE chapter_id=?)",
            (chapter_id,),
        )
        conn.execute("DELETE FROM panels WHERE chapter_id=?", (chapter_id,))


# --- Character Profiles ---

def upsert_character_profile(
    series_id: int,
    name: str,
    aliases: list[str],
    speech_style: str,
    vocabulary_notes: str,
    personality: str,
    tone: str,
    example_lines: list[str],
    vietnamese_voice_guide: str,
) -> int:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO character_profiles
                (series_id, name, aliases_json, speech_style, vocabulary_notes,
                 personality, tone, example_lines_json, vietnamese_voice_guide, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(series_id, name) DO UPDATE SET
                aliases_json=excluded.aliases_json,
                speech_style=excluded.speech_style,
                vocabulary_notes=excluded.vocabulary_notes,
                personality=excluded.personality,
                tone=excluded.tone,
                example_lines_json=excluded.example_lines_json,
                vietnamese_voice_guide=excluded.vietnamese_voice_guide,
                updated_at=datetime('now')
            """,
            (
                series_id, name, json.dumps(aliases), speech_style,
                vocabulary_notes, personality, tone,
                json.dumps(example_lines), vietnamese_voice_guide,
            ),
        )
        row = conn.execute(
            "SELECT id FROM character_profiles WHERE series_id=? AND name=?",
            (series_id, name),
        ).fetchone()
        return row["id"]


def get_character_profiles(series_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM character_profiles WHERE series_id=? ORDER BY name",
            (series_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["aliases"] = json.loads(d.pop("aliases_json"))
            d["example_lines"] = json.loads(d.pop("example_lines_json"))
            result.append(d)
        return result


# --- Translations ---

def save_translation(
    dialogue_id: int,
    translated_text: str,
    translator_notes: str,
    profile_version: str,
    target_lang: str = "vi",
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO translations
                (dialogue_id, translated_text, translator_notes, profile_version, target_lang)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(dialogue_id, target_lang) DO UPDATE SET
                translated_text=excluded.translated_text,
                translator_notes=excluded.translator_notes,
                profile_version=excluded.profile_version,
                created_at=datetime('now')
            """,
            (dialogue_id, translated_text, translator_notes or "", profile_version, target_lang),
        )


def get_translations_for_chapter(chapter_id: int, target_lang: str = "vi") -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT d.id as dialogue_id, d.speaker, d.text as original_text,
                   t.translated_text, t.translator_notes,
                   p.panel_index, p.scene_description
            FROM dialogues d
            JOIN panels p ON p.id = d.panel_id
            LEFT JOIN translations t ON t.dialogue_id = d.id AND t.target_lang = ?
            WHERE p.chapter_id = ?
            ORDER BY p.panel_index, d.id
            """,
            (target_lang, chapter_id),
        ).fetchall()
        return [dict(r) for r in rows]


# --- Series Style Guides ---

def upsert_style_guide(
    series_id: int,
    pronoun_rules: str,
    sentence_particles: str,
    formality_scale: str,
    phrasing_patterns: str,
    narrator_style: str,
    taboo_choices: str,
    example_pairs: list[dict],
    raw_guide: str,
    source_series_slug: str = "",
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO series_style_guides
                (series_id, pronoun_rules, sentence_particles, formality_scale,
                 phrasing_patterns, narrator_style, taboo_choices,
                 example_pairs_json, raw_guide, source_series_slug, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(series_id) DO UPDATE SET
                pronoun_rules=excluded.pronoun_rules,
                sentence_particles=excluded.sentence_particles,
                formality_scale=excluded.formality_scale,
                phrasing_patterns=excluded.phrasing_patterns,
                narrator_style=excluded.narrator_style,
                taboo_choices=excluded.taboo_choices,
                example_pairs_json=excluded.example_pairs_json,
                raw_guide=excluded.raw_guide,
                source_series_slug=excluded.source_series_slug,
                updated_at=datetime('now')
            """,
            (
                series_id, pronoun_rules, sentence_particles, formality_scale,
                phrasing_patterns, narrator_style, taboo_choices,
                json.dumps(example_pairs, ensure_ascii=False), raw_guide, source_series_slug,
            ),
        )


def get_style_guide(series_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM series_style_guides WHERE series_id=?", (series_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["example_pairs"] = json.loads(d.pop("example_pairs_json"))
        return d
