from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class CharacterManager:
    """Handles loading and ownership of One Piece characters."""

    def __init__(self, cog):
        self.cog = cog
        self.characters: dict[int, dict] = {}
        self.owners: dict[int, int] = {}

        self.load()

    # -----------------------
    # Loading
    # -----------------------

    def load(self):
        """Load all characters from the JSON file."""

        path = Path(__file__).parent / "data" / "characters.json"

        if not path.exists():
            self.characters = {}
            return

        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)

        self.characters = {}

        for character in data:
            self.characters[int(character["id"])] = character

    def reload(self):
        self.load()

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