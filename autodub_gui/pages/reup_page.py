"""Trang Tự động Reup: Tải không logo -> Xóa sub -> Lồng tiếng -> Lách bản quyền -> Tự đăng bài."""
from __future__ import annotations

import os
import threading
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QRadioButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from autodub.autoreup import AutoReupEngine, AutoReupJob, scan_videos_in_dir, VIDEO_EXTS
from autodub.config import Settings
from autodub.media.douyin import extract_url_from_text
from autodub.media.uploader import AutoUploader
from autodub.utils import app_root
from autodub_gui import icons, tokens
from autodub_gui.pages import BasePage
from autodub_gui.system_open import open_folder
from autodub_gui.ui.buttons import GhostButton, PrimaryButton
from autodub_gui.ui.cards import Card
from autodub_gui.ui.dropzone import VIDEO_FILTER
from autodub_gui.widgets import LogPanel

_PAGE_MARGIN = 28


class _ReupDropPlainTextEdit(QPlainTextEdit):
    """Hộp nhập văn bản hỗ trợ kéo thả tệp video và thư mục mẹ đệ quy."""

    paths_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            raw_paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if raw_paths:
                event.acceptProposedAction()
                self.paths_dropped.emit(raw_paths)
                return
        super().dropEvent(event)


class _ReupWorker(QThread):
    progress_signal = Signal(str, float)
    finished_signal = Signal(list)
    error_signal = Signal(str)

    def __init__(self, jobs: list[AutoReupJob], settings: Settings, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.settings = settings
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        def _cb(msg: str, pct: float):
            self.progress_signal.emit(msg, pct)

        engine = AutoReupEngine(self.settings, progress_cb=_cb, cancel_event=self.cancel_event)
        try:
            results = engine.process_batch(self.jobs)
            self.finished_signal.emit(results)
        except Exception as e:
            self.error_signal.emit(str(e))


class ReupPage(BasePage):
    """Trang điều khiển Quy trình Tự động Reup Toàn diện."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._worker: _ReupWorker | None = None
        self._settings = Settings.load()
        self._uploader = AutoUploader()
        self._build()

    def _build(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        root = QVBoxLayout(container)
        root.setContentsMargins(_PAGE_MARGIN, tokens.SP_2, _PAGE_MARGIN, tokens.SP_5)
        root.setSpacing(tokens.SP_4)

        # 1. Card nhập liệu
        root.addWidget(self._build_input_card())

        # 2. Card tùy chọn lách bản quyền & đăng tải
        root.addWidget(self._build_options_card())

        # 3. Tiến độ & Log
        root.addWidget(self._build_progress_card())

        root.addStretch()
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def _build_input_card(self) -> QWidget:
        card = Card(padding=tokens.SP_4)
        card.add_header("1. Nguồn Video cần Reup (Tệp video / Thư mục mẹ / Liên kết)")

        desc = QLabel(
            "Chọn thư mục mẹ (tự động quét đệ quy các thư mục con để lọc video), "
            "chọn tệp video từ máy, hoặc dán liên kết video (Douyin, TikTok, YouTube):"
        )
        desc.setStyleSheet(f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.FS_BODY}px;")
        desc.setWordWrap(True)
        card.body.addWidget(desc)

        self.input_edit = _ReupDropPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "• Bấm 'Chọn thư mục mẹ (quét đệ quy)' để tự động lọc tất cả video trong mọi thư mục con\n"
            "• Bấm 'Chọn tệp video' để chọn các video từ máy tính\n"
            "• Kéo thả trực tiếp tệp hoặc thư mục mẹ vào khung này\n"
            "• Dán liên kết video (Douyin, TikTok, YouTube, mỗi dòng một link)"
        )
        self.input_edit.setMinimumHeight(110)
        self.input_edit.setMaximumHeight(170)
        self.input_edit.setStyleSheet(
            f"background: {tokens.BG_INPUT}; color: {tokens.TEXT_PRIMARY}; "
            f"border: 1px solid {tokens.BORDER_SUBTLE}; border-radius: 6px; padding: 10px; font-size: 13px;"
        )
        self.input_edit.paths_dropped.connect(self._on_paths_dropped)
        self.input_edit.textChanged.connect(self._update_count_label)
        card.body.addWidget(self.input_edit)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(tokens.SP_2)

        btn_pick_folder = GhostButton("📁 Chọn thư mục mẹ (quét đệ quy)...")
        btn_pick_folder.clicked.connect(self._on_pick_folder)
        btn_pick_folder.setToolTip("Quét đệ quy thư mục mẹ và tất cả các thư mục con để tự động lọc mọi video")

        btn_pick_files = GhostButton("🎬 Chọn tệp video...")
        btn_pick_files.clicked.connect(self._on_pick_files)
        btn_pick_files.setToolTip("Chọn một hoặc nhiều tệp video từ máy tính")

        btn_paste = GhostButton("📋 Dán")
        btn_paste.clicked.connect(self._paste_clipboard)

        btn_clear = GhostButton("🗑️ Xóa hết")
        btn_clear.clicked.connect(self.input_edit.clear)

        self.lbl_count = QLabel("Chưa có video nào.")
        self.lbl_count.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 12px;")

        btn_row.addWidget(btn_pick_folder)
        btn_row.addWidget(btn_pick_files)
        btn_row.addWidget(btn_paste)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        btn_row.addWidget(self.lbl_count)
        card.body.addLayout(btn_row)

        return card

    def _on_pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục mẹ chứa video (hệ thống sẽ quét đệ quy mọi thư mục con)",
            os.path.expanduser("~")
        )
        if not folder:
            return
        videos = scan_videos_in_dir(folder)
        if not videos:
            self.status_label.setText(f"⚠️ Không tìm thấy video hợp lệ trong thư mục: {os.path.basename(folder)}")
            self._log(f"Không tìm thấy video hợp lệ (.mp4, .mkv, .mov, ...) trong thư mục '{folder}' và các thư mục con.", is_error=True)
            return
        added = self._append_paths(videos)
        self.status_label.setText(f"📁 Đã quét thư mục '{os.path.basename(folder)}': tìm thấy {len(videos)} video.")
        self._log(f"Đã quét đệ quy thư mục mẹ '{folder}': lọc được {len(videos)} video (thêm mới {added} video vào danh sách).", is_success=True)

    def _on_pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Chọn một hoặc nhiều tệp video",
            os.path.expanduser("~"), VIDEO_FILTER
        )
        if not files:
            return
        added = self._append_paths(files)
        self.status_label.setText(f"🎬 Đã nạp {len(files)} tệp video.")
        self._log(f"Đã chọn {len(files)} tệp video (thêm mới {added} video vào danh sách).", is_success=True)

    def _on_paths_dropped(self, paths: list[str]) -> None:
        all_videos = []
        for p in paths:
            if os.path.isdir(p):
                scanned = scan_videos_in_dir(p)
                all_videos.extend(scanned)
            elif os.path.isfile(p) and p.lower().endswith(VIDEO_EXTS):
                all_videos.append(p)
        if all_videos:
            added = self._append_paths(all_videos)
            self.status_label.setText(f"📥 Đã nhận {len(all_videos)} video kéo thả.")
            self._log(f"Kéo thả: đã lọc được {len(all_videos)} video từ các tệp/thư mục vừa thả (thêm mới {added}).", is_success=True)

    def _append_paths(self, paths: list[str]) -> int:
        cur = self.input_edit.toPlainText().strip()
        existing_lines = [l.strip() for l in cur.splitlines() if l.strip()]
        added = 0
        for p in paths:
            norm_p = os.path.normpath(p)
            if norm_p not in existing_lines and p not in existing_lines:
                existing_lines.append(norm_p)
                added += 1
        if added > 0:
            self.input_edit.setPlainText("\n".join(existing_lines))
        self._update_count_label()
        return added

    def _update_count_label(self) -> None:
        text = self.input_edit.toPlainText().strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        count = len(lines)
        if count == 0:
            self.lbl_count.setText("Chưa có video nào.")
            self.lbl_count.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 12px;")
        else:
            self.lbl_count.setText(f"✓ Đã sẵn sàng {count} video / liên kết")
            self.lbl_count.setStyleSheet(f"color: {tokens.PRIMARY}; font-weight: 600; font-size: 12px;")

    def _build_options_card(self) -> QWidget:
        card = Card(padding=tokens.SP_4)
        card.add_header("2. Tùy chọn Xử lý & Nền tảng Đăng bài")

        # Nền tảng đăng bài
        plat_label = QLabel("Nơi xuất bản sau khi lồng tiếng:")
        plat_label.setStyleSheet(f"font-weight: 600; color: {tokens.TEXT_PRIMARY}; font-size: {tokens.FS_BODY}px;")
        card.body.addWidget(plat_label)

        plat_layout = QHBoxLayout()
        self.bg_plat = QButtonGroup(self)
        self.rb_none = QRadioButton("📁 Chỉ xuất video ra máy (không tự đăng)")
        self.rb_tiktok = QRadioButton("🎵 Tự đăng lên TikTok Studio")
        self.rb_youtube = QRadioButton("▶️ Tự đăng lên YouTube Shorts")
        self.rb_all = QRadioButton("🌐 Đăng lên cả TikTok và YouTube")

        self.rb_none.setChecked(True)
        self.bg_plat.addButton(self.rb_none, 1)
        self.bg_plat.addButton(self.rb_tiktok, 2)
        self.bg_plat.addButton(self.rb_youtube, 3)
        self.bg_plat.addButton(self.rb_all, 4)

        for rb in (self.rb_none, self.rb_tiktok, self.rb_youtube, self.rb_all):
            rb.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-size: 13px; margin-right: 12px;")
            plat_layout.addWidget(rb)
        plat_layout.addStretch()
        card.body.addLayout(plat_layout)

        # Thư mục đích xuất video thành phẩm
        out_label = QLabel("Thư mục lưu video thành phẩm sau khi xử lý:")
        out_label.setStyleSheet(f"font-weight: 600; color: {tokens.TEXT_PRIMARY}; font-size: {tokens.FS_BODY}px; margin-top: 6px;")
        card.body.addWidget(out_label)

        out_row = QHBoxLayout()
        out_row.setSpacing(tokens.SP_2)
        self.txt_output_dir = QLineEdit()
        self.txt_output_dir.setText(os.path.join(app_root(), "thanh_pham"))
        self.txt_output_dir.setPlaceholderText("Đường dẫn thư mục lưu video thành phẩm...")
        self.txt_output_dir.setStyleSheet(
            f"background: {tokens.BG_INPUT}; color: {tokens.TEXT_PRIMARY}; "
            f"border: 1px solid {tokens.BORDER_SUBTLE}; border-radius: 6px; padding: 6px 10px; font-size: 13px;"
        )
        btn_browse_out = GhostButton("📁 Chọn thư mục lưu...")
        btn_browse_out.clicked.connect(self._on_browse_output_dir)
        btn_browse_out.setToolTip("Chọn thư mục trên máy tính để lưu tất cả video sau khi reup")
        out_row.addWidget(self.txt_output_dir, 1)
        out_row.addWidget(btn_browse_out)
        card.body.addLayout(out_row)

        # Quy tắc đặt tên video thành phẩm
        name_label = QLabel("Quy tắc đặt tên video thành phẩm:")
        name_label.setStyleSheet(f"font-weight: 600; color: {tokens.TEXT_PRIMARY}; font-size: {tokens.FS_BODY}px; margin-top: 6px;")
        card.body.addWidget(name_label)

        name_row = QHBoxLayout()
        name_row.setSpacing(tokens.SP_2)

        self.cb_rename_number = QCheckBox("Đổi tên video thành số thứ tự (ví dụ: 001.mp4, 002.mp4...)")
        self.cb_rename_number.setChecked(True)
        self.cb_rename_number.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-weight: 600; font-size: 13px;")
        name_row.addWidget(self.cb_rename_number)

        self.lbl_prefix = QLabel("Tiền tố:")
        self.lbl_prefix.setStyleSheet(f"color: {tokens.TEXT_SECONDARY}; font-size: 12px; margin-left: 8px;")
        self.txt_prefix = QLineEdit()
        self.txt_prefix.setPlaceholderText("Để trống (chỉ số) hoặc ví dụ: Video_, Tap_")
        self.txt_prefix.setMaximumWidth(220)
        self.txt_prefix.setStyleSheet(
            f"background: {tokens.BG_INPUT}; color: {tokens.TEXT_PRIMARY}; "
            f"border: 1px solid {tokens.BORDER_SUBTLE}; border-radius: 4px; padding: 4px 8px; font-size: 12px;"
        )

        self.cb_number_pad = QCheckBox("Đệm số 0 theo tổng số (001, 002...)")
        self.cb_number_pad.setChecked(True)
        self.cb_number_pad.setStyleSheet(f"color: {tokens.TEXT_SECONDARY}; font-size: 12px; margin-left: 6px;")

        name_row.addWidget(self.lbl_prefix)
        name_row.addWidget(self.txt_prefix)
        name_row.addWidget(self.cb_number_pad)
        name_row.addStretch()
        card.body.addLayout(name_row)

        self.cb_rename_number.toggled.connect(lambda chk: (
            self.lbl_prefix.setEnabled(chk),
            self.txt_prefix.setEnabled(chk),
            self.cb_number_pad.setEnabled(chk),
        ))

        # Đường kẻ ngăn cách
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"background: {tokens.BORDER_SUBTLE}; border: none; height: 1px; margin: 8px 0;")
        card.body.addWidget(line)

        # Xóa sub & Lách bản quyền
        fx_header_row = QHBoxLayout()
        fx_label = QLabel("Bộ lọc Xóa phụ đề cũ & Lách bản quyền AI đa tầng:")
        fx_label.setStyleSheet(f"font-weight: 600; color: {tokens.TEXT_PRIMARY}; font-size: {tokens.FS_BODY}px;")
        fx_header_row.addWidget(fx_label)
        fx_header_row.addStretch()

        btn_preset_fast = GhostButton("🚀 1-Click: Reup Siêu Tốc (Chỉ lách hình, ghép nhạc sau)")
        btn_preset_fast.setToolTip("Cấu hình nhanh: Zoom + Lật gương + Nền mờ PIP + Tăng tốc 1.03x. Giữ nguyên âm thanh, không lồng tiếng AI.")
        btn_preset_fast.clicked.connect(self._preset_fast_reup)

        btn_preset_max = GhostButton("⚡ Bật Toàn Bộ")
        btn_preset_max.setToolTip("Bật đầy đủ tất cả 6 lớp bảo vệ: Xóa sub + Zoom 4% + Lật gương + Nền mờ PIP + Tăng tốc 1.03x + Thay BGM")
        btn_preset_max.clicked.connect(self._preset_max_anti_copyright)

        btn_preset_std = GhostButton("🛡️ Chuẩn (Không lật gương)")
        btn_preset_std.setToolTip("Giữ nguyên hướng video cho các video có chữ quan trọng")
        btn_preset_std.clicked.connect(self._preset_std_anti_copyright)

        fx_header_row.addWidget(btn_preset_fast)
        fx_header_row.addWidget(btn_preset_max)
        fx_header_row.addWidget(btn_preset_std)
        card.body.addLayout(fx_header_row)

        opt_grid = QGridLayout()
        opt_grid.setSpacing(tokens.SP_2)

        self.cb_cover_sub = QCheckBox("Tự động xóa/che mờ dải phụ đề gốc ở chân video (Boxblur)")
        self.cb_cover_sub.setChecked(False)
        self.cb_cover_sub.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-size: 13px;")

        self.cb_zoom = QCheckBox("Lách bản quyền: Zoom nhẹ 4% + Chỉnh màu Jitter + Reset MD5 Hash")
        self.cb_zoom.setChecked(True)
        self.cb_zoom.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-size: 13px;")

        self.cb_hflip = QCheckBox("Lách bản quyền: Lật gương khung hình (Horizontal Flip)")
        self.cb_hflip.setChecked(True)
        self.cb_hflip.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-size: 13px;")

        self.cb_canvas_blur = QCheckBox("Khung nền mờ PIP (Canvas Blur - Đổi tỷ lệ chống quét DNA)")
        self.cb_canvas_blur.setChecked(True)
        self.cb_canvas_blur.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-size: 13px;")

        self.cb_speed = QCheckBox("Tăng tốc độ video & audio 1.03x (Lệch timeline quét Content ID)")
        self.cb_speed.setChecked(True)
        self.cb_speed.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-size: 13px;")

        self.cb_bgm_replace = QCheckBox("Thay thế nhạc nền gốc bằng kho nhạc không bản quyền (bgm/)")
        self.cb_bgm_replace.setChecked(False)
        self.cb_bgm_replace.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-size: 13px;")

        self.cb_ai_dubbing = QCheckBox("Lồng tiếng AI tiếng Việt (Tách nhạc Demucs + Dịch + Giọng đọc AI)")
        self.cb_ai_dubbing.setChecked(False)
        self.cb_ai_dubbing.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-size: 13px;")
        self.cb_ai_dubbing.setToolTip("Mặc định TẮT: Reup siêu tốc chỉ trong vài giây/video. BẬT: Lồng tiếng AI tiếng Việt đầy đủ.")

        opt_grid.addWidget(self.cb_cover_sub, 0, 0)
        opt_grid.addWidget(self.cb_zoom, 0, 1)
        opt_grid.addWidget(self.cb_hflip, 1, 0)
        opt_grid.addWidget(self.cb_canvas_blur, 1, 1)
        opt_grid.addWidget(self.cb_speed, 2, 0)
        opt_grid.addWidget(self.cb_bgm_replace, 2, 1)
        opt_grid.addWidget(self.cb_ai_dubbing, 3, 0, 1, 2)
        card.body.addLayout(opt_grid)

        # Nút đăng nhập tài khoản
        auth_row = QHBoxLayout()
        auth_row.setSpacing(tokens.SP_3)
        auth_txt = QLabel("Đăng nhập tài khoản mạng xã hội (chỉ cần 1 lần):")
        auth_txt.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: 12px;")
        auth_row.addWidget(auth_txt)

        btn_login_tt = GhostButton("Đăng nhập TikTok")
        btn_login_tt.clicked.connect(lambda: self._uploader.login_and_save_session("tiktok"))
        auth_row.addWidget(btn_login_tt)

        btn_login_yt = GhostButton("Đăng nhập YouTube")
        btn_login_yt.clicked.connect(lambda: self._uploader.login_and_save_session("youtube"))
        auth_row.addWidget(btn_login_yt)
        auth_row.addStretch()

        card.body.addLayout(auth_row)
        return card

    def _build_progress_card(self) -> QWidget:
        card = Card(padding=tokens.SP_4)
        card.add_header("3. Thực thi & Nhật ký Tiến trình")

        btn_row = QHBoxLayout()
        btn_row.setSpacing(tokens.SP_3)
        self.btn_run = PrimaryButton("⚡ 1-CLICK BẮT ĐẦU AUTO REUP")
        self.btn_run.setFixedHeight(40)
        self.btn_run.setMinimumWidth(260)
        self.btn_run.setToolTip("Tự động thực hiện trọn gói toàn bộ quy trình: Quét video -> Xóa sub -> Lồng tiếng AI -> Lách bản quyền đa tầng -> Xuất video!")
        self.btn_run.setStyleSheet(
            f"background: {tokens.PRIMARY}; color: white; font-weight: bold; "
            f"font-size: 14px; border-radius: 6px; padding: 0 20px;"
        )
        self.btn_run.clicked.connect(self._on_start_reup)
        btn_row.addWidget(self.btn_run)

        self.btn_cancel = GhostButton("⏹️ Dừng lại")
        self.btn_cancel.setFixedHeight(40)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._on_cancel_reup)
        btn_row.addWidget(self.btn_cancel)

        self.btn_open = GhostButton("Mở thư mục Video thành phẩm")
        self.btn_open.setFixedHeight(40)
        self.btn_open.clicked.connect(self._on_open_output_dir)
        btn_row.addWidget(self.btn_open)
        btn_row.addStretch()
        card.body.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(22)
        self.progress_bar.setStyleSheet(
            f"QProgressBar {{ background: {tokens.BG_INPUT}; border: 1px solid {tokens.BORDER_SUBTLE}; border-radius: 4px; text-align: center; color: {tokens.TEXT_PRIMARY}; font-weight: bold; }} "
            f"QProgressBar::chunk {{ background: {tokens.PRIMARY}; border-radius: 3px; }}"
        )
        card.body.addWidget(self.progress_bar)

        self.status_label = QLabel("Trạng thái: Sẵn sàng")
        self.status_label.setStyleSheet(f"color: {tokens.TEXT_SECONDARY}; font-size: 13px; font-weight: 500;")
        card.body.addWidget(self.status_label)

        self.log_panel = LogPanel()
        self.log_panel.setMinimumHeight(160)
        self.log_panel.setMaximumHeight(260)
        card.body.addWidget(self.log_panel)

        return card

    def _preset_fast_reup(self) -> None:
        self.cb_cover_sub.setChecked(False)
        self.cb_zoom.setChecked(True)
        self.cb_hflip.setChecked(True)
        self.cb_canvas_blur.setChecked(True)
        self.cb_speed.setChecked(True)
        self.cb_bgm_replace.setChecked(False)
        self.cb_ai_dubbing.setChecked(False)
        self._log("🚀 Đã kích hoạt cấu hình Reup Siêu Tốc (Lật hình + Zoom + Nền mờ PIP + Tăng tốc 1.03x, giữ nguyên âm thanh để ghép nhạc sau).", is_success=True)

    def _preset_max_anti_copyright(self) -> None:
        self.cb_cover_sub.setChecked(True)
        self.cb_zoom.setChecked(True)
        self.cb_hflip.setChecked(True)
        self.cb_canvas_blur.setChecked(True)
        self.cb_speed.setChecked(True)
        self.cb_bgm_replace.setChecked(True)
        self.cb_ai_dubbing.setChecked(False)
        self._log("⚡ Đã kích hoạt cấu hình Lách Bản Quyền Toàn Diện (Bật đầy đủ 6 lớp bảo vệ hình ảnh & âm thanh).", is_success=True)

    def _preset_std_anti_copyright(self) -> None:
        self.cb_cover_sub.setChecked(False)
        self.cb_zoom.setChecked(True)
        self.cb_hflip.setChecked(False)
        self.cb_canvas_blur.setChecked(False)
        self.cb_speed.setChecked(True)
        self.cb_bgm_replace.setChecked(False)
        self.cb_ai_dubbing.setChecked(False)
        self._log("🛡️ Đã chuyển sang cấu hình lách bản quyền Tiêu chuẩn (không lật gương).")

    def _on_browse_output_dir(self) -> None:
        initial = self.txt_output_dir.text().strip() or os.path.join(app_root(), "thanh_pham")
        folder = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục lưu video thành phẩm sau khi Reup",
            initial if os.path.isdir(initial) else app_root()
        )
        if folder:
            self.txt_output_dir.setText(os.path.normpath(folder))
            self._log(f"Đã đổi thư mục đích xuất video sang: {folder}")

    def _on_open_output_dir(self) -> None:
        out_dir = self.txt_output_dir.text().strip() or os.path.join(app_root(), "thanh_pham")
        os.makedirs(out_dir, exist_ok=True)
        open_folder(out_dir)

    def _paste_clipboard(self) -> None:
        from PySide6.QtWidgets import QApplication
        clip = QApplication.clipboard().text()
        if clip:
            cur = self.input_edit.toPlainText().strip()
            self.input_edit.setPlainText(f"{cur}\n{clip}".strip() if cur else clip)

    def _log(self, msg: str, is_error: bool = False, is_success: bool = False) -> None:
        import logging
        from autodub_gui.log_text import SUCCESS
        lvl = logging.ERROR if is_error else (SUCCESS if is_success else logging.INFO)
        self.log_panel.append_log(msg, lvl)

    def _on_start_reup(self) -> None:
        text = self.input_edit.toPlainText().strip()
        if not text:
            self.status_label.setText("⚠️ Chưa có video hoặc link nào để xử lý.")
            return

        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            return

        # Tự động mở rộng nếu người dùng dán đường dẫn thư mục trực tiếp vào ô nhập
        expanded_items: list[str] = []
        for l in lines:
            cleaned = l.strip('"\'')
            if os.path.isdir(cleaned):
                scanned = scan_videos_in_dir(cleaned)
                if scanned:
                    expanded_items.extend(scanned)
                    self._log(f"Đã mở rộng thư mục '{cleaned}': tìm thấy {len(scanned)} video.")
                else:
                    self._log(f"Thư mục '{cleaned}' không chứa video hợp lệ.", is_error=True)
            else:
                expanded_items.append(cleaned)

        if not expanded_items:
            self.status_label.setText("⚠️ Không tìm thấy video hợp lệ để xử lý.")
            return

        plat_id = self.bg_plat.checkedId()
        plat_map = {1: "none", 2: "tiktok", 3: "youtube", 4: "all"}
        target_platform = plat_map.get(plat_id, "none")
        target_out_dir = self.txt_output_dir.text().strip() or os.path.join(app_root(), "thanh_pham")

        rename_seq = self.cb_rename_number.isChecked()
        prefix = self.txt_prefix.text().strip()
        padding = self.cb_number_pad.isChecked()

        jobs = []
        for item in expanded_items:
            norm_item = os.path.abspath(item) if os.path.isfile(item) else item
            clean_url = extract_url_from_text(norm_item) if not os.path.isfile(norm_item) else None
            jobs.append(AutoReupJob(
                url_or_path=clean_url or norm_item,
                target_platform=target_platform,
                output_dir=target_out_dir,
                enable_dubbing=self.cb_ai_dubbing.isChecked(),
                auto_cover_subtitles=self.cb_cover_sub.isChecked(),
                zoom=1.04 if self.cb_zoom.isChecked() else 1.0,
                hflip=self.cb_hflip.isChecked(),
                canvas_blur=self.cb_canvas_blur.isChecked(),
                speed=1.03 if self.cb_speed.isChecked() else 1.0,
                vignette=False,
                reup_bg_mode="replace" if self.cb_bgm_replace.isChecked() else "original",
                naming_mode="number" if rename_seq else "original",
                number_prefix=prefix if rename_seq else "",
                number_padding=padding,
            ))

        self.btn_run.setEnabled(False)
        self.btn_cancel.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(2)
        self.status_label.setText("Đang bắt đầu quy trình tự động...")
        self.log_panel.clear()
        mode_desc = "KÈM LỒNG TIẾNG AI" if self.cb_ai_dubbing.isChecked() else "REUP SIÊU TỐC (giữ âm thanh gốc, ghép nhạc sau)"
        self._log(f"Khởi động xử lý hàng loạt {len(jobs)} video — Chế độ: {mode_desc}...", is_success=True)

        self._worker = _ReupWorker(jobs, self._settings, self)
        self._worker.progress_signal.connect(self._on_progress)
        self._worker.finished_signal.connect(self._on_finished)
        self._worker.error_signal.connect(self._on_error)
        self._worker.start()

    def _on_cancel_reup(self) -> None:
        if self._worker:
            self.btn_cancel.setEnabled(False)
            self.status_label.setText("Đang dừng tiến trình...")
            self._log("⚠️ Đã gửi yêu cầu dừng tiến trình...", is_error=True)
            self._worker.cancel()

    def _on_progress(self, msg: str, pct: float) -> None:
        self.status_label.setText(f"Trạng thái: {msg}")
        self.progress_bar.setValue(int(pct))
        self._log(f"[{pct:.0f}%] {msg}")

    def _on_finished(self, results: list) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.progress_bar.setValue(100)
        has_cancel = any(getattr(r, "status", "") == "cancelled" for r in results)
        if has_cancel:
            self.status_label.setText("⏹️ Đã dừng quy trình Auto Reup!")
            self._log("=" * 50)
            self._log("ĐÃ DỪNG QUY TRÌNH AUTO REUP THEO YÊU CẦU.", is_error=True)
        else:
            self.status_label.setText("✅ Đã hoàn thành toàn bộ quy trình Auto Reup!")
            self._log("=" * 50, is_success=True)
            self._log("HOÀN THÀNH TOÀN BỘ QUY TRÌNH AUTO REUP:", is_success=True)
        for r in results:
            self._log(f"• Video: {r.output_video or r.url} - Trạng thái: {r.status}", is_success=(r.status == "completed"), is_error=(r.status != "completed"))
            if r.uploaded_platforms:
                self._log(f"  Đã đăng lên: {', '.join(r.uploaded_platforms)}", is_success=True)
            if r.error_message:
                self._log(f"  Ghi chú/Lỗi: {r.error_message}", is_error=(r.status != "completed"))

    def _on_error(self, err: str) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.status_label.setText(f"❌ Lỗi: {err}")
        self._log(f"[LỖI] {err}", is_error=True)
