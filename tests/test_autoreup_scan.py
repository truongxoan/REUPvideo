import os
import pytest
from autodub.autoreup import scan_videos_in_dir, VIDEO_EXTS


def test_scan_videos_recursive(tmp_path):
    # Tạo cấu trúc thư mục mẹ và các thư mục con
    parent = tmp_path / "folder_me"
    parent.mkdir()

    # Video ở folder mẹ
    (parent / "video_0.mp4").write_text("dummy")

    # Folder con 1
    sub1 = parent / "Tap_01"
    sub1.mkdir()
    (sub1 / "part1.mp4").write_text("dummy")
    (sub1 / "part2.mkv").write_text("dummy")
    (sub1 / "notes.txt").write_text("not a video")

    # Folder con 2 với sub-folder cháu
    sub2 = parent / "Tap_02" / "extra"
    sub2.mkdir(parents=True)
    (sub2 / "clip.mov").write_text("dummy")
    (sub2 / "thumb.jpg").write_text("dummy")

    # Folder con 10 (để test natural sort)
    sub10 = parent / "Tap_10"
    sub10.mkdir()
    (sub10 / "scene10.webm").write_text("dummy")

    # Quét thư mục mẹ
    results = scan_videos_in_dir(str(parent))

    # Kiểm tra số lượng video hợp lệ (5 video: video_0.mp4, part1.mp4, part2.mkv, clip.mov, scene10.webm)
    assert len(results) == 5

    # Kiểm tra không chứa các file không phải video
    basenames = [os.path.basename(r) for r in results]
    assert "notes.txt" not in basenames
    assert "thumb.jpg" not in basenames
    assert "video_0.mp4" in basenames
    assert "part1.mp4" in basenames
    assert "part2.mkv" in basenames
    assert "clip.mov" in basenames
    assert "scene10.webm" in basenames

    # Kiểm tra thứ tự natural sort
    # Tap_01 xuất hiện trước Tap_02 và Tap_10
    tap01_idx = next(i for i, p in enumerate(results) if "Tap_01" in p)
    tap10_idx = next(i for i, p in enumerate(results) if "Tap_10" in p)
    assert tap01_idx < tap10_idx


def test_scan_empty_or_invalid():
    assert scan_videos_in_dir("") == []
    assert scan_videos_in_dir("duong_dan_khong_ton_tai_xyz") == []


def test_unique_output_name_logic(tmp_path):
    # Kiểm tra thuật toán sinh tên không bị ghi đè
    out_dir = tmp_path / "thanh_pham"
    out_dir.mkdir()

    # Giả lập file đã tồn tại
    existing1 = out_dir / "Tap01_video_reup_dubbed.mp4"
    existing1.write_text("dummy")

    base_name = "video"
    parent_dir = "Tap01"
    video_name = f"{parent_dir}_{base_name}"

    final_output = os.path.join(str(out_dir), f"{video_name}_reup_dubbed.mp4")
    counter = 1
    while os.path.exists(final_output):
        final_output = os.path.join(str(out_dir), f"{video_name}_reup_dubbed_{counter}.mp4")
        counter += 1

    assert os.path.basename(final_output) == "Tap01_video_reup_dubbed_1.mp4"


def test_build_filter_complex_zoom_scale():
    from autodub.media.subtitle import build_filter_complex
    anti_copyright = {
        "enabled": True,
        "zoom": 1.04,
        "hflip": True,
        "color_shift": True,
    }
    fc = build_filter_complex(
        blur_regions=None,
        video_w=1080,
        video_h=1920,
        srt_path=None,
        anti_copyright=anti_copyright,
        auto_cover_subtitles=True,
    )
    assert fc is not None
    # Đảm bảo lệnh scale sau crop zoom dùng scale=1080:1920 thay vì scale=iw:ih
    assert "scale=1080:1920" in fc
    assert "scale=iw:ih" not in fc


def test_autoreup_cancel_batch(tmp_path):
    import threading
    from autodub.autoreup import AutoReupEngine, AutoReupJob
    from autodub.config import Settings

    cancel_event = threading.Event()
    cancel_event.set()  # kích hoạt hủy trước khi chạy

    engine = AutoReupEngine(settings=Settings(), cancel_event=cancel_event)
    jobs = [
        AutoReupJob(url_or_path="https://example.com/test1.mp4"),
        AutoReupJob(url_or_path="https://example.com/test2.mp4"),
    ]
    results = engine.process_batch(jobs)
    assert len(results) == 1
    assert results[0].status == "cancelled"


def test_filter_complex_respects_unchecked_flags():
    from autodub.media.subtitle import build_filter_complex

    # Trường hợp người dùng chỉ tích: hflip, canvas_blur, speed 1.03x
    # BỎ TÍCH: zoom/color_shift, vignette, auto_cover_subtitles
    anti_copyright = {
        "enabled": True,
        "hflip": True,
        "zoom": 1.0,
        "color_shift": False,
        "vignette": False,
        "canvas_blur": True,
    }
    fc = build_filter_complex(
        blur_regions=None,
        video_w=1080,
        video_h=1920,
        srt_path=None,
        anti_copyright=anti_copyright,
        auto_cover_subtitles=False,
    )
    assert fc is not None
    # Có hflip và canvas_blur
    assert "hflip" in fc
    assert "boxblur=24:6" in fc
    # KHÔNG CÓ color_shift (eq=...), KHÔNG CÓ vignette, KHÔNG CÓ crop zoom
    assert "eq=contrast=" not in fc
    assert "vignette" not in fc
    assert "crop=w=iw/" not in fc


def test_autoreup_fast_mode_skips_dub_pipeline(monkeypatch, tmp_path):
    from autodub.autoreup import AutoReupEngine, AutoReupJob
    from autodub.config import Settings

    # Tạo video giả lập
    fake_video = tmp_path / "sample.mp4"
    fake_video.write_text("fake video content")

    pipeline_called = False

    def _mock_pipeline_run(self, req):
        nonlocal pipeline_called
        pipeline_called = True
        raise RuntimeError("DubPipeline không được phép chạy trong chế độ Reup siêu tốc!")

    from autodub.pipeline import DubPipeline
    monkeypatch.setattr(DubPipeline, "run", _mock_pipeline_run)

    # Mock merge_video để không cần ffmpeg thật chạy trên file rỗng
    merge_called = False
    def _mock_merge_video(**kwargs):
        nonlocal merge_called
        merge_called = True
        # Ghi file đích giả lập
        out = kwargs["output_path"]
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            f.write("result")
        return out

    import autodub.autoreup
    monkeypatch.setattr(autodub.autoreup, "merge_video", _mock_merge_video)

    engine = AutoReupEngine(settings=Settings())
    job = AutoReupJob(
        url_or_path=str(fake_video),
        output_dir=str(tmp_path / "thanh_pham"),
        enable_dubbing=False,  # REUP SIÊU TỐC
        hflip=True,
        canvas_blur=True,
        speed=1.03,
        auto_cover_subtitles=False,
    )

    result = engine.process(job)
    assert result.status == "completed"
    assert not pipeline_called, "DubPipeline đã bị gọi dù enable_dubbing=False!"
    assert merge_called, "merge_video phải được gọi để render video!"


def test_autoreup_local_file_with_tiktok_url_in_path(monkeypatch, tmp_path):
    """Kiểm tra trường hợp thực tế: Thư mục trên máy tính có tên chứa link/domain TikTok

    (ví dụ: C:\\...\\httpswww.tiktok.com@_funny.268\\video.mp4).
    Hệ thống phải nhận diện đây là tệp cục bộ trên máy, KHÔNG được coi là URL tải về.
    """
    from autodub.autoreup import AutoReupEngine, AutoReupJob
    from autodub.config import Settings
    from autodub.media.douyin import extract_url_from_text, is_douyin_url

    # Tạo thư mục và file có tên chứa link TikTok như máy người dùng
    folder = tmp_path / "httpswww.tiktok.com@_funny.268"
    folder.mkdir()
    fake_video = folder / "fa8c803f-0bd6-4d1d-a15a-fe1b9e1ab2f1.mp4"
    fake_video.write_text("fake video content")

    path_str = str(fake_video)

    # 1. extract_url_from_text và is_douyin_url không được nhầm tệp cục bộ thành URL
    assert extract_url_from_text(path_str) == path_str
    assert not is_douyin_url(path_str)

    # 2. download_video không được phép gọi đối với tệp cục bộ
    def _mock_download_video(*args, **kwargs):
        raise RuntimeError("download_video không được phép gọi đối với tệp cục bộ trên máy!")

    import autodub.media.downloader
    monkeypatch.setattr(autodub.media.downloader, "download_video", _mock_download_video)

    merge_called = False
    def _mock_merge_video(**kwargs):
        nonlocal merge_called
        merge_called = True
        out = kwargs["output_path"]
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            f.write("result")
        return out

    import autodub.autoreup
    monkeypatch.setattr(autodub.autoreup, "merge_video", _mock_merge_video)

    engine = AutoReupEngine(settings=Settings())
    job = AutoReupJob(
        url_or_path=path_str,
        output_dir=str(tmp_path / "thanh_pham"),
        enable_dubbing=False,
    )

    result = engine.process(job)
    assert result.status == "completed"
    assert merge_called
    assert os.path.exists(result.output_video)
    # Tên video thành phẩm không chứa ký tự cấm và bảo toàn tiền tố thư mục
    assert "reup" in os.path.basename(result.output_video)


def test_autoreup_sequential_numbering(monkeypatch, tmp_path):
    from autodub.autoreup import AutoReupEngine, AutoReupJob
    from autodub.config import Settings

    # Tạo 3 video giả lập
    videos = []
    for i in range(3):
        v = tmp_path / f"source_{i}.mp4"
        v.write_text("dummy")
        videos.append(str(v))

    def _mock_merge_video(**kwargs):
        out = kwargs["output_path"]
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            f.write("result")
        return out

    import autodub.autoreup
    monkeypatch.setattr(autodub.autoreup, "merge_video", _mock_merge_video)
    engine = AutoReupEngine(settings=Settings())

    # 1. Đánh số thứ tự có đệm (01.mp4, 02.mp4, 03.mp4)
    out_dir1 = tmp_path / "out1"
    jobs1 = [
        AutoReupJob(url_or_path=v, output_dir=str(out_dir1), naming_mode="number", number_padding=True)
        for v in videos
    ]
    res1 = engine.process_batch(jobs1)
    names1 = [os.path.basename(r.output_video) for r in res1]
    assert names1 == ["01.mp4", "02.mp4", "03.mp4"]

    # 2. Đánh số thứ tự kèm tiền tố (Video_01.mp4, Video_02.mp4...)
    out_dir2 = tmp_path / "out2"
    jobs2 = [
        AutoReupJob(url_or_path=v, output_dir=str(out_dir2), naming_mode="number", number_prefix="Video_", number_padding=True)
        for v in videos
    ]
    res2 = engine.process_batch(jobs2)
    names2 = [os.path.basename(r.output_video) for r in res2]
    assert names2 == ["Video_01.mp4", "Video_02.mp4", "Video_03.mp4"]

    # 3. Đánh số không đệm (1.mp4, 2.mp4, 3.mp4)
    out_dir3 = tmp_path / "out3"
    jobs3 = [
        AutoReupJob(url_or_path=v, output_dir=str(out_dir3), naming_mode="number", number_padding=False)
        for v in videos
    ]
    res3 = engine.process_batch(jobs3)
    names3 = [os.path.basename(r.output_video) for r in res3]
    assert names3 == ["1.mp4", "2.mp4", "3.mp4"]

    # 4. Giữ tên gốc kèm hậu tố
    out_dir4 = tmp_path / "out4"
    jobs4 = [
        AutoReupJob(url_or_path=v, output_dir=str(out_dir4), naming_mode="original")
        for v in videos
    ]
    res4 = engine.process_batch(jobs4)
    names4 = [os.path.basename(r.output_video) for r in res4]
    assert len(names4) == 3
    assert all(n.endswith("_reup.mp4") for n in names4)
    assert "source_0" in names4[0]
    assert "source_1" in names4[1]
    assert "source_2" in names4[2]





