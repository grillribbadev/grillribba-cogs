import json
from pathlib import Path

DATA = Path(__file__).parent / "data" / "fruits.json"

class FruitManager:
    def __init__(self):
        if not DATA.exists():
            DATA.write_text("[]")

    def all(self):
        return json.loads(DATA.read_text())

    def add(self, name, ftype, bonus):
        fruits = self.all()
        fruits.append({
            "name": name,
            "type": ftype,
            "bonus": bonus
        })
        DATA.write_text(json.dumps(fruits, indent=2))

    def random(self):
        import random
        fruits = self.all()
        if not fruits or random.random() < 0.35:
            return None
        return random.choice(fruits)
