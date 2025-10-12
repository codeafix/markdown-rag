import os, time, threading, json
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
import requests

WATCH_PATH = os.getenv("WATCH_PATH", "/vault")
RAG_URL = os.getenv("RAG_URL", "http://rag:8000/reindex")
DEBOUNCE = float(os.getenv("WATCH_DEBOUNCE_SECS", "3"))
# Default to polling in container mounts where inotify can be unreliable (Docker Desktop on macOS)
WATCH_POLLING = os.getenv("WATCH_POLLING", "true").lower() == "true"
RAG_FILES_URL = os.getenv("RAG_FILES_URL", "http://rag:8000/reindex/files")

# Track changed files (vault-relative POSIX paths)
_CHANGED: set[str] = set()
_CHANGED_LOCK = threading.Lock()

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
        # Snapshot and clear the changed set
        with _CHANGED_LOCK:
            files = sorted(_CHANGED)
            _CHANGED.clear()
        if not files:
            return
        # Try partial reindex first
        payload = {"files": files}
        headers = {"Content-Type": "application/json"}
        try:
            print(f"Watcher: reindexing {len(files)} file(s)...")
            r = requests.post(RAG_FILES_URL, data=json.dumps(payload), headers=headers, timeout=300)
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code} {r.text[:200]}")
            print("Watcher: partial reindex result:", r.status_code)
        except Exception as e:
            # Fallback: trigger full reindex
            try:
                print("Watcher: partial reindex failed, falling back to full reindex:", e)
                r = requests.post(RAG_URL, timeout=300)
                print("Watcher: full reindex result:", r.status_code, r.text[:200])
            except Exception as ee:
                print("Watcher: reindex error:", ee)

class Handler(FileSystemEventHandler):
    def __init__(self, debouncer):
        self.debouncer = debouncer

    def on_any_event(self, event):
        # only care about .md changes, including renames where dest_path matters
        paths = []
        src = getattr(event, "src_path", None)
        if src:
            paths.append(src)
        dst = getattr(event, "dest_path", None)
        if dst:
            paths.append(dst)
        md_paths = [p for p in paths if p and p.lower().endswith(".md")]
        if not md_paths:
            return
        # Normalize to vault-relative POSIX paths
        rels = []
        for p in md_paths:
            try:
                rel = os.path.relpath(p, WATCH_PATH)
            except ValueError:
                # If outside the watch path, skip
                continue
            rels.append(rel.replace('\\', '/'))
        if not rels:
            return
        with _CHANGED_LOCK:
            _CHANGED.update(rels)
        print("Watcher: change detected:", event.event_type, rels)
        self.debouncer.trigger()

def main():
    os.makedirs(WATCH_PATH, exist_ok=True)
    debouncer = DebouncedReindex(DEBOUNCE)
    event_handler = Handler(debouncer)
    observer = PollingObserver() if WATCH_POLLING else Observer()
    observer.schedule(event_handler, WATCH_PATH, recursive=True)
    observer.start()
    mode = "polling" if WATCH_POLLING else "inotify"
    print(f"Watcher: monitoring {WATCH_PATH} ... (debounce={DEBOUNCE}s, mode={mode})")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
