import hashlib
import json
import os
import uuid
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def atomic_write(path, content):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        tmp.write_text(content,encoding='utf-8'); os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)
