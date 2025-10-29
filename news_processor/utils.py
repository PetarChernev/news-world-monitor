
from datetime import datetime, timezone
import hashlib
import re
import time
from dateutil import parser as dtparser


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def now_ms() -> int:
    return int(time.time() * 1000)

def parse_timestamp(value: str) -> datetime:
    dt = dtparser.parse(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt

def country_alpha(value: str) -> str:
    return (value or "").upper()



