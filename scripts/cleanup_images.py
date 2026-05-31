#!/usr/bin/env python3
"""Clean up report images older than 7 days. Runs weekly."""  
import os
import sys
import time

IMG_DIR = os.path.expanduser('/home/ubuntu/.hermes/cron/images')
CUTOFF = time.time() - (7 * 86400)

if not os.path.exists(IMG_DIR):
    print("[SILENT]")
    exit(0)

deleted = []
for f in os.listdir(IMG_DIR):
    if not f.endswith(('.jpg', '.png', '.webp')):
        continue
    path = os.path.join(IMG_DIR, f)
    try:
        mtime = os.path.getmtime(path)
        if mtime < CUTOFF:
            age_days = int((time.time() - mtime) / 86400)
            os.remove(path)
            deleted.append((f, age_days))
    except OSError as e:
        print(f"⚠️ Không xoá được {f}: {e}", file=sys.stderr)

if not deleted:
    print("[SILENT]")
else:
    print(f"🧹 **Dọn dẹp ảnh bản tin** — {len(deleted)} ảnh cũ hơn 7 ngày")
    for f, age in sorted(deleted, key=lambda x: -x[1]):
        print(f"  • {f} ({age} ngày tuổi) — đã xoá")
