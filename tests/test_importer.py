import time

from PIL import Image

from server import importer, walks


def _walk(conn):
    return walks.get_or_create_walk(conn, "2026-10-08")["id"]


def test_adds_photo_and_stores_resized_copy(conn, cfg, jpeg):
    r = importer.import_bytes(conn, cfg, _walk(conn), jpeg(size=(3000, 2000), seed=1), "a.jpg")
    assert r.status == "added" and r.discovery_id
    d = walks.get_discovery(conn, r.discovery_id)
    assert d["status"] == "imported"
    with Image.open(cfg.data_dir / d["photo_path"]) as im:
        assert max(im.size) <= cfg.display_max_side


def test_duplicate_is_skipped(conn, cfg, jpeg):
    wid = _walk(conn)
    data = jpeg(seed=2)
    assert importer.import_bytes(conn, cfg, wid, data, "a.jpg").status == "added"
    again = importer.import_bytes(conn, cfg, wid, data, "copy-of-a.jpg")
    assert again.status == "duplicate"
    assert conn.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0] == 1


def test_duplicate_detected_across_walks(conn, cfg, jpeg):
    data = jpeg(seed=3)
    importer.import_bytes(conn, cfg, _walk(conn), data, "a.jpg")
    other = walks.get_or_create_walk(conn, "2026-10-09")["id"]
    assert importer.import_bytes(conn, cfg, other, data, "a.jpg").status == "duplicate"


def test_different_photos_both_added(conn, cfg, jpeg):
    wid = _walk(conn)
    assert importer.import_bytes(conn, cfg, wid, jpeg(seed=4), "a.jpg").status == "added"
    assert importer.import_bytes(conn, cfg, wid, jpeg(seed=5), "b.jpg").status == "added"


def test_capture_date_from_exif(conn, cfg, jpeg):
    r = importer.import_bytes(conn, cfg, _walk(conn), jpeg(seed=6, exif_dt="2026:10:08 14:03:22"), "a.jpg",
                              file_mtime=time.time())
    assert r.captured_source == "exif"
    assert r.captured_at == "2026-10-08T14:03:22"


def test_capture_date_falls_back_to_file_date(conn, cfg, jpeg):
    mtime = time.mktime((2026, 10, 7, 9, 30, 0, 0, 0, -1))
    r = importer.import_bytes(conn, cfg, _walk(conn), jpeg(seed=7), "a.jpg", file_mtime=mtime)
    assert r.captured_source == "file"
    assert r.captured_at.startswith("2026-10-07T09:30")


def test_capture_date_unknown_without_any_source(conn, cfg, jpeg):
    r = importer.import_bytes(conn, cfg, _walk(conn), jpeg(seed=8), "a.jpg")
    assert r.captured_source == "unknown"


def test_stored_copy_has_no_exif_or_gps(conn, cfg, jpeg):
    r = importer.import_bytes(conn, cfg, _walk(conn), jpeg(seed=9, exif_dt="2026:10:08 10:00:00", gps=True), "a.jpg")
    d = walks.get_discovery(conn, r.discovery_id)
    with Image.open(cfg.data_dir / d["photo_path"]) as im:
        assert len(im.getexif()) == 0


def test_non_image_is_an_error_not_a_crash(conn, cfg):
    r = importer.import_bytes(conn, cfg, _walk(conn), b"this is not a photo", "notes.txt")
    assert r.status == "error"
    assert conn.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0] == 0


def test_import_paths_and_summary(conn, cfg, jpeg, tmp_path):
    a, b, bad = tmp_path / "a.jpg", tmp_path / "b.jpg", tmp_path / "bad.jpg"
    a.write_bytes(jpeg(seed=10)); b.write_bytes(jpeg(seed=10)); bad.write_bytes(b"nope")
    res = importer.import_paths(conn, cfg, _walk(conn), [a, b, bad])
    s = importer.summarize(res)
    assert s["added"] == 1 and s["duplicates"] == 1 and len(s["errors"]) == 1