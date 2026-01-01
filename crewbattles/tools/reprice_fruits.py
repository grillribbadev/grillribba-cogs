import json
from pathlib import Path

HERE = Path(__file__).resolve()
FRUITS_PATH = (HERE.parents[2] / "fruits.json")  # c:\cogs\fruits.json

# Base prices by rarity tier (edit these to taste)
BASE = {
    "paramecia": 400_000,
    "zoan": 650_000,
    "ancient zoan": 900_000,
    "logia": 1_200_000,
    "mythical zoan": 2_200_000,
}

PER_BONUS = {
    "paramecia": 70_000,
    "zoan": 85_000,
    "ancient zoan": 95_000,
    "logia": 120_000,
    "mythical zoan": 160_000,
}

PRICE_MIN = 100_000
PRICE_MAX = 25_000_000


def norm_type(t: str) -> str:
    t = " ".join((t or "").strip().lower().split())
    aliases = {
        "mythical": "mythical zoan",
        "mythic zoan": "mythical zoan",
    }
    return aliases.get(t, t)


def clamp(n: int) -> int:
    return max(PRICE_MIN, min(PRICE_MAX, int(n)))


def main():
    if not FRUITS_PATH.exists():
        raise SystemExit(f"fruits.json not found at: {FRUITS_PATH}")

    data = json.loads(FRUITS_PATH.read_text(encoding="utf-8"))
    fruits = data.get("fruits", [])
    if not isinstance(fruits, list):
        raise SystemExit("Invalid fruits.json: 'fruits' must be a list")

    changed = 0
    for f in fruits:
        if not isinstance(f, dict):
            continue
        t = norm_type(f.get("type", "paramecia"))
        base = BASE.get(t, BASE["paramecia"])
        per = PER_BONUS.get(t, PER_BONUS["paramecia"])
        bonus = int(f.get("bonus", 0) or 0)

        new_price = clamp(base + (bonus * per))
        old_price = int(f.get("price", 0) or 0)
        if old_price != new_price:
            f["price"] = new_price
            changed += 1

    FRUITS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Repriced {changed} fruit(s). Wrote: {FRUITS_PATH}")


if __name__ == "__main__":
    main()