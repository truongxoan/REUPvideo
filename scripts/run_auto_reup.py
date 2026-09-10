"""Script dòng lệnh thực hiện quy trình Tự động Reup (Auto Reup Pipeline).

Cách dùng:
    py scripts/run_auto_reup.py                       # Chế độ tương tác menu
    py scripts/run_auto_reup.py --url "https://..."   # Chế độ dòng lệnh
    py scripts/run_auto_reup.py --batch links.txt
    py scripts/run_auto_reup.py --login tiktok
"""
from __future__ import annotations

import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autodub.autoreup import AutoReupEngine, AutoReupJob, scan_videos_in_dir
from autodub.config import Settings
from autodub.media.douyin import extract_url_from_text
from autodub.media.uploader import AutoUploader


def interactive_menu() -> tuple[list[AutoReupJob], bool]:
    """Hiển thị menu tương tác thân thiện trên Terminal nếu không truyền tham số CLI."""
    uploader = AutoUploader()
    print("=" * 60)
    print("   VoxDub Studio - TRÌNH TỰ ĐỘNG REUP TOÀN DIỆN")
    print("=" * 60)
    print("  Quy trình tự động:")
    print("    1. Tải video không logo hoặc nhận tệp từ máy / thư mục con")
    print("    2. Xóa/che phụ đề gốc và Lách bản quyền AI")
    print("    3. Lồng tiếng Việt & Phụ đề mới")
    print("    4. Xuất video và Tự động Đăng bài")
    print("-" * 60)
    print("  [1] Nhập link video hoặc đường dẫn tệp video đơn lẻ")
    print("  [2] Quét thư mục mẹ (tự động lọc tất cả video trong mọi thư mục con)")
    print("  [3] Xử lý danh sách link/tệp từ tệp (links.txt)")
    print("  [4] Đăng nhập lưu tài khoản TikTok (1 lần duy nhất)")
    print("  [5] Đăng nhập lưu tài khoản YouTube (1 lần duy nhất)")
    print("-" * 60)

    try:
        choice = input("  Chọn chức năng (1/2/3/4/5) [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        return [], True

    if choice == "4":
        uploader.login_and_save_session("tiktok")
        return [], True
    elif choice == "5":
        uploader.login_and_save_session("youtube")
        return [], True

    jobs = []
    raw_links: list[str] = []
    if choice == "2":
        folder_path = input("  Nhập đường dẫn thư mục mẹ: ").strip().strip('"\'')
        if not os.path.isdir(folder_path):
            print(f"  [LỖI] Thư mục không tồn tại: {folder_path}")
            return [], True
        raw_links = scan_videos_in_dir(folder_path)
        if not raw_links:
            print(f"  [LỖI] Không tìm thấy tệp video nào trong '{folder_path}' và các thư mục con.")
            return [], True
        print(f"  ✓ Đã tìm thấy {len(raw_links)} tệp video trong thư mục mẹ và các thư mục con!")
    elif choice == "3":
        file_path = input("  Nhập đường dẫn tệp danh sách (mặc định links.txt): ").strip().strip('"\'') or "links.txt"
        if not os.path.exists(file_path):
            print(f"  [LỖI] Không tìm thấy tệp: {file_path}")
            return [], True
        with open(file_path, encoding="utf-8") as f:
            for l in f:
                item = l.strip().strip('"\'')
                if not item or item.startswith("#"):
                    continue
                if os.path.isdir(item):
                    raw_links.extend(scan_videos_in_dir(item))
                else:
                    raw_links.append(item)
    else:
        raw_input = input("  Dán link hoặc đường dẫn tệp/thư mục: ").strip().strip('"\'')
        if not raw_input:
            print("  [LỖI] Chưa nhập dữ liệu.")
            return [], True
        if os.path.isdir(raw_input):
            raw_links = scan_videos_in_dir(raw_input)
            print(f"  ✓ Đã quét tìm thấy {len(raw_links)} video.")
        else:
            raw_links = [raw_input]

    print("\n  Chọn nơi đăng bài sau khi lồng tiếng xong:")
    print("    1. Chỉ xuất video ra máy (Không tự đăng)")
    print("    2. Tự đăng lên TikTok")
    print("    3. Tự đăng lên YouTube Shorts")
    print("    4. Đăng lên cả TikTok và YouTube")
    plat_choice = input("  Chọn (1/2/3/4) [1]: ").strip() or "1"

    plat_map = {"1": "none", "2": "tiktok", "3": "youtube", "4": "all"}
    platform = plat_map.get(plat_choice, "none")

    print("\n  Quy tắc đặt tên video thành phẩm:")
    print("    1. Đổi tên theo số thứ tự (001.mp4, 002.mp4...) [Khuyên dùng cho Reup]")
    print("    2. Giữ nguyên tên gốc của video kèm hậu tố _reup")
    name_choice = input("  Chọn (1/2) [1]: ").strip() or "1"
    naming_mode = "number" if name_choice == "1" else "original"
    number_prefix = ""
    if naming_mode == "number":
        number_prefix = input("  Nhập tiền tố tên video (để trống nếu chỉ muốn số, ví dụ 001.mp4): ").strip()

    for raw in raw_links:
        clean_url = extract_url_from_text(raw) if not os.path.isfile(raw) else None
        jobs.append(AutoReupJob(
            url_or_path=clean_url or raw,
            target_platform=platform,
            output_dir=out_dir_choice,
            enable_dubbing=False,
            auto_cover_subtitles=False,
            zoom=1.04,
            hflip=True,
            canvas_blur=True,
            speed=1.03,
            vignette=False,
            reup_bg_mode="original",
            naming_mode=naming_mode,
            number_prefix=number_prefix,
            number_padding=True,
        ))

    return jobs, False


def main() -> int:
    parser = argparse.ArgumentParser(description="VoxDub Studio - Auto Reup Engine")
    parser.add_argument("--url", help="Link video đơn (Douyin / TikTok / YouTube) hoặc đường dẫn tệp video")
    parser.add_argument("--dir", "--folder", dest="folder", help="Thư mục mẹ chứa video (tự động quét đệ quy mọi thư mục con)")
    parser.add_argument("--files", nargs="+", help="Danh sách một hoặc nhiều tệp video từ máy tính")
    parser.add_argument("--batch", help="Tệp text chứa danh sách link hoặc đường dẫn (mỗi dòng 1 mục)")
    parser.add_argument("--output", "--output-dir", dest="output_dir", default="",
                        help="Thư mục lưu video thành phẩm sau khi xử lý (mặc định: thanh_pham/)")
    parser.add_argument("--platform", default="tiktok", choices=["tiktok", "youtube", "all", "none"],
                        help="Nền tảng đăng bài tự động (mặc định: tiktok)")
    parser.add_argument("--rename-number", action="store_true", default=False,
                        help="Đổi tên video thành phẩm theo số thứ tự (ví dụ: 001.mp4, 002.mp4...)")
    parser.add_argument("--prefix", default="",
                        help="Tiền tố khi đặt tên số thứ tự (ví dụ: Video_ -> Video_001.mp4)")
    parser.add_argument("--no-pad", action="store_true", default=False,
                        help="Tắt đệm số 0 (sẽ đặt tên 1.mp4, 2.mp4... thay vì 001.mp4)")
    parser.add_argument("--caption", default="", help="Tiêu đề / Caption tùy chỉnh")
    parser.add_argument("--tags", default="reup,shorts,reviewphim", help="Danh sách hashtag phân cách bằng dấu phẩy")
    parser.add_argument("--dubbing", action="store_true",
                        help="Bật lồng tiếng AI tiếng Việt (mặc định TẮT để Reup siêu tốc vài giây/video)")
    parser.add_argument("--no-cover-sub", action="store_true", help="Tắt tự động che mờ phụ đề gốc")
    parser.add_argument("--hflip", action="store_true", help="Bật lật gương khung hình (lách bản quyền)")
    parser.add_argument("--zoom", type=float, default=1.04, help="Mức zoom lách bản quyền (mặc định: 1.04)")
    parser.add_argument("--canvas-blur", action="store_true", help="Bật hiệu ứng khung nền mờ PIP (chống quét DNA video)")
    parser.add_argument("--speed", type=float, default=1.03, help="Tốc độ tăng nhẹ video & audio (mặc định: 1.03x)")
    parser.add_argument("--no-vignette", action="store_true", help="Tắt hiệu ứng vignette tối 4 góc")
    parser.add_argument("--bg-music", choices=["replace", "none", "original"], default="original",
                        help="Chế độ nhạc nền: original (giữ gốc), replace (thay bằng kho bgm/), none (tắt âm)")
    parser.add_argument("--login", choices=["tiktok", "youtube"], help="Mở trình duyệt đăng nhập lưu session")

    args, unknown = parser.parse_known_args()

    uploader = AutoUploader()
    if args.login:
        print(f"\n[Login] Mở trình duyệt để đăng nhập {args.login.upper()}...")
        uploader.login_and_save_session(args.login)
        return 0

    jobs = []
    if not args.url and not args.batch and not args.folder and not args.files:
        # Không có tham số -> Chạy giao diện Menu tương tác
        jobs, should_exit = interactive_menu()
        if should_exit or not jobs:
            return 0
    else:
        tags_list = [t.strip() for t in args.tags.split(",") if t.strip()]
        collected_items: list[str] = []

        if args.url:
            collected_items.append(args.url.strip().strip('"\''))

        if args.files:
            for f in args.files:
                collected_items.append(f.strip().strip('"\''))

        if args.folder:
            f_clean = args.folder.strip().strip('"\'')
            if os.path.isdir(f_clean):
                scanned = scan_videos_in_dir(f_clean)
                print(f"  📁 Đã quét thư mục '{f_clean}': tìm thấy {len(scanned)} video.")
                collected_items.extend(scanned)
            else:
                print(f"  [LỖI] Thư mục không tồn tại: {f_clean}")

        if args.batch and os.path.exists(args.batch):
            with open(args.batch, encoding="utf-8") as f:
                for line in f:
                    url = line.strip().strip('"\'')
                    if url and not url.startswith("#"):
                        if os.path.isdir(url):
                            collected_items.extend(scan_videos_in_dir(url))
                        else:
                            collected_items.append(url)

        for item in collected_items:
            clean_url = extract_url_from_text(item) if not os.path.isfile(item) else None
            jobs.append(AutoReupJob(
                url_or_path=clean_url or item,
                target_platform=args.platform,
                output_dir=args.output_dir,
                custom_caption=args.caption,
                tags=tags_list,
                enable_dubbing=args.dubbing,
                hflip=args.hflip,
                zoom=args.zoom,
                canvas_blur=args.canvas_blur,
                speed=args.speed,
                vignette=not args.no_vignette,
                reup_bg_mode=args.bg_music,
                naming_mode="number" if args.rename_number else "original",
                number_prefix=args.prefix,
                number_padding=not args.no_pad,
            ))

    settings = Settings.load()
    engine = AutoReupEngine(settings)

    print(f"\n🚀 Bắt đầu xử lý {len(jobs)} video trong danh sách Reup...\n")
    results = engine.process_batch(jobs)

    print("\n" + "=" * 60)
    print(" BÁO CÁO KẾT QUẢ AUTO REUP")
    print("=" * 60)
    for r in results:
        status_sym = "✅" if r.status == "completed" else "❌"
        print(f"{status_sym} {r.url} ➔ Trạng thái: {r.status}")
        if r.output_video:
            print(f"   📂 Video đã lưu: {r.output_video}")
        if r.uploaded_platforms:
            print(f"   🌐 Đã đăng lên: {', '.join(r.uploaded_platforms)}")
        if r.error_message:
            print(f"   ⚠️ Lỗi: {r.error_message}")
    print("=" * 60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
