import os
import time

from app.services import media_perception_cache as cache


def test_write_then_read_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "PERCEPTION_CACHE_DIR", tmp_path)
    cache.write_perception_bytes(1, 42, b"VOICEBYTES")
    assert cache.read_perception_bytes(1, 42) == b"VOICEBYTES"


def test_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "PERCEPTION_CACHE_DIR", tmp_path)
    assert cache.read_perception_bytes(1, 999) is None


def test_expired_returns_none_and_unlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "PERCEPTION_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "PERCEPTION_CACHE_TTL_SECONDS", 60)
    cache.write_perception_bytes(1, 7, b"OLD")
    # backdate the file well beyond the TTL
    p = cache._path(1, 7)
    old = time.time() - 3600
    os.utime(p, (old, old))
    assert cache.read_perception_bytes(1, 7) is None
    assert not p.exists()


def test_empty_data_is_not_written(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "PERCEPTION_CACHE_DIR", tmp_path)
    cache.write_perception_bytes(1, 8, b"")
    assert cache.read_perception_bytes(1, 8) is None


def test_eviction_caps_file_count(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "PERCEPTION_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "PERCEPTION_CACHE_MAX_FILES_PER_WORKSPACE", 3)
    for mid in range(6):
        cache.write_perception_bytes(2, mid, f"m{mid}".encode())
        time.sleep(0.01)  # distinct mtimes so eviction order is deterministic
    ws_dir = tmp_path / "2"
    remaining = sorted(p.name for p in ws_dir.iterdir() if p.is_file())
    assert len(remaining) <= 3
    # newest survive
    assert "5" in remaining
