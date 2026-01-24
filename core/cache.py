
import time, threading
class TTLCache:
    def __init__(self, ttl=120):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.store = {}
    def get(self, key):
        with self.lock:
            v = self.store.get(key)
            if not v: return None
            exp, val = v
            if exp < time.time():
                del self.store[key]; return None
            return val
    def set(self, key, value):
        with self.lock:
            self.store[key] = (time.time() + self.ttl, value)
    def invalidate(self, prefix=None):
        with self.lock:
            if prefix is None: self.store.clear(); return
            to_del = [k for k in self.store if k.startswith(prefix)]
            for k in to_del: del self.store[k]
CACHE = TTLCache(ttl=120)
