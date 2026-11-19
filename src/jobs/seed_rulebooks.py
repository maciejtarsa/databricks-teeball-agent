"""Copy rulebook PDFs synced with the bundle into the kb_docs volume (idempotent)."""
import filecmp
import os
import shutil
import sys

src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=True)

for name in sorted(os.listdir(src)):
    if name.lower().endswith(".pdf"):
        src_file, dst_file = os.path.join(src, name), os.path.join(dst, name)
        # Skip unchanged files so their modification time (and parse cache) is kept
        if os.path.exists(dst_file) and filecmp.cmp(src_file, dst_file, shallow=False):
            print(f"unchanged {name}")
            continue
        shutil.copyfile(src_file, dst_file)
        print(f"copied {name} -> {dst}")
