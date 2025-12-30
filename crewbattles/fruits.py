from pathlib import Path
import json
import random

DATA = Path(__file__).parent / "data" / "fruits.json"

class FruitManager:
    def __init__(self):
        self._data = []
        self._load()

    def _load(self):
        try:
            if not DATA.exists():
                DATA.parent.mkdir(parents=True, exist_ok=True)
                DATA.write_text("[]", encoding="utf-8")
            with DATA.open("r", encoding="utf-8") as fh:
                self._data = json.load(fh) or []
        except Exception:
            self._data = []

    def _save(self):
        try:
            with DATA.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def all(self):
        """Return list of all fruits (shallow copy)."""
        return list(self._data)

    def get(self, name: str):
        """Case-insensitive lookup by name. Returns fruit dict or None."""
        if not name:
            return None
        name_l = name.strip().lower()
        for f in self._data:
            if f.get("name", "").strip().lower() == name_l:
                return f
        return None

    def random(self):
        """Return a random fruit dict or None if no fruits."""
        if not self._data:
            return None
        return random.choice(self._data)

    def add(self, name: str, ftype: str, bonus: int, price: int, stock=None):
        """Add a new fruit (overwrites if same name exists)."""
        fruit = {
            "name": str(name),
            "type": str(ftype).lower(),
            "bonus": int(bonus),
            "price": int(price),
            "stock": None if stock is None else int(stock),
        }
        # replace existing
        existing = self.get(name)
        if existing:
            # update existing in-place
            existing.update(fruit)
        else:
            self._data.append(fruit)
        self._save()
        return fruit

    def update(self, fruit: dict):
        """Update an existing fruit by name. If not found, append."""
        if not fruit or "name" not in fruit:
            return
        name_l = fruit["name"].strip().lower()
        for idx, f in enumerate(self._data):
            if f.get("name", "").strip().lower() == name_l:
                self._data[idx] = fruit
                self._save()
                return
        # not found -> append
        self._data.append(fruit)
        self._save()
        return
