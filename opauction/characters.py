from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


class CharacterManager:
    """Handles loading and ownership of One Piece characters."""

    def __init__(self, cog):
        self.cog = cog
        self.characters: dict[int, dict] = {}
        self.owners: dict[int, int] = {}

        self.load()

    # -----------------------
    # File and persistence helpers
    # -----------------------

    @property
    def data_path(self) -> Path:
        return Path(__file__).parent / "data" / "characters.json"

    def load(self):
        """Load all characters from the JSON file."""

        path = self.data_path

        if not path.exists():
            self.characters = {}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[]", encoding="utf-8")
            return

        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)

        self.load_roster(data)

    def load_roster(self, raw: list[dict]) -> bool:
        """Load a validated roster without writing to the deployed cog directory."""
        if not isinstance(raw, list):
            return False

        imported: dict[int, dict] = {}
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "name" not in item:
                return False
            character_id = int(item["id"])
            if character_id in imported:
                continue
            imported[character_id] = {
                "id": character_id,
                "name": str(item["name"]),
                "rarity": str(item.get("rarity", "Common")),
                "arc": str(item.get("arc", "Unknown")),
                "wiki": str(item.get("wiki", "")),
            }

        self.characters = imported
        return True

    def save(self):
        """Persist the canonical roster back to characters.json."""
        path = self.data_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(self.all(), fp, indent=2, ensure_ascii=False)
            fp.write("\n")

    def reload(self):
        self.load()

    def export_json(self) -> str:
        """Return a JSON payload suitable for import/export commands."""
        return json.dumps(self.all(), indent=2, ensure_ascii=False)

    def import_json(self, payload: str) -> bool:
        """Replace the roster from a JSON payload."""
        try:
            raw = json.loads(payload.lstrip("\ufeff"))
        except (TypeError, json.JSONDecodeError):
            return False
        if not self.load_roster(raw):
            return False
        self.save()
        return True

    def add(self, *, name: str, rarity: str = "Common", arc: str = "Unknown", wiki: str = "") -> Optional[dict]:
        """Add a new character to the roster and persist it."""
        normalized_name = " ".join(name.strip().split())
        if not normalized_name:
            return None

        if any(entry.get("name", "").lower() == normalized_name.lower() for entry in self.characters.values()):
            return None

        next_id = 1
        if self.characters:
            next_id = max(self.characters.keys()) + 1

        character = {
            "id": next_id,
            "name": normalized_name,
            "rarity": rarity or "Common",
            "arc": arc or "Unknown",
            "wiki": wiki or "",
        }

        self.characters[next_id] = character
        self.save()
        return character

    def remove(self, character_id: int) -> bool:
        """Remove a character from the canonical roster."""
        if character_id not in self.characters:
            return False
        removed = self.characters.pop(character_id)
        self.owners.pop(character_id, None)
        self.save()
        return removed is not None

    # -----------------------
    # Character Lookup
    # -----------------------

    def get(self, character_id: int) -> Optional[dict]:
        return self.characters.get(character_id)

    def get_by_name(self, name: str) -> Optional[dict]:
        name = name.lower()

        for character in self.characters.values():
            if character["name"].lower() == name:
                return character

        return None

    def exists(self, character_id: int) -> bool:
        return character_id in self.characters

    def all(self) -> list[dict]:
        return list(self.characters.values())

    def all_ids(self) -> list[int]:
        """Return all loaded character IDs."""
        return list(self.characters.keys())

    # -----------------------
    # Ownership
    # -----------------------

    async def rebuild_owners(self):
        """Rebuild ownership cache from Config."""

        self.owners.clear()

        for user_id, data in (await self.cog.config.all_users()).items():
            if not data.get("started"):
                continue

            for character_id in data.get("characters", []):
                self.owners[int(character_id)] = int(user_id)

    def owner_of(self, character_id: int) -> Optional[int]:
        return self.owners.get(character_id)

    def owned(self, character_id: int) -> bool:
        return character_id in self.owners

    def assign(self, character_id: int, user_id: int):
        self.owners[character_id] = user_id

    def unassign(self, character_id: int):
        self.owners.pop(character_id, None)

    # -----------------------
    # Pool
    # -----------------------

    async def available_pool(self) -> list[int]:
        """Return IDs of characters that have never been claimed."""

        available = []

        for character_id in self.characters:
            if character_id not in self.owners:
                available.append(character_id)

        return available

    async def random_pool_character(self) -> Optional[dict]:
        """Return a random unowned character."""

        import random

        pool = await self.available_pool()

        if not pool:
            return None

        return self.characters[random.choice(pool)]

    # -----------------------
    # Searching
    # -----------------------

    def search(self, text: str) -> list[dict]:
        """Simple partial search."""

        text = text.lower()

        results = []

        for character in self.characters.values():

            if text in character["name"].lower():
                results.append(character)

        return results