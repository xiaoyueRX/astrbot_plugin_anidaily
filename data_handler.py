import json
import os

class DataHandler:
    def __init__(self, data_dir):
        self.data_path = os.path.join(data_dir, "subscribers.json")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        self.subscribers = self._load()

    def _load(self):
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save(self):
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(self.subscribers, f, ensure_ascii=False, indent=2)

    def add_subscriber(self, origin):
        if origin not in self.subscribers:
            import datetime
            self.subscribers[origin] = {
                "subscribed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self._save()
            return True
        return False

    def remove_subscriber(self, origin):
        if origin in self.subscribers:
            del self.subscribers[origin]
            self._save()
            return True
        return False

    def get_subscribers(self):
        return self.subscribers
