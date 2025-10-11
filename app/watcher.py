import os, time, threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import requests

WATCH_PATH = os.getenv("WATCH_PATH", "/vault")
RAG_URL = os.getenv("RAG_URL", "http://rag:8000/reindex")
DEBOUNCE = float(os.getenv("WATCH_DEBOUNCE_SECS", "3"))

class DebouncedReindex:
    def __init__(self, delay):
        self.delay = delay
        self.timer = None
        self.lock = threading.Lock()

    def trigger(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.delay, self._fire)
            self.timer.daemon = True
            self.timer.start()

    def _fire(self):
        try:
            print("Watcher: reindexing...")
            r = requests.post(RAG_URL, timeout=300)
            print("Watcher: reindex result:", r.status_code, r.text[:200])
        except Exception as e:
            print("Watcher: reindex error:", e)

class Handler(FileSystemEventHandler):
    def __init__(self, debouncer):
        self.debouncer = debouncer

    def on_any_event(self, event):
        # only care about .md changes
        path = getattr(event, "src_path", "") or ""
        if not path.lower().endswith(".md"):
            return
        print("Watcher: change detected:", event.event_type, path)
        self.debouncer.trigger()

def main():
    os.makedirs(WATCH_PATH, exist_ok=True)
    debouncer = DebouncedReindex(DEBOUNCE)
    event_handler = Handler(debouncer)
    observer = Observer()
    observer.schedule(event_handler, WATCH_PATH, recursive=True)
    observer.start()
    print(f"Watcher: monitoring {WATCH_PATH} ... (debounce={DEBOUNCE}s)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
