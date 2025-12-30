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
            DATA.parent.mkdir(parents=True, exist_ok=True)
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

    def add(self, name: str, ftype: str, bonus: int, price: int, stock=None, ability: str = ""):
        """Add a new fruit (overwrites if same name exists)."""
        fruit = {
            "name": str(name),
            "type": str(ftype),
            "bonus": int(bonus),
            "price": int(price),
            "stock": None if stock is None else int(stock),
            "ability": str(ability or "")
        }
        existing = self.get(name)
        if existing:
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
        self._data.append(fruit)
        self._save()
        return

    def import_json(self, json_obj):
        """
        Replace current shop with provided JSON data.
        json_obj can be a list of fruit objects or a JSON string.
        Each fruit must include: name, type, bonus, price, ability.
        stock is optional (use null for unlimited).
        """
        # accept raw string
        if isinstance(json_obj, str):
            try:
                parsed = json.loads(json_obj)
            except Exception as e:
                raise ValueError(f"Invalid JSON: {e}")
        else:
            parsed = json_obj

        if not isinstance(parsed, list):
            raise ValueError("Imported JSON must be a list of fruit objects.")

        new_list = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            ftype = item.get("type") or item.get("ftype")
            if not name or not ftype:
                raise ValueError(f"Each fruit must include 'name' and 'type'. Problem: {item}")
            bonus = int(item.get("bonus", 0))
            price = int(item.get("price", 0))
            stock = item.get("stock", None)
            if stock is not None:
                try:
                    stock = int(stock)
                except Exception:
                    stock = None
            ability = str(item.get("ability", ""))  # special ability text
            new_list.append({
                "name": str(name),
                "type": str(ftype),
                "bonus": bonus,
                "price": price,
                "stock": stock,
                "ability": ability,
            })

        # override current shop
        self._data = new_list
        self._save()
        return len(new_list)
