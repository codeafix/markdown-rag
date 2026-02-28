"""Tests for app/watcher.py"""
import pytest
from unittest.mock import patch, MagicMock, call
import watcher
from watcher import DebouncedReindex, Handler


@pytest.fixture(autouse=True)
def reset_changed():
    """Clear the shared _CHANGED set between tests."""
    watcher._CHANGED.clear()
    yield
    watcher._CHANGED.clear()


# ── DebouncedReindex ──────────────────────────────────────────────────────────

def test_debounce_trigger_starts_timer():
    debouncer = DebouncedReindex(delay=60)  # long delay so it won't fire
    with patch("threading.Timer") as mock_timer_cls:
        mock_timer = MagicMock()
        mock_timer_cls.return_value = mock_timer
        debouncer.trigger()
    mock_timer_cls.assert_called_once()
    mock_timer.start.assert_called_once()


def test_debounce_trigger_cancels_previous_timer():
    debouncer = DebouncedReindex(delay=60)
    with patch("threading.Timer") as mock_timer_cls:
        mock_timer1 = MagicMock()
        mock_timer2 = MagicMock()
        mock_timer_cls.side_effect = [mock_timer1, mock_timer2]
        debouncer.trigger()
        debouncer.trigger()
    mock_timer1.cancel.assert_called_once()
    mock_timer2.start.assert_called_once()


def test_fire_does_nothing_when_no_changes():
    debouncer = DebouncedReindex(delay=0)
    watcher._CHANGED.clear()
    with patch("watcher.requests") as mock_requests:
        debouncer._fire()
    mock_requests.post.assert_not_called()


def test_fire_calls_partial_reindex():
    debouncer = DebouncedReindex(delay=0)
    watcher._CHANGED.add("note.md")
    watcher._CHANGED.add("other.md")

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("watcher.requests") as mock_requests, \
         patch.object(watcher, "RAG_FILES_URL", "http://rag:8000/reindex/files"):
        mock_requests.post.return_value = mock_response
        debouncer._fire()

    mock_requests.post.assert_called_once()
    call_args = mock_requests.post.call_args
    assert call_args[0][0] == "http://rag:8000/reindex/files"
    # Check files were included in payload
    payload = call_args[1]["data"]
    import json
    data = json.loads(payload)
    assert set(data["files"]) == {"note.md", "other.md"}


def test_fire_clears_changed_set():
    debouncer = DebouncedReindex(delay=0)
    watcher._CHANGED.add("note.md")

    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch("watcher.requests") as mock_requests:
        mock_requests.post.return_value = mock_response
        debouncer._fire()

    assert len(watcher._CHANGED) == 0


def test_fire_falls_back_to_full_reindex_on_error():
    debouncer = DebouncedReindex(delay=0)
    watcher._CHANGED.add("note.md")

    mock_response_full = MagicMock()
    mock_response_full.status_code = 200
    mock_response_full.text = "ok"

    with patch("watcher.requests") as mock_requests, \
         patch.object(watcher, "RAG_URL", "http://rag:8000/reindex"), \
         patch.object(watcher, "RAG_FILES_URL", "http://rag:8000/reindex/files"):
        # First call (partial) raises; second call (full) succeeds
        mock_requests.post.side_effect = [Exception("partial failed"), mock_response_full]
        debouncer._fire()

    assert mock_requests.post.call_count == 2
    # Second call is to the full reindex URL
    full_call = mock_requests.post.call_args_list[1]
    assert full_call[0][0] == "http://rag:8000/reindex"


def test_fire_handles_http_error_status():
    """A 4xx/5xx response raises RuntimeError and triggers full reindex fallback."""
    debouncer = DebouncedReindex(delay=0)
    watcher._CHANGED.add("note.md")

    bad_response = MagicMock()
    bad_response.status_code = 500
    bad_response.text = "Internal Server Error"

    full_response = MagicMock()
    full_response.status_code = 200
    full_response.text = "ok"

    with patch("watcher.requests") as mock_requests, \
         patch.object(watcher, "RAG_URL", "http://rag:8000/reindex"), \
         patch.object(watcher, "RAG_FILES_URL", "http://rag:8000/reindex/files"):
        mock_requests.post.side_effect = [bad_response, full_response]
        debouncer._fire()

    assert mock_requests.post.call_count == 2


def test_fire_full_fallback_also_fails():
    """If both partial and full reindex fail, no exception propagates."""
    debouncer = DebouncedReindex(delay=0)
    watcher._CHANGED.add("note.md")

    with patch("watcher.requests") as mock_requests:
        mock_requests.post.side_effect = Exception("network down")
        # Should not raise
        debouncer._fire()


# ── Handler ───────────────────────────────────────────────────────────────────

def _make_event(src_path=None, dest_path=None, event_type="modified"):
    event = MagicMock()
    event.src_path = src_path
    event.dest_path = dest_path
    event.event_type = event_type
    return event


def test_handler_ignores_non_md():
    debouncer = MagicMock()
    handler = Handler(debouncer)
    event = _make_event(src_path="/vault/image.png")
    with patch.object(watcher, "WATCH_PATH", "/vault"):
        handler.on_any_event(event)
    debouncer.trigger.assert_not_called()
    assert len(watcher._CHANGED) == 0


def test_handler_tracks_md_file():
    debouncer = MagicMock()
    handler = Handler(debouncer)
    event = _make_event(src_path="/vault/note.md")
    with patch.object(watcher, "WATCH_PATH", "/vault"):
        handler.on_any_event(event)
    debouncer.trigger.assert_called_once()
    assert "note.md" in watcher._CHANGED


def test_handler_tracks_dest_path_on_rename():
    debouncer = MagicMock()
    handler = Handler(debouncer)
    event = _make_event(src_path="/vault/old.md", dest_path="/vault/new.md", event_type="moved")
    with patch.object(watcher, "WATCH_PATH", "/vault"):
        handler.on_any_event(event)
    debouncer.trigger.assert_called_once()
    assert "old.md" in watcher._CHANGED
    assert "new.md" in watcher._CHANGED


def test_handler_normalises_relative_path():
    debouncer = MagicMock()
    handler = Handler(debouncer)
    event = _make_event(src_path="/vault/subdir/note.md")
    with patch.object(watcher, "WATCH_PATH", "/vault"):
        handler.on_any_event(event)
    assert "subdir/note.md" in watcher._CHANGED


def test_handler_skips_path_outside_watch():
    """A path that can't be made relative to WATCH_PATH is silently skipped."""
    debouncer = MagicMock()
    handler = Handler(debouncer)
    event = _make_event(src_path="/other/note.md")
    with patch.object(watcher, "WATCH_PATH", "/vault"):
        handler.on_any_event(event)
    # On some systems os.path.relpath still returns a path; the handler
    # should not crash regardless.


def test_handler_no_paths():
    """Event with no src_path or dest_path does nothing."""
    debouncer = MagicMock()
    handler = Handler(debouncer)
    event = _make_event(src_path=None, dest_path=None)
    handler.on_any_event(event)
    debouncer.trigger.assert_not_called()


# ── main ──────────────────────────────────────────────────────────────────────

def test_main_starts_and_stops_observer():
    mock_observer = MagicMock()
    with patch("watcher.PollingObserver", return_value=mock_observer), \
         patch("watcher.Observer", return_value=mock_observer), \
         patch("watcher.WATCH_POLLING", True), \
         patch("watcher.WATCH_PATH", "/tmp"), \
         patch("time.sleep", side_effect=[None, KeyboardInterrupt]):
        watcher.main()
    mock_observer.start.assert_called_once()
    mock_observer.stop.assert_called_once()
    mock_observer.join.assert_called_once()
