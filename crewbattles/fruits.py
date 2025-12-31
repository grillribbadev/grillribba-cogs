from pathlib import Path
import json
import random

# data file inside the cog folder (used as fallback)
DATA = Path(__file__).parent / "data" / "fruits_cache.json"
DATA.parent.mkdir(parents=True, exist_ok=True)

# project-root fruits.json (one level up from the cog folder), used as primary persistent store
ROOT = Path(__file__).resolve().parents[1] / "fruits.json"

class FruitManager:
    def __init__(self):
        self._data = []
        self._load()

    def _load(self):
        # Prefer root fruits.json if it exists (keeps imports across reloads)
        if ROOT.exists():
            try:
                with ROOT.open("r", encoding="utf-8") as fh:
                    self._data = json.load(fh) or []
                    return
            except Exception:
                # fall back to DATA
                pass

        # Fall back to cog-local data file
        if DATA.exists():
            try:
                with DATA.open("r", encoding="utf-8") as fh:
                    self._data = json.load(fh) or []
                    return
            except Exception:
                pass

        # default: empty list
        self._data = []

    def _save(self):
        # Always attempt to save both: root (if writable) and cog data file.
        try:
            with DATA.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

        try:
            # attempt to write root file so imports survive reloads
            with ROOT.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
        except Exception:
            # ignore permission errors; cog will still have DATA fallback
            pass

    def all(self):
        return list(self._data)

    def get(self, name: str):
        if not name:
            return None
        nl = str(name).strip().lower()
        for f in self._data:
            if str(f.get("name", "")).strip().lower() == nl:
                return dict(f)
        return None

    def random(self):
        if not self._data:
            return None
        return dict(random.choice(self._data))

    def add(self, name: str, ftype: str, bonus: int, price: int, stock=None, ability: str = ""):
        fruit = {
            "name": str(name),
            "type": str(ftype),
            "bonus": int(bonus),
            "price": int(price),
            "stock": None if stock is None else int(stock),
            "ability": str(ability or ""),
        }
        existing = self.get(name)
        if existing:
            # update in-place
            for idx, it in enumerate(self._data):
                if str(it.get("name","")).strip().lower() == str(name).strip().lower():
                    self._data[idx] = fruit
                    break
        else:
            self._data.append(fruit)
        self._save()
        return fruit

    def update(self, fruit: dict):
        if not fruit or "name" not in fruit:
            return
        name = str(fruit["name"]).strip().lower()
        for idx, it in enumerate(self._data):
            if str(it.get("name","")).strip().lower() == name:
                self._data[idx] = fruit
                self._save()
                return
        self._data.append(fruit)
        self._save()
        return

    def import_json(self, json_obj):
        if isinstance(json_obj, str):
            parsed = json.loads(json_obj)
        else:
            parsed = json_obj
        if not isinstance(parsed, list):
            raise ValueError("Import must be a list of fruit objects")
        new = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            typ = item.get("type") or item.get("ftype")
            if not name or not typ:
                raise ValueError("Each fruit must include 'name' and 'type'")
            bonus = int(item.get("bonus", 0))
            price = int(item.get("price", 0))
            stock = item.get("stock", None)
            if stock is not None:
                try:
                    stock = int(stock)
                except Exception:
                    stock = None
            ability = str(item.get("ability", "") or "")
            new.append({
                "name": str(name),
                "type": str(typ),
                "bonus": bonus,
                "price": price,
                "stock": stock,
                "ability": ability,
            })
        self._data = new
        self._save()
        return len(new)
