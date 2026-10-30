"""原执行者导出前的只读文件查询；禁止用自报不存在或SHA代替实际查询。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

parser = argparse.ArgumentParser()
parser.add_argument('--path', required=True)
parser.add_argument('--expected-script-sha256', required=True)
args = parser.parse_args()
script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
if script_sha != args.expected_script_sha256:
    raise SystemExit('IMMUTABLE_PROBE_SCRIPT_SHA_MISMATCH')
if not os.path.isabs(args.path):
    raise SystemExit('ABSOLUTE_PDF_PATH_REQUIRED')
path = os.path.realpath(args.path)
result = {'schema': 'spatial42-pdf-pre-export-filesystem-probe-v1',
          'at_utc': datetime.now(timezone.utc).isoformat(), 'read_only': True,
          'helper_sha256': script_sha, 'path': path, 'status': 'READY'}

def identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
except FileNotFoundError:
    result.update(existed=False, file=None, absence='FileNotFoundError')
else:
    try:
        before = os.fstat(fd)
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise RuntimeError('PDF_CHANGED_DURING_PRE_EXPORT_READ')
        result.update(existed=True, file={'sha256': digest.hexdigest(), 'identity': identity(after)})
    finally:
        os.close(fd)
print(json.dumps(result, ensure_ascii=False))
