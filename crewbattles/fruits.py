from pathlib import Path
import json
import random
import shutil

# cog-local cache (fallback)
DATA = Path(__file__).parent / "data" / "fruits_cache.json"
DATA.parent.mkdir(parents=True, exist_ok=True)

# root-level fruits.json (persistent across reloads)
ROOT = Path(__file__).resolve().parents[1] / "fruits.json"

class FruitManager:
    def __init__(self):
        # make sure ROOT exists (prefer it). If not, try to populate it from DATA or create empty.
        self._ensure_root_exists()
        self._data = []
        self._load()

    def _ensure_root_exists(self):
        try:
            if not ROOT.exists():
                # prefer cog-local DATA if present
                if DATA.exists():
                    try:
                        shutil.copy2(DATA, ROOT)
                        print(f"[CrewBattles] fruits: copied {DATA} -> {ROOT}")
                    except Exception as e:
                        print(f"[CrewBattles] fruits: failed to copy DATA to ROOT: {e}")
                        try:
                            ROOT.write_text("[]", encoding="utf-8")
                            print(f"[CrewBattles] fruits: created empty {ROOT}")
                        except Exception as e2:
                            print(f"[CrewBattles] fruits: failed to create ROOT file: {e2}")
                else:
                    try:
                        ROOT.write_text("[]", encoding="utf-8")
                        print(f"[CrewBattles] fruits: created empty {ROOT}")
                    except Exception as e:
                        print(f"[CrewBattles] fruits: failed to create ROOT file: {e}")
            # ensure DATA exists too so cog-local fallback remains
            if not DATA.exists():
                try:
                    DATA.write_text("[]", encoding="utf-8")
                    print(f"[CrewBattles] fruits: created empty {DATA}")
                except Exception as e:
                    print(f"[CrewBattles] fruits: failed to create DATA file: {e}")
        except Exception as e:
            print(f"[CrewBattles] fruits: unexpected _ensure_root_exists error: {e}")

    def _load(self):
        # Prefer ROOT (persistent); fallback to DATA
        try:
            if ROOT.exists():
                with ROOT.open("r", encoding="utf-8") as fh:
                    self._data = json.load(fh) or []
                    return
        except Exception as e:
            print(f"[CrewBattles] fruits: failed to load ROOT {ROOT}: {e}")

        try:
            if DATA.exists():
                with DATA.open("r", encoding="utf-8") as fh:
                    self._data = json.load(fh) or []
                    return
        except Exception as e:
            print(f"[CrewBattles] fruits: failed to load DATA {DATA}: {e}")

        self._data = []

    def _save(self):
        # persist to both DATA and ROOT; print diagnostics on error
        try:
            with DATA.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CrewBattles] fruits: failed to write DATA {DATA}: {e}")

        try:
            with ROOT.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CrewBattles] fruits: failed to write ROOT {ROOT}: {e}")
            # try an atomic fallback via temp file
            try:
                tmp = ROOT.with_suffix(".tmp")
                with tmp.open("w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, ensure_ascii=False, indent=2)
                tmp.replace(ROOT)
                print(f"[CrewBattles] fruits: wrote ROOT via tmp {tmp}")
            except Exception as e2:
                print(f"[CrewBattles] fruits: atomic write fallback failed: {e2}")

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
