import json
import random
from pathlib import Path

DATA = Path(__file__).parent / "data" / "fruits.json"

class FruitManager:
    def __init__(self):
        DATA.parent.mkdir(parents=True, exist_ok=True)
        if not DATA.exists():
            DATA.write_text("[]")

    def all(self):
        return json.loads(DATA.read_text())

    def add(self, name, ftype, bonus=0, price=25000, stock=None):
        data = self.all()
        data.append({
            "name": name,
            "type": ftype,
            "bonus": bonus,
            "price": price,
            "stock": stock,  # None = unlimited
        })
        self.save(data)

    def save(self, data):
        DATA.write_text(json.dumps(data, indent=2))

    def random(self):
        fruits = self.all()
        if not fruits or random.random() < 0.35:
            return None
        return random.choice(fruits)

    def get(self, name):
        return next((f for f in self.all() if f["name"].lower() == name.lower()), None)

    def update(self, fruit):
        data = self.all()
        for i, f in enumerate(data):
            if f["name"] == fruit["name"]:
                data[i] = fruit
                self.save(data)
                return
        # if not found, append and save
        data.append(fruit)
        self.save(data)
