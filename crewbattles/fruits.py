from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _norm(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _as_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


@dataclass
class Fruit:
    name: str
    type: str = "paramecia"
    bonus: int = 0
    price: int = 0
    ability: str = ""  # NEW

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "bonus": int(self.bonus),
            "price": int(self.price),
            "ability": self.ability or "",
        }

    @staticmethod
    def from_any(obj: Any) -> "Fruit":
        if not isinstance(obj, dict):
            raise ValueError("Fruit must be an object/dict")
        name = str(obj.get("name", "")).strip()
        if not name:
            raise ValueError("Fruit missing name")
        ftype = str(obj.get("type", "paramecia") or "paramecia").strip().lower()
        bonus = _as_int(obj.get("bonus", 0), 0)
        price = _as_int(obj.get("price", 0), 0)
        ability = str(obj.get("ability", "") or "").strip()
        return Fruit(name=name, type=ftype, bonus=bonus, price=price, ability=ability)


class FruitManager:
    """
    Two stores:
      - Pool (catalog): fruits you *can* stock in the shop
      - Shop (inventory): fruit names with per-item stock

    Back-compat:
      - all() / get() / update() operate on SHOP items (so cbshop/cbbuy keep working)
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._pool_path = self.data_dir / "fruits_pool.json"
        self._shop_path = self.data_dir / "fruits_shop.json"

        self._pool: Dict[str, Fruit] = {}
        self._shop: Dict[str, Optional[int]] = {}  # key -> stock (None = unlimited)
        self._load()

    # -------------------------
    # Persistence
    # -------------------------
    def _load(self):
        self._pool = {}
        self._shop = {}

        if self._pool_path.exists():
            data = json.loads(self._pool_path.read_text(encoding="utf-8"))
            # accept list[fruit] or {"fruits":[...]} or dict keyed by name
            fruits_raw = data.get("fruits") if isinstance(data, dict) else data
            if isinstance(fruits_raw, dict):
                fruits_raw = list(fruits_raw.values())
            if isinstance(fruits_raw, list):
                for item in fruits_raw:
                    try:
                        f = Fruit.from_any(item)
                        self._pool[_norm(f.name)] = f
                    except Exception:
                        continue

        if self._shop_path.exists():
            data = json.loads(self._shop_path.read_text(encoding="utf-8"))
            # accept dict name->stock OR {"shop":{...}}
            shop_raw = data.get("shop") if isinstance(data, dict) and "shop" in data else data
            if isinstance(shop_raw, dict):
                for name, stock in shop_raw.items():
                    key = _norm(name)
                    if stock is None:
                        self._shop[key] = None
                    else:
                        self._shop[key] = max(0, _as_int(stock, 0))

    def _save_pool(self):
        payload = {"fruits": [f.to_dict() for f in sorted(self._pool.values(), key=lambda x: _norm(x.name))]}
        self._pool_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _save_shop(self):
        # store original names? we only store normalized keys; that’s fine because we join with pool for display
        payload = {"shop": {k: v for k, v in sorted(self._shop.items(), key=lambda kv: kv[0])}}
        self._shop_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # -------------------------
    # Pool (catalog)
    # -------------------------
    def pool_all(self) -> List[dict]:
        return [f.to_dict() for f in sorted(self._pool.values(), key=lambda x: _norm(x.name))]

    def pool_get(self, name: str) -> Optional[dict]:
        f = self._pool.get(_norm(name))
        return f.to_dict() if f else None

    def pool_upsert(self, fruit_dict: dict) -> dict:
        f = Fruit.from_any(fruit_dict)
        self._pool[_norm(f.name)] = f
        self._save_pool()
        return f.to_dict()

    def pool_import(self, payload: Any) -> Tuple[int, int]:
        """
        Import into pool ONLY. Returns (added_or_updated, skipped).
        Accepts list[fruit] OR {"fruits":[...]} OR dict keyed by name.
        """
        fruits_raw = payload.get("fruits") if isinstance(payload, dict) else payload
        if isinstance(fruits_raw, dict):
            fruits_raw = list(fruits_raw.values())
        if not isinstance(fruits_raw, list):
            raise ValueError("Invalid format. Expected a list of fruits or {'fruits':[...]}")

        ok = 0
        bad = 0
        for item in fruits_raw:
            try:
                self.pool_upsert(item)
                ok += 1
            except Exception:
                bad += 1
        return ok, bad

    # -------------------------
    # Shop (inventory)
    # -------------------------
    def shop_list(self) -> List[dict]:
        """
        Returns list of fruit dicts (merged from pool) with 'stock' included.
        Only fruits present in shop are listed.
        """
        out = []
        for key, stock in self._shop.items():
            f = self._pool.get(key)
            if not f:
                # allow “dangling” shop entries, but show minimal
                out.append({"name": key, "type": "unknown", "bonus": 0, "price": 0, "ability": "", "stock": stock})
            else:
                d = f.to_dict()
                d["stock"] = stock
                out.append(d)
        out.sort(key=lambda x: _norm(x.get("name", "")))
        return out

    def shop_get(self, name: str) -> Optional[dict]:
        key = _norm(name)
        if key not in self._shop:
            return None
        f = self._pool.get(key)
        if not f:
            return {"name": name, "type": "unknown", "bonus": 0, "price": 0, "ability": "", "stock": self._shop.get(key)}
        d = f.to_dict()
        d["stock"] = self._shop.get(key)
        return d

    def shop_add(self, name: str, stock: Optional[int] = 1):
        key = _norm(name)
        if key not in self._pool:
            raise ValueError("Fruit not found in pool")
        if stock is None:
            self._shop[key] = None
        else:
            stock_i = max(0, _as_int(stock, 0))
            self._shop[key] = stock_i
        self._save_shop()

    def shop_set_stock(self, name: str, stock: Optional[int]):
        key = _norm(name)
        if key not in self._shop:
            raise ValueError("Fruit not in shop")
        if stock is None:
            self._shop[key] = None
        else:
            self._shop[key] = max(0, _as_int(stock, 0))
        self._save_shop()

    def shop_remove(self, name: str):
        key = _norm(name)
        if key in self._shop:
            del self._shop[key]
            self._save_shop()

    # -------------------------
    # Back-compat API used by your cog
    # -------------------------
    def all(self) -> List[dict]:
        # used by cbshop; return shop items
        return self.shop_list()

    def get(self, name: str) -> Optional[dict]:
        # used by cbbuy/battle bonus lookup; return shop entry (must exist in shop)
        return self.shop_get(name)

    def update(self, fruit_dict: dict):
        """
        Used by cbbuy to decrement stock.
        Expects dict containing at least: name + stock
        """
        if not isinstance(fruit_dict, dict):
            raise ValueError("fruit_dict must be dict")
        name = fruit_dict.get("name")
        if not name:
            raise ValueError("fruit_dict missing name")
        key = _norm(str(name))
        if key not in self._shop:
            raise ValueError("fruit not in shop")
        stock = fruit_dict.get("stock", None)
        if stock is None:
            self._shop[key] = None
        else:
            self._shop[key] = max(0, _as_int(stock, 0))
        self._save_shop()
