
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ArticleRaw:
    url: str
    title: str
    seendate: str
    url_mobile: str = ""
    socialimage: str = ""
    domain: str = ""
    language: str = ""
    sourcecountry: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ArticleRaw":
        # Be tolerant of missing keys; required ones will raise a KeyError if absent
        return cls(
            url=d["url"],
            title=d["title"],
            seendate=d["seendate"],
            url_mobile=d.get("url_mobile", "") or "",
            socialimage=d.get("socialimage", "") or "",
            domain=d.get("domain", "") or "",
            language=d.get("language", "") or "",
            sourcecountry=d.get("sourcecountry", "") or "",
        )



@dataclass
class EntitySpan:
    text: str
    label: str  # NER label (ORG, PERSON, GPE, etc.)
    start_char: int
    end_char: int
    token_start: int
    token_end: int  # exclusive
    head_token_i: int