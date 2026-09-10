"""Trình điều phối Quy trình Tự động Hóa Toàn diện (End-to-End Auto Reup).

Quy trình khép kín:
1. Nhận URL video (Douyin, TikTok, YouTube, Bilibili, ...)
2. Tải video không logo
3. Tách BGM (Demucs) + Nhận dạng lời thoại (Whisper/Paraformer)
4. Dịch sang tiếng Việt + Lồng tiếng AI (VieNeu-TTS)
5. Xóa phụ đề gốc (Boxblur/Delogo) + Lách bản quyền (Zoom 4%, Lật hình, Đổi màu nhẹ)
6. Render xuất video thành phẩm (.mp4)
7. Tự động tải lên các nền tảng (TikTok / YouTube Shorts / Facebook Reels)
"""
from __future__ import annotations

import glob
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import random
import re
import subprocess

from autodub.config import Settings
from autodub.media.douyin import extract_url_from_text
from autodub.media.uploader import AutoUploader
from autodub.media.video import merge_video, probe_has_audio
from autodub.pipeline import DubPipeline, DubRequest, DubResult, source_video_path
from autodub.progress import PipelineCancelled, ProgressEvent
from autodub.utils import app_root, setup_logging
from autodub.workdir import data_path

logger = setup_logging("autodub.autoreup")

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts")


def scan_videos_in_dir(dir_path: str) -> list[str]:
    """Quét đệ quy toàn bộ thư mục mẹ và tất cả các thư mục con để tìm tệp video.

    Kết quả trả về được sắp xếp theo thứ tự tự nhiên (natural sort) để các tập/phần
    giữ đúng thứ tự (ví dụ: ep1, ep2, ep10...).
    """
    if not dir_path or not os.path.isdir(dir_path):
        return []

    found = []
    for root, _, files in os.walk(dir_path):
        for f in files:
            if f.lower().endswith(VIDEO_EXTS):
                found.append(os.path.abspath(os.path.join(root, f)))

    def _natural_sort_key(s: str):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]

    found.sort(key=_natural_sort_key)
    return found


def _pick_random_bgm(bgm_dir: str) -> str | None:
    """Chọn ngẫu nhiên một tệp âm thanh trong thư mục bgm/."""
    if not os.path.exists(bgm_dir):
        return None
    valid_exts = (".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac")
    candidates = [
        os.path.join(bgm_dir, f)
        for f in os.listdir(bgm_dir)
        if os.path.isfile(os.path.join(bgm_dir, f)) and f.lower().endswith(valid_exts)
    ]
    if not candidates:
        return None
    return random.choice(candidates)


def _mix_replacement_bgm(
    dubbed_audio_path: str,
    bgm_path: str,
    output_path: str,
    volume_db: float = -18.0,
) -> bool:
    """Hòa trộn nhạc nền BGM thay thế vào bản thu giọng đọc AI bằng FFmpeg."""
    vol_linear = 10.0 ** (volume_db / 20.0)
    cmd = [
        "ffmpeg", "-y",
        "-i", dubbed_audio_path,
        "-stream_loop", "-1",
        "-i", bgm_path,
        "-filter_complex",
        f"[1:a]volume={vol_linear:.4f}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "[aout]",
        "-c:a", "pcm_s16le",
        output_path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.warning(f"Lỗi khi trộn nhạc nền BGM: {e}")
        return False


@dataclass
class AutoReupJob:
    url_or_path: str
    target_platform: str = "none"        # "tiktok" | "youtube" | "all" | "none"
    custom_caption: str = ""
    tags: list[str] = field(default_factory=lambda: ["reup", "shorts", "trending", "reviewphim"])
    enable_dubbing: bool = False         # Mặc định False: Reup Siêu Tốc (bỏ qua Demucs, Whisper, Dịch, TTS)
    hflip: bool = False
    zoom: float = 1.04
    canvas_blur: bool = False            # Khung nền mờ PIP
    speed: float = 1.03                  # Tăng tốc độ video & audio (1.03x)
    vignette: bool = False               # Tối nhẹ 4 góc
    reup_bg_mode: str = "original"       # "replace" (thay bằng bgm/) | "none" (tắt) | "original" (giữ gốc)
    auto_cover_subtitles: bool = False   # Tự động che mờ dải phụ đề gốc ở chân video
    sub_cover_height_pct: float = 0.14
    output_dir: str = ""                 # Thư mục xuất video (mặc định để trống sẽ lưu vào thanh_pham/)
    naming_mode: str = "original"        # "number" (đổi tên theo số thứ tự) | "original" (giữ tên gốc)
    number_prefix: str = ""              # Tiền tố khi đánh số (vd: "Video_" -> "Video_001.mp4", để trống -> "001.mp4")
    number_start: int = 1                # Số thứ tự bắt đầu (mặc định: 1)
    number_padding: bool = True          # Tự động đệm số 0 (001, 002... dựa theo tổng số video)


def _build_output_filename(
    job: AutoReupJob,
    job_idx: int,
    total_jobs: int,
    raw_path: str,
    is_url: bool,
    source_video: str | None,
    mode_suffix: str = "reup",
) -> tuple[str, str]:
    """Tạo tên tệp video thành phẩm và tên hiển thị cơ bản theo cấu hình đặt tên.

    Trả về: (tên_tệp_hoàn_chỉnh.mp4, tên_hiển_thị_cơ_bản)
    """
    if job.naming_mode == "number":
        seq = job.number_start + (job_idx - 1)
        if job.number_padding:
            max_num = job.number_start + max(total_jobs - 1, 0)
            pad_width = max(len(str(max_num)), 2)
            num_str = f"{seq:0{pad_width}d}"
        else:
            num_str = str(seq)

        prefix = (job.number_prefix or "").strip()
        if prefix:
            prefix = re.sub(r'[\\/*?:"<>|]', "_", prefix)
            base = f"{prefix}{num_str}"
        else:
            base = num_str
        return f"{base}.mp4", base

    # Chế độ mặc định: Giữ tên gốc kèm hậu tố
    if not is_url and os.path.isfile(raw_path):
        base_name = os.path.splitext(os.path.basename(raw_path))[0]
        parent_dir = os.path.basename(os.path.dirname(os.path.abspath(raw_path)))
        if parent_dir and parent_dir not in ("\\", "/", ":", "."):
            video_name = f"{parent_dir}_{base_name}"
        else:
            video_name = base_name
    else:
        video_name = os.path.splitext(os.path.basename(source_video or "reup_video.mp4"))[0]

    video_name = re.sub(r'[\\/*?:"<>|]', "_", video_name).strip() or "reup_video"
    return f"{video_name}_{mode_suffix}.mp4", video_name


@dataclass
class AutoReupResult:
    url: str
    status: str                         # "completed" | "failed" | "skipped"
    output_video: str = ""
    uploaded_platforms: list[str] = field(default_factory=list)
    error_message: str = ""


class AutoReupEngine:
    """Động cơ thực thi quy trình Reup tự động."""

    def __init__(
        self,
        settings: Settings | None = None,
        progress_cb: Callable[[str, float], None] | None = None,
        cancel_event: threading.Event | None = None,
    ):
        self.settings = settings or Settings.load()
        self.progress_cb = progress_cb or (lambda msg, pct: None)
        self.cancel_event = cancel_event
        self.uploader = AutoUploader()

    def _log_progress(self, msg: str, pct: float = 0.0) -> None:
        logger.info(f"[{pct:.0f}%] {msg}")
        self.progress_cb(msg, pct)

    def process(self, job: AutoReupJob, job_idx: int = 1, total_jobs: int = 1) -> AutoReupResult:
        """Xử lý trọn gói 1 video từ tải về đến đăng bài."""
        import re

        raw_path = str(job.url_or_path or "").strip().strip('"\'')

        # 1. Nhận diện tệp cục bộ trên máy tính (kể cả khi tên thư mục chứa "tiktok.com", "douyin.com", ...)
        is_local_file = os.path.isfile(raw_path) or os.path.isfile(os.path.abspath(raw_path))
        is_windows_path = bool(re.match(r"^[a-zA-Z]:[\\/]", raw_path) or raw_path.startswith(("\\\\", "//")))

        if is_local_file or (is_windows_path and not raw_path.startswith(("http://", "https://"))):
            is_url = False
            clean_url = ""
            source_video = os.path.abspath(raw_path)
            item_name = os.path.basename(source_video)
        else:
            clean_url = extract_url_from_text(raw_path)
            is_url = bool(
                clean_url.startswith(("http://", "https://"))
                or re.match(r"^(?:https?://)?(?:[a-zA-Z0-9-]+\.)*(?:douyin\.com|tiktok\.com|youtube\.com|youtu\.be)(?:/|$)", clean_url, re.IGNORECASE)
            )
            if is_url and not clean_url.startswith(("http://", "https://")):
                clean_url = "https://" + clean_url
            item_name = os.path.basename(raw_path) if not is_url else clean_url
            source_video = os.path.abspath(raw_path) if not is_url else None

        def _report(msg: str, job_pct: float) -> None:
            if total_jobs > 1:
                global_pct = ((job_idx - 1) + (job_pct / 100.0)) / total_jobs * 100.0
                prefix = f"[{job_idx}/{total_jobs}] "
            else:
                global_pct = job_pct
                prefix = ""
            self._log_progress(f"{prefix}{msg}", global_pct)

        if self.cancel_event and self.cancel_event.is_set():
            return AutoReupResult(
                url=clean_url if is_url else raw_path,
                status="cancelled",
                error_message="Đã dừng theo yêu cầu của người dùng",
            )

        # =====================================================================
        # 1. CHẾ ĐỘ REUP SIÊU TỐC (FAST REUP - Bỏ qua Demucs, Whisper, Dịch, TTS)
        # =====================================================================
        if not job.enable_dubbing:
            try:
                _report(f"🚀 Bắt đầu Reup siêu tốc: {item_name}", 5.0)

                # Thu thập video nguồn
                if is_url:
                    _report("Đang tải video nguồn về máy...", 12.0)
                    temp_dl_dir = os.path.join(app_root(), "downloads", "reup_fast")
                    from autodub.media.downloader import download_video
                    source_video = download_video(clean_url, temp_dl_dir)
                else:
                    if not source_video:
                        source_video = os.path.abspath(raw_path)

                if not source_video or not os.path.isfile(source_video):
                    raise FileNotFoundError(f"Không tìm thấy video nguồn: {raw_path}")

                if self.cancel_event and self.cancel_event.is_set():
                    return AutoReupResult(
                        url=clean_url if is_url else raw_path,
                        status="cancelled",
                        error_message="Đã hủy theo yêu cầu của người dùng",
                    )

                # Xử lý âm thanh
                has_audio = probe_has_audio(source_video)
                audio_to_use = source_video if has_audio else None

                if job.reup_bg_mode == "replace":
                    bgm_folder = self.settings.bgm_folder
                    if not os.path.isabs(bgm_folder):
                        bgm_folder = os.path.join(app_root(), bgm_folder)
                    picked_bgm = _pick_random_bgm(bgm_folder)
                    if picked_bgm:
                        _report(f"Hòa trộn nhạc nền miễn phí: {os.path.basename(picked_bgm)}", 25.0)
                        bgm_mixed = os.path.join(
                            os.path.dirname(source_video),
                            f"bgm_mixed_{os.path.splitext(os.path.basename(source_video))[0]}.wav"
                        )
                        ok = _mix_replacement_bgm(source_video, picked_bgm, bgm_mixed, volume_db=self.settings.bgm_volume_db)
                        if ok:
                            audio_to_use = bgm_mixed
                    else:
                        _report("Thư mục bgm/ trống — giữ nguyên âm thanh gốc", 25.0)
                else:
                    # Giữ nguyên âm thanh gốc, không tách Demucs, người dùng ghép nhạc sau
                    _report("Giữ nguyên âm thanh gốc (không thay nhạc, người dùng sẽ ghép nhạc sau)...", 25.0)

                # Cấu hình thư mục lưu
                if job.output_dir:
                    out_dir = job.output_dir if os.path.isabs(job.output_dir) else os.path.join(app_root(), job.output_dir)
                else:
                    out_dir = os.path.join(app_root(), "thanh_pham")
                os.makedirs(out_dir, exist_ok=True)

                out_filename, video_base_name = _build_output_filename(
                    job=job,
                    job_idx=job_idx,
                    total_jobs=total_jobs,
                    raw_path=raw_path,
                    is_url=is_url,
                    source_video=source_video,
                    mode_suffix="reup",
                )
                final_output = os.path.join(out_dir, out_filename)
                base_f, ext_f = os.path.splitext(out_filename)
                counter = 1
                while os.path.exists(final_output):
                    final_output = os.path.join(out_dir, f"{base_f}_{counter}{ext_f}")
                    counter += 1

                # CHỈ BẬT ĐÚNG NHỮNG GÌ NGƯỜI DÙNG ĐÃ TÍCH CHỌN:
                has_zoom = float(job.zoom or 1.0) > 1.001
                has_speed = abs(float(job.speed or 1.0) - 1.0) > 0.005
                anti_copyright = {
                    "enabled": bool(job.hflip or has_zoom or job.canvas_blur or job.vignette),
                    "hflip": bool(job.hflip),
                    "zoom": float(job.zoom) if has_zoom else 1.0,
                    "color_shift": bool(has_zoom),
                    "canvas_blur": bool(job.canvas_blur),
                    "vignette": bool(job.vignette),
                    "speed": float(job.speed) if has_speed else 1.0,
                }

                _report("Đang xử lý video bằng bộ lọc lách bản quyền siêu tốc...", 45.0)
                merge_video(
                    video_path=source_video,
                    audio_path=audio_to_use,
                    output_path=final_output,
                    srt_path=None,
                    subtitle_mode="none",
                    subtitle_style=None,
                    anti_copyright=anti_copyright,
                    speed=float(job.speed) if has_speed else None,
                    auto_cover_subtitles=bool(job.auto_cover_subtitles),
                    sub_cover_height_pct=job.sub_cover_height_pct,
                )

                _report(f"Xuất video hoàn tất: {os.path.basename(final_output)}", 88.0)

                # Tự động đăng tải nếu có chọn nền tảng
                uploaded = []
                if job.target_platform != "none":
                    caption = job.custom_caption or f"Video {video_base_name}"
                    if job.target_platform in ("tiktok", "all"):
                        _report("Đang đăng video lên TikTok Studio...", 92.0)
                        ok = self.uploader.upload_to_tiktok(final_output, caption=caption, tags=job.tags)
                        if ok:
                            uploaded.append("tiktok")

                    if job.target_platform in ("youtube", "all"):
                        _report("Đang đăng video lên YouTube Studio...", 96.0)
                        ok = self.uploader.upload_to_youtube(final_output, title=caption, tags=job.tags)
                        if ok:
                            uploaded.append("youtube")

                _report("Hoàn tất 100%!", 100.0)
                return AutoReupResult(
                    url=clean_url if is_url else raw_path,
                    status="completed",
                    output_video=final_output,
                    uploaded_platforms=uploaded,
                )

            except PipelineCancelled:
                logger.info(f"Quy trình Reup bị hủy bởi người dùng: {raw_path}")
                return AutoReupResult(url=clean_url if is_url else raw_path, status="cancelled", error_message="Đã hủy bởi người dùng")
            except Exception as e:
                logger.error(f"Lỗi quy trình Auto Reup siêu tốc: {e}", exc_info=True)
                return AutoReupResult(url=clean_url if is_url else raw_path, status="failed", error_message=str(e))

        # =====================================================================
        # 2. CHẾ ĐỘ REUP KÈM LỒNG TIẾNG AI (Full Dubbing Pipeline)
        # =====================================================================
        _report(f"Bắt đầu quy trình Lồng tiếng AI & Reup cho: {item_name}", 2.0)

        # Quyết định chế độ âm thanh nền gốc
        bg_mode = "none"
        if job.reup_bg_mode == "original":
            bg_mode = "demucs" if self.settings.hq_background else "duck"

        # Chuẩn bị yêu cầu Lồng tiếng (skip_video=True để tránh render video 2 lần lãng phí)
        req = DubRequest(
            url=clean_url if is_url else None,
            file_path=os.path.abspath(raw_path) if not is_url else None,
            source_lang=self.settings.default_source_lang,
            voice=self.settings.vieneu_voice or None,
            bg_mode=bg_mode,
            subtitle_mode="burn",
            skip_video=True,
            defer_export=False,
        )

        def _pipeline_progress(ev: ProgressEvent) -> None:
            if self.cancel_event and self.cancel_event.is_set():
                raise PipelineCancelled("Người dùng đã hủy quy trình")
            # Map từng bước sang thang % tương đối trong lượt xử lý này (5% - 75%)
            step_ranges = {
                "acquire": (5.0, 15.0, "Đang tải / kiểm tra video"),
                "extract": (15.0, 22.0, "Đang trích xuất âm thanh"),
                "separate": (22.0, 32.0, "Đang tách lời thoại & nhạc nền"),
                "asr": (32.0, 52.0, "Đang nhận dạng lời thoại AI"),
                "translate": (52.0, 60.0, "Đang dịch sang tiếng Việt"),
                "tts": (60.0, 72.0, "Đang tạo giọng đọc AI"),
                "merge_audio": (72.0, 75.0, "Đang đồng bộ và ghép giọng đọc"),
                "content": (75.0, 76.0, "Đang tạo metadata mạng xã hội"),
            }
            if ev.step in step_ranges:
                start_p, end_p, label = step_ranges[ev.step]
                if ev.total and ev.total > 0 and ev.current is not None:
                    ratio = min(1.0, max(0.0, ev.current / ev.total))
                    pct = start_p + (end_p - start_p) * ratio
                    if ev.step == "tts":
                        msg = f"Đang tạo giọng đọc AI ({ev.current}/{ev.total})"
                    else:
                        msg = f"{label} ({ev.current}/{ev.total})"
                else:
                    pct = start_p if ev.status == "start" else (end_p if ev.status == "done" else (start_p + end_p) / 2)
                    msg = label
                _report(msg, pct)

        pipeline = DubPipeline(self.settings, progress=_pipeline_progress, cancel_event=self.cancel_event)

        try:
            _report("Đang tải video và nhận dạng lời thoại AI...", 5.0)
            res: DubResult = pipeline.run(req)

            if res.status != "completed":
                return AutoReupResult(
                    url=clean_url or job.url_or_path,
                    status="failed",
                    error_message=f"Pipeline lồng tiếng dừng ở trạng thái: {res.status}",
                )

            work_dir = res.work_dir
            _report("Lồng tiếng hoàn tất. Đang chuẩn bị âm thanh và bộ lọc lách bản quyền...", 76.0)

            # Tìm tệp video gốc (source_video)
            source_video = None
            src_json = data_path(work_dir, "source_video.json")
            if os.path.exists(src_json):
                try:
                    with open(src_json, encoding="utf-8") as f:
                        cand = json.load(f).get("file_path")
                        if cand and os.path.exists(cand):
                            source_video = cand
                except Exception:
                    pass

            if not source_video and not is_url and os.path.isfile(raw_path):
                source_video = os.path.abspath(raw_path)

            if not source_video:
                source_video = source_video_path(work_dir)

            if not source_video:
                for ext in VIDEO_EXTS:
                    cands = [f for f in glob.glob(os.path.join(work_dir, f"*{ext}")) if not os.path.basename(f).startswith("dubbed_video")]
                    if cands:
                        source_video = cands[0]
                        break

            if not source_video or not os.path.exists(source_video):
                raise FileNotFoundError(f"Không tìm thấy video gốc trong {work_dir}")

            # Phân giải tệp âm thanh lồng tiếng (audio_vi_full.wav)
            audio_cands = [
                data_path(work_dir, "audio_vi_full.wav"),
                os.path.join(work_dir, "audio_vi_full.wav"),
                data_path(work_dir, "dubbed_audio.wav"),
                os.path.join(work_dir, "dubbed_audio.wav"),
            ]
            dubbed_audio = next((p for p in audio_cands if os.path.isfile(p) and os.path.getsize(p) > 0), None)
            if not dubbed_audio:
                wavs = glob.glob(os.path.join(work_dir, "**", "audio_*_full.wav"), recursive=True)
                if wavs:
                    dubbed_audio = wavs[0]
            if not dubbed_audio and os.path.isfile(source_video):
                dubbed_audio = source_video
            if not dubbed_audio:
                raise FileNotFoundError(f"Không tìm thấy tệp âm thanh lồng tiếng sau khi chạy trong {work_dir}")

            # Phân giải tệp phụ đề (transcript_vi.srt / transcript_karaoke.ass)
            sub_style = self.settings.subtitle_style()
            # Nếu kiểu phụ đề karaoke được chọn, sinh tệp karaoke .ass
            if (sub_style or {}).get("display") == "karaoke":
                try:
                    from autodub.text.subtitles import refresh_subtitles
                    from autodub.languages import get_target
                    target = get_target("vi")
                    tr_path = data_path(work_dir, target.transcript_name)
                    if os.path.isfile(tr_path):
                        with open(tr_path, encoding="utf-8") as f:
                            segs = json.load(f).get("segments", [])
                        refresh_subtitles(
                            segs, work_dir, target, sub_style,
                            merge_dir=data_path(work_dir, "segments_post"),
                            settings=self.settings,
                            for_burn=True,
                        )
                except Exception as exc:
                    logger.debug(f"Không tạo được karaoke .ass: {exc}")

            srt_cands = [
                data_path(work_dir, "transcript_karaoke.ass"),
                os.path.join(work_dir, "transcript_vi.srt"),
                data_path(work_dir, "transcript_vi.srt"),
                os.path.join(work_dir, "subtitles_vi.ass"),
                os.path.join(work_dir, "subtitles_vi.srt"),
                data_path(work_dir, "subtitles_vi.ass"),
                data_path(work_dir, "subtitles_vi.srt"),
            ]
            srt_path = next((p for p in srt_cands if os.path.isfile(p) and os.path.getsize(p) > 0), None)

            # Xử lý thay thế BGM bằng kho nhạc không bản quyền nếu bật chế độ 'replace'
            if job.reup_bg_mode == "replace":
                bgm_folder = self.settings.bgm_folder
                if not os.path.isabs(bgm_folder):
                    bgm_folder = os.path.join(app_root(), bgm_folder)
                picked_bgm = _pick_random_bgm(bgm_folder)
                if picked_bgm:
                    _report(f"Hòa trộn nhạc nền miễn phí bản quyền: {os.path.basename(picked_bgm)}", 78.0)
                    mixed_audio = data_path(work_dir, "dubbed_audio_mixed_bgm.wav")
                    ok = _mix_replacement_bgm(dubbed_audio, picked_bgm, mixed_audio, volume_db=self.settings.bgm_volume_db)
                    if ok:
                        dubbed_audio = mixed_audio
                else:
                    _report("Thư mục bgm/ trống — dùng âm thanh thuần để tránh vi phạm bản quyền nhạc gốc", 78.0)

            if job.output_dir:
                out_dir = job.output_dir if os.path.isabs(job.output_dir) else os.path.join(app_root(), job.output_dir)
            else:
                out_dir = os.path.join(app_root(), "thanh_pham")
            os.makedirs(out_dir, exist_ok=True)

            out_filename, video_base_name = _build_output_filename(
                job=job,
                job_idx=job_idx,
                total_jobs=total_jobs,
                raw_path=raw_path,
                is_url=is_url,
                source_video=source_video,
                mode_suffix="reup_dubbed",
            )
            final_output = os.path.join(out_dir, out_filename)
            base_f, ext_f = os.path.splitext(out_filename)
            counter = 1
            while os.path.exists(final_output):
                final_output = os.path.join(out_dir, f"{base_f}_{counter}{ext_f}")
                counter += 1

            # Áp dụng ĐÚNG các bộ lọc lách bản quyền người dùng đã tích chọn
            has_zoom = float(job.zoom or 1.0) > 1.001
            has_speed = abs(float(job.speed or 1.0) - 1.0) > 0.005
            anti_copyright = {
                "enabled": bool(job.hflip or has_zoom or job.canvas_blur or job.vignette),
                "hflip": bool(job.hflip),
                "zoom": float(job.zoom) if has_zoom else 1.0,
                "color_shift": bool(has_zoom),
                "canvas_blur": bool(job.canvas_blur),
                "vignette": bool(job.vignette),
                "speed": float(job.speed) if has_speed else 1.0,
            }

            _report("Đang render video với bộ lọc lách bản quyền nâng cao...", 82.0)
            has_valid_sub = bool(srt_path and os.path.isfile(srt_path) and os.path.getsize(srt_path) > 0)
            merge_video(
                video_path=source_video,
                audio_path=dubbed_audio,
                output_path=final_output,
                srt_path=srt_path if has_valid_sub else None,
                subtitle_mode="burn" if has_valid_sub else "none",
                subtitle_style=self.settings.subtitle_style(),
                anti_copyright=anti_copyright,
                speed=float(job.speed) if has_speed else None,
                auto_cover_subtitles=job.auto_cover_subtitles,
                sub_cover_height_pct=job.sub_cover_height_pct,
            )

            _report(f"Xuất video hoàn chỉnh thành công: {os.path.basename(final_output)}", 88.0)

            # 2. Tự động Đăng bài (Auto Upload)
            uploaded = []
            if job.target_platform != "none":
                caption = job.custom_caption or f"Phim hay lồng tiếng Việt {video_base_name}"
                if job.target_platform in ("tiktok", "all"):
                    _report("Đang đăng video lên TikTok Studio...", 92.0)
                    ok = self.uploader.upload_to_tiktok(final_output, caption=caption, tags=job.tags)
                    if ok:
                        uploaded.append("tiktok")

                if job.target_platform in ("youtube", "all"):
                    _report("Đang đăng video lên YouTube Studio...", 96.0)
                    ok = self.uploader.upload_to_youtube(final_output, title=caption, tags=job.tags)
                    if ok:
                        uploaded.append("youtube")

            _report("Hoàn tất 100%!", 100.0)
            return AutoReupResult(
                url=clean_url if is_url else raw_path,
                status="completed",
                output_video=final_output,
                uploaded_platforms=uploaded,
            )

        except PipelineCancelled:
            logger.info(f"Quy trình Auto Reup bị hủy bởi người dùng: {raw_path}")
            return AutoReupResult(
                url=clean_url if is_url else raw_path,
                status="cancelled",
                error_message="Đã hủy bởi người dùng",
            )
        except Exception as e:
            logger.error(f"Lỗi quy trình Auto Reup: {e}", exc_info=True)
            return AutoReupResult(
                url=clean_url if is_url else raw_path,
                status="failed",
                error_message=str(e),
            )

    def process_batch(self, jobs: list[AutoReupJob]) -> list[AutoReupResult]:
        """Xử lý danh sách nhiều video liên tục."""
        results = []
        total = len(jobs)
        for idx, job in enumerate(jobs, 1):
            if self.cancel_event and self.cancel_event.is_set():
                self._log_progress("⏹️ Đã dừng quy trình Reup theo yêu cầu của người dùng.", (idx - 1) / total * 100)
                results.append(AutoReupResult(
                    url=job.url_or_path,
                    status="cancelled",
                    error_message="Người dùng đã hủy quy trình",
                ))
                break

            res = self.process(job, job_idx=idx, total_jobs=total)
            results.append(res)
        return results
