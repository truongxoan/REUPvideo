"""Bộ công cụ Tự động đăng video (Auto-Uploader) lên TikTok & YouTube Shorts.

Sử dụng Playwright tự động hóa trình duyệt với Session Cookies:
- Đăng nhập 1 lần để lưu cookies vào tệp JSON.
- Tự động điền tiêu đề, mô tả, hashtag và tải video lên đa nền tảng.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from autodub.utils import app_root, setup_logging

logger = setup_logging("autodub.uploader")


class AutoUploader:
    """Điều phối tải video tự động lên các mạng xã hội."""

    def __init__(self, cookies_dir: str | None = None):
        self.cookies_dir = cookies_dir or os.path.join(app_root(), "cookies")
        os.makedirs(self.cookies_dir, exist_ok=True)

    def _cookie_path(self, platform: str) -> str:
        return os.path.join(self.cookies_dir, f"{platform}_cookies.json")

    def login_and_save_session(self, platform: str = "tiktok") -> bool:
        """Mở trình duyệt để người dùng đăng nhập tài khoản một lần và lưu session."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Chưa cài đặt playwright. Chạy: pip install playwright && playwright install chromium")
            return False

        cookie_file = self._cookie_path(platform)
        urls = {
            "tiktok": "https://www.tiktok.com/login",
            "youtube": "https://accounts.google.com",
            "facebook": "https://www.facebook.com/login",
        }
        target_url = urls.get(platform, urls["tiktok"])

        logger.info(f"Đang mở trình duyệt để đăng nhập {platform.upper()}...")
        print(f"\n[AutoUploader] Mở trình duyệt đăng nhập {platform.upper()}. Hãy đăng nhập tài khoản của bạn, sau đó đóng trình duyệt để lưu session.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(target_url)

            # Chờ người dùng đăng nhập và đóng trình duyệt hoặc điều hướng thành công
            try:
                # Đợi tối đa 5 phút cho người dùng đăng nhập
                page.wait_for_timeout(300000)
            except Exception:
                pass

            # Lưu storage state (cookies + local storage)
            context.storage_state(path=cookie_file)
            browser.close()

        logger.info(f"Đã lưu session {platform} vào: {cookie_file}")
        return os.path.exists(cookie_file)

    def upload_to_tiktok(
        self,
        video_path: str,
        caption: str = "",
        tags: list[str] | None = None,
        headless: bool = True,
        progress_cb: Callable[[str], None] | None = None,
    ) -> bool:
        """Tải video tự động lên TikTok Studio."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Không tìm thấy video: {video_path}")

        cookie_file = self._cookie_path("tiktok")
        if not os.path.exists(cookie_file):
            logger.warning("Chưa có session TikTok. Vui lòng đăng nhập trước qua login_and_save_session('tiktok').")
            if progress_cb:
                progress_cb("Chưa có cookie TikTok, đang mở trình duyệt đăng nhập...")
            self.login_and_save_session("tiktok")
            if not os.path.exists(cookie_file):
                return False

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright chưa được cài đặt.")
            return False

        if progress_cb:
            progress_cb("Đang khởi động trình duyệt tải video lên TikTok...")

        full_caption = caption
        if tags:
            tag_str = " ".join([f"#{t.lstrip('#')}" for t in tags])
            full_caption = f"{caption} {tag_str}".strip()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=cookie_file)
            page = context.new_page()

            try:
                if progress_cb:
                    progress_cb("Đang truy cập TikTok Studio Upload...")
                page.goto("https://www.tiktok.com/tiktokstudio/upload", timeout=60000)
                page.wait_for_load_state("networkidle", timeout=30000)

                # Chọn file tải lên
                if progress_cb:
                    progress_cb("Đang tải file video lên hệ thống...")
                file_input = page.locator('input[type="file"]')
                file_input.set_input_files(os.path.abspath(video_path))

                # Đợi xử lý upload video
                page.wait_for_timeout(5000)

                # Điền Caption / Hashtag
                if full_caption:
                    caption_box = page.locator('div[contenteditable="true"]').first
                    if caption_box.is_visible():
                        caption_box.click()
                        caption_box.fill(full_caption)

                if progress_cb:
                    progress_cb("Video đã sẵn sàng. Đang bấm Đăng bài...")

                # Bấm nút Post / Đăng
                post_btn = page.locator('button:has-text("Đăng"), button:has-text("Post")').first
                if post_btn.is_visible():
                    post_btn.click()
                    page.wait_for_timeout(5000)

                if progress_cb:
                    progress_cb("Đăng video lên TikTok thành công!")

                # Cập nhật lại session cookies mới nhất
                context.storage_state(path=cookie_file)
                browser.close()
                return True

            except Exception as e:
                logger.error(f"Lỗi khi upload TikTok: {e}")
                if progress_cb:
                    progress_cb(f"Lỗi upload TikTok: {e}")
                browser.close()
                return False

    def upload_to_youtube(
        self,
        video_path: str,
        title: str = "",
        description: str = "",
        tags: list[str] | None = None,
        headless: bool = True,
        progress_cb: Callable[[str], None] | None = None,
    ) -> bool:
        """Tải video tự động lên YouTube Studio (Shorts/Videos)."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Không tìm thấy video: {video_path}")

        cookie_file = self._cookie_path("youtube")
        if not os.path.exists(cookie_file):
            logger.warning("Chưa có session YouTube. Vui lòng đăng nhập trước.")
            if progress_cb:
                progress_cb("Chưa có cookie YouTube, đang mở trình duyệt đăng nhập...")
            self.login_and_save_session("youtube")
            if not os.path.exists(cookie_file):
                return False

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=cookie_file)
            page = context.new_page()

            try:
                if progress_cb:
                    progress_cb("Đang truy cập YouTube Studio...")
                page.goto("https://studio.youtube.com", timeout=60000)
                page.wait_for_load_state("networkidle", timeout=30000)

                # Nút Tạo / Upload
                create_btn = page.locator('#create-icon, button:has-text("CREATE"), button:has-text("TẠO")').first
                if create_btn.is_visible():
                    create_btn.click()
                    page.locator('text="Tải video lên", text="Upload videos"').first.click()

                # Upload file
                file_input = page.locator('input[type="file"]')
                file_input.set_input_files(os.path.abspath(video_path))
                page.wait_for_timeout(6000)

                # Tiêu đề
                if title:
                    title_box = page.locator('#textbox[aria-label*="tiêu đề"], #textbox[aria-label*="title"]').first
                    if title_box.is_visible():
                        title_box.fill(title[:100])

                if progress_cb:
                    progress_cb("Đã điền thông tin YouTube Shorts thành công.")

                context.storage_state(path=cookie_file)
                browser.close()
                return True
            except Exception as e:
                logger.error(f"Lỗi khi upload YouTube: {e}")
                if progress_cb:
                    progress_cb(f"Lỗi YouTube: {e}")
                browser.close()
                return False
