import json
import os

class Storage:
    def __init__(self, filepath):
        self.filepath = filepath
        if not os.path.exists(filepath):
            with open(filepath, "w") as f:
                json.dump({}, f)

    def _load(self):
        with open(self.filepath, "r") as f:
            return json.load(f)

    def _save(self, data):
        with open(self.filepath, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get(self, key, default=None):
        data = self._load()
        return data.get(key, default)

    def set(self, key, value):
        data = self._load()
        data[key] = value
        self._save(data)

    def delete(self, key):
        data = self._load()
        data.pop(key, None)
        self._save(data)
