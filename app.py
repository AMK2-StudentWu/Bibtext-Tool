import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import arxiv
import requests
import streamlit as st
from rapidfuzz import fuzz

try:
    # Optional, used only when "实时模式" 开启
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None


# ==========================
# Regex & basic helpers
# ==========================

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
ARXIV_NEW_RE = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", re.IGNORECASE)
ARXIV_OLD_RE = re.compile(r"\b[a-z\-]+/\d{7}(?:v\d+)?\b", re.IGNORECASE)


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def latex_escape(s: str) -> str:
    """Minimal escaping for BibTeX."""
    if s is None:
        return ""
    return (
        s.replace("\\", "\\\\")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("_", r"\_")
        .replace("#", r"\#")
        .replace("$", r"\$")
    )


def extract_doi(text: str) -> Optional[str]:
    m = DOI_RE.search(text or "")
    if not m:
        return None
    return m.group(0).rstrip(".")


def extract_arxiv_id(text: str) -> Optional[str]:
    if not text:
        return None
    # arXiv:xxxx.xxxxx
    m = re.search(r"arxiv\s*:\s*([^\s]+)", text, re.IGNORECASE)
    if m:
        cand = m.group(1)
        cand = cand.replace("abs/", "").replace("pdf/", "")
        cand = cand.replace(".pdf", "")
        cand = cand.strip().rstrip(".")
        if ARXIV_NEW_RE.fullmatch(cand) or ARXIV_OLD_RE.fullmatch(cand):
            return cand

    # URL forms
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+)", text, re.IGNORECASE)
    if m:
        cand = m.group(1)
        cand = cand.replace(".pdf", "")
        cand = cand.strip().rstrip(".")
        if ARXIV_NEW_RE.fullmatch(cand) or ARXIV_OLD_RE.fullmatch(cand):
            return cand

    # raw IDs
    m = ARXIV_NEW_RE.search(text)
    if m:
        return m.group(0)
    m = ARXIV_OLD_RE.search(text)
    if m:
        return m.group(0)
    return None


def guess_title(text: str) -> str:
    """Try to extract a title from messy citation strings."""
    if not text:
        return ""

    t = text.strip()
    # remove leading numbering like [1] or 1.
    t = re.sub(r"^\s*(\[\d+\]|\d+\.)\s*", "", t)
    # remove trailing arXiv / doi url noise
    t = re.sub(r"\barxiv\b.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"https?://\S+", "", t).strip()

    # quoted title first
    qm = re.search(r"[\"\“\”\'\‘\’](.+?)[\"\“\”\'\‘\’]", t)
    if qm and len(qm.group(1)) >= 6:
        return qm.group(1).strip().rstrip(".")

    # common pattern: author list. Title. Venue...
    # if there's a period, take the longest sentence-like chunk
    parts = [p.strip() for p in re.split(r"[\.;]", t) if p.strip()]
    if parts:
        cand = max(parts, key=len)
        # if overly long (authors), fall back to comma heuristic
        if len(cand) > 15:
            return cand.strip().rstrip(".")

    # comma heuristic: often title is the longest segment
    if "," in t:
        chunks = [c.strip() for c in t.split(",") if c.strip()]
        if chunks:
            cand = max(chunks, key=len)
            return cand.strip().rstrip(".")

    return t.strip().rstrip(".")


def make_key(authors: List[str], year: Optional[int], title: str) -> str:
    last = "paper"
    if authors:
        last = (authors[0].split()[-1] or "paper").lower()
        last = re.sub(r"[^a-z0-9]", "", last)
    y = str(year) if year else "noyear"
    first = (_norm(title).split()[:1] or ["work"])[0]
    return f"{last}{y}{first}"




# ==========================
# RIS helpers & converters
# ==========================

def ris_escape(s: str) -> str:
    """Minimal escaping for RIS (single-line values)."""
    if s is None:
        return ""
    s = str(s).replace("\r", " ").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _ris_line(tag: str, value: str) -> str:
    value = ris_escape(value)
    if not value:
        return ""
    return f"{tag}  - {value}"


def _name_to_ris_author(name: str) -> str:
    """Convert 'First Last' -> 'Last, First' when possible."""
    name = (name or "").strip()
    if not name:
        return ""
    if "," in name:
        return name
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        last = parts[-1]
        given = " ".join(parts[:-1])
        return f"{last}, {given}"
    return name


def _split_pages(pages: str) -> Tuple[Optional[str], Optional[str]]:
    if not pages:
        return None, None
    p = pages.strip().replace("–", "-").replace("--", "-")
    if "-" in p:
        a, b = p.split("-", 1)
        a, b = a.strip(), b.strip()
        return (a or None), (b or None)
    return (p.strip() or None), None


def arxiv_doi(arxiv_id: str) -> str:
    """arXiv DataCite DOI (best-effort). e.g. 10.48550/arXiv.2110.14051"""
    if not arxiv_id:
        return ""
    base = arxiv_id.split("v", 1)[0]  # drop version
    return f"10.48550/arXiv.{base}"


def format_arxiv_to_ris(r: arxiv.Result) -> str:
    arxiv_id = r.get_short_id()
    year = r.published.year if r.published else None
    da = ""
    if r.published:
        da = r.published.strftime("%Y/%m/%d")
    primary = getattr(r, "primary_category", None) or ""
    authors = [_name_to_ris_author(a.name) for a in r.authors]
    lines = []
    lines.append(_ris_line("TY", "RPRT"))  # report / preprint
    lines.append(_ris_line("TI", r.title))
    for a in authors:
        ln = _ris_line("AU", a)
        if ln:
            lines.append(ln)
    if year:
        lines.append(_ris_line("PY", str(year)))
    if da:
        lines.append(_ris_line("DA", da))
    lines.append(_ris_line("JO", "arXiv"))
    lines.append(_ris_line("T2", f"arXiv:{arxiv_id}"))
    if primary:
        lines.append(_ris_line("KW", primary))
    lines.append(_ris_line("DO", arxiv_doi(arxiv_id)))
    lines.append(_ris_line("UR", r.entry_id))
    lines.append("ER  -")
    return "\n".join([ln for ln in lines if ln])


CROSSREF_TYPE_TO_RIS = {
    "journal-article": "JOUR",
    "journal-issue": "JOUR",
    "proceedings-article": "CONF",
    "conference-paper": "CONF",
    "book-chapter": "CHAP",
    "book-section": "CHAP",
    "book": "BOOK",
    "monograph": "BOOK",
    "report": "RPRT",
    "posted-content": "RPRT",  # preprint / posted content
    "dissertation": "THES",
    "thesis": "THES",
}


def _crossref_best_date(m: Dict) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    for key in ("issued", "created", "published-print", "published-online"):
        dp = ((m.get(key) or {}).get("date-parts") or [])
        if dp and dp[0]:
            parts = dp[0]
            y = int(parts[0]) if len(parts) >= 1 and parts[0] else None
            mo = int(parts[1]) if len(parts) >= 2 and parts[1] else None
            d = int(parts[2]) if len(parts) >= 3 and parts[2] else None
            return y, mo, d
    return None, None, None


def ris_from_crossref(m: Dict) -> str:
    title_list = m.get("title") or []
    title = title_list[0] if title_list else ""
    ct = m.get("container-title") or []
    container = ct[0] if ct else ""
    typ = (m.get("type") or "").strip()
    ris_ty = CROSSREF_TYPE_TO_RIS.get(typ, "GEN")

    doi = (m.get("DOI") or "").strip()
    url = (m.get("URL") or "").strip()
    volume = (m.get("volume") or "").strip()
    number = (m.get("issue") or "").strip()
    pages = (m.get("page") or "").strip()
    publisher = (m.get("publisher") or "").strip()

    sp, ep = _split_pages(pages)

    # authors
    aus = []
    for a in (m.get("author") or []):
        family = (a.get("family") or "").strip()
        given = (a.get("given") or "").strip()
        if family and given:
            aus.append(f"{family}, {given}")
        else:
            name = (a.get("name") or "").strip()
            if name:
                aus.append(name)

    y, mo, d = _crossref_best_date(m)
    da = ""
    if y and mo and d:
        da = f"{y:04d}/{mo:02d}/{d:02d}"
    elif y and mo:
        da = f"{y:04d}/{mo:02d}"
    elif y:
        da = f"{y:04d}"

    lines = []
    lines.append(_ris_line("TY", ris_ty))
    lines.append(_ris_line("TI", title))
    for a in aus:
        ln = _ris_line("AU", a)
        if ln:
            lines.append(ln)
    if y:
        lines.append(_ris_line("PY", str(y)))
    if da:
        lines.append(_ris_line("DA", da))

    if ris_ty == "JOUR":
        if container:
            lines.append(_ris_line("JO", container))
            lines.append(_ris_line("JF", container))
        if volume:
            lines.append(_ris_line("VL", volume))
        if number:
            lines.append(_ris_line("IS", number))
    else:
        if container:
            # secondary title / proceedings / booktitle
            lines.append(_ris_line("T2", container))
        if publisher:
            lines.append(_ris_line("PB", publisher))

    if sp:
        lines.append(_ris_line("SP", sp))
    if ep:
        lines.append(_ris_line("EP", ep))
    if doi:
        lines.append(_ris_line("DO", doi))
    if url:
        lines.append(_ris_line("UR", url))

    lines.append("ER  -")
    return "\n".join([ln for ln in lines if ln])

@dataclass
class BibResult:
    raw: str
    ok: bool
    source: str
    matched_title: Optional[str]
    bibtex: Optional[str]
    ris: Optional[str]
    message: Optional[str] = None


# ==========================
# arXiv
# ==========================


def format_arxiv_to_bibtex(r: arxiv.Result) -> str:
    authors = [a.name for a in r.authors]
    authors_str = " and ".join(authors)
    year = r.published.year if r.published else None
    key = make_key(authors, year, r.title)
    arxiv_id = r.get_short_id()
    primary = getattr(r, "primary_category", None) or ""

    title = latex_escape(r.title.strip())
    url = r.entry_id

    return (
        f"@misc{{{key},\n"
        f"  title={{{{{title}}}}},\n"
        f"  author={{{{{latex_escape(authors_str)}}}}},\n"
        f"  year={{{{{year}}}}},\n"
        f"  eprint={{{{{arxiv_id}}}}},\n"
        f"  archivePrefix={{{{arXiv}}}},\n"
        f"  primaryClass={{{{{primary}}}}},\n"
        f"  url={{{{{url}}}}},\n"
        f"}}"
    )


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def arxiv_by_id(arxiv_id: str) -> Optional[arxiv.Result]:
    client = arxiv.Client()
    search = arxiv.Search(id_list=[arxiv_id])
    try:
        results = list(client.results(search))
        return results[0] if results else None
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def arxiv_search_title(title: str, max_results: int = 10) -> List[arxiv.Result]:
    client = arxiv.Client()
    # force title-field search for higher precision
    q = title.strip()
    q = q.strip('"')
    search = arxiv.Search(query=f'ti:"{q}"', max_results=max_results)
    try:
        return list(client.results(search))
    except Exception:
        return []


def best_arxiv_match(title: str, threshold: int) -> Tuple[Optional[arxiv.Result], int]:
    candidates = arxiv_search_title(title, max_results=12)
    if not candidates:
        # fallback to broader search (sometimes arXiv title index is picky)
        client = arxiv.Client()
        search = arxiv.Search(query=title, max_results=12)
        try:
            candidates = list(client.results(search))
        except Exception:
            candidates = []

    best, best_score = None, -1
    nt = _norm(title)
    for c in candidates:
        score = fuzz.token_set_ratio(nt, _norm(c.title))
        if score > best_score:
            best, best_score = c, score
    if best and best_score >= threshold:
        return best, best_score
    return None, best_score


# ==========================
# Crossref (DOI)
# ==========================


CR_BASE = "https://api.crossref.org"


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def crossref_work(doi: str) -> Optional[Dict]:
    try:
        r = requests.get(f"{CR_BASE}/works/{doi}", timeout=15)
        if r.status_code != 200:
            return None
        return r.json().get("message")
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def crossref_search(title: str, rows: int = 8) -> List[Dict]:
    try:
        r = requests.get(
            f"{CR_BASE}/works",
            params={"query.title": title, "rows": rows},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        return (r.json().get("message") or {}).get("items") or []
    except Exception:
        return []


def bibtex_from_crossref(m: Dict) -> str:
    title_list = m.get("title") or []
    title = title_list[0] if title_list else ""
    ct = m.get("container-title") or []
    container = ct[0] if ct else ""

    # authors
    authors = []
    for a in (m.get("author") or []):
        family = (a.get("family") or "").strip()
        given = (a.get("given") or "").strip()
        if family and given:
            authors.append(f"{family}, {given}")
        else:
            name = (a.get("name") or "").strip()
            if name:
                authors.append(name)

    year = None
    for key in ("issued", "created", "published-print", "published-online"):
        dp = ((m.get(key) or {}).get("date-parts") or [])
        if dp and dp[0] and dp[0][0]:
            year = int(dp[0][0])
            break

    doi = (m.get("DOI") or "").strip()
    url = (m.get("URL") or "").strip()
    volume = (m.get("volume") or "").strip()
    number = (m.get("issue") or "").strip()
    pages = (m.get("page") or "").strip()
    publisher = (m.get("publisher") or "").strip()
    typ = (m.get("type") or "").strip()

    entry_type = "misc"
    fields: Dict[str, str] = {}

    if typ in {"journal-article", "journal-issue"}:
        entry_type = "article"
        fields["journal"] = container
        if volume:
            fields["volume"] = volume
        if number:
            fields["number"] = number
        if pages:
            fields["pages"] = pages
    elif typ in {"proceedings-article", "conference-paper"}:
        entry_type = "inproceedings"
        fields["booktitle"] = container
        if pages:
            fields["pages"] = pages
        if publisher:
            fields["publisher"] = publisher
    else:
        entry_type = "misc"
        if container:
            fields["howpublished"] = container

    fields["title"] = title
    if authors:
        fields["author"] = " and ".join(authors)
    if year:
        fields["year"] = str(year)
    if doi:
        fields["doi"] = doi
    if url:
        fields["url"] = url

    key = make_key([a.split(",")[0] for a in authors] if authors else [], year, title)

    # render
    lines = [f"@{entry_type}{{{key},"]
    for k, v in fields.items():
        if not v:
            continue
        lines.append(f"  {k}={{{{{latex_escape(v)}}}}},")
    # remove trailing comma on last field
    if len(lines) > 1:
        lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines)


def best_crossref_match(title: str, threshold: int) -> Tuple[Optional[Dict], int]:
    items = crossref_search(title, rows=10)
    best, best_score = None, -1
    nt = _norm(title)
    for it in items:
        t = ((it.get("title") or [""])[0] if it.get("title") else "")
        if not t:
            continue
        score = fuzz.token_set_ratio(nt, _norm(t))
        if score > best_score:
            best, best_score = it, score
    if best and best_score >= threshold:
        return best, best_score
    return None, best_score


# ==========================
# OpenAlex & Semantic Scholar
# ==========================


OA_BASE = "https://api.openalex.org"
SS_BASE = "https://api.semanticscholar.org/graph/v1"


def _ua_headers() -> Dict[str, str]:
    # OpenAlex prefers a UA / mailto; users can optionally set OPENALEX_MAILTO in Streamlit secrets
    mailto = None
    try:
        mailto = st.secrets.get("OPENALEX_MAILTO")
    except Exception:
        mailto = None
    ua = "BibTeX-Converter/1.0"
    if mailto:
        ua += f" (mailto:{mailto})"
    return {"User-Agent": ua}


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def openalex_search(title: str, rows: int = 8) -> List[Dict]:
    try:
        r = requests.get(
            f"{OA_BASE}/works",
            params={"search": title, "per-page": rows},
            headers=_ua_headers(),
            timeout=15,
        )
        if r.status_code != 200:
            return []
        return (r.json() or {}).get("results") or []
    except Exception:
        return []


@st.cache_data(show_spinner=False, ttl=24 * 3600)
def semanticscholar_search(title: str, limit: int = 8) -> List[Dict]:
    try:
        r = requests.get(
            f"{SS_BASE}/paper/search",
            params={
                "query": title,
                "limit": limit,
                "fields": "title,year,authors,venue,externalIds,url",
            },
            headers=_ua_headers(),
            timeout=15,
        )
        if r.status_code != 200:
            return []
        return (r.json() or {}).get("data") or []
    except Exception:
        return []


def best_title_match_from_candidates(
    title: str, candidates: List[Tuple[str, Dict]], threshold: int
) -> Tuple[Optional[Dict], int]:
    nt = _norm(title)
    best, best_score = None, -1
    for cand_title, payload in candidates:
        score = fuzz.token_set_ratio(nt, _norm(cand_title))
        if score > best_score:
            best, best_score = payload, score
    if best and best_score >= threshold:
        return best, best_score
    return None, best_score


# ==========================
# Main resolution logic
# ==========================


def resolve_one(raw: str, threshold: int) -> BibResult:
    text = (raw or "").strip()
    if not text:
        return BibResult(raw=raw, ok=False, source="", matched_title=None, bibtex=None, ris=None, message="空输入")

    doi = extract_doi(text)
    arxiv_id = extract_arxiv_id(text)
    title = guess_title(text)

    # 1) DOI -> Crossref
    if doi:
        m = crossref_work(doi)
        if m:
            try:
                bib = bibtex_from_crossref(m)
                ris = ris_from_crossref(m)
                t = ((m.get("title") or [""])[0] if m.get("title") else None)
                return BibResult(raw=raw, ok=True, source="DOI/Crossref", matched_title=t, bibtex=bib, ris=ris)
            except Exception as e:
                return BibResult(raw=raw, ok=False, source="DOI/Crossref", matched_title=None, bibtex=None, ris=None, message=str(e))

    # 2) arXiv ID -> arXiv
    if arxiv_id:
        r = arxiv_by_id(arxiv_id)
        if r:
            return BibResult(raw=raw, ok=True, source="arXiv", matched_title=r.title, bibtex=format_arxiv_to_bibtex(r), ris=format_arxiv_to_ris(r))

    # 3) Try arXiv title matching (high precision)
    if title:
        r, score = best_arxiv_match(title, threshold=threshold)
        if r:
            return BibResult(
                raw=raw,
                ok=True,
                source=f"arXiv(标题匹配, score={score})",
                matched_title=r.title,
                bibtex=format_arxiv_to_bibtex(r),
                ris=format_arxiv_to_ris(r),
            )

    # 4) Semantic Scholar -> (prefer DOI/arXiv)
    if title:
        ss = semanticscholar_search(title, limit=10)
        cand = [(it.get("title") or "", it) for it in ss if it.get("title")]
        best, score = best_title_match_from_candidates(title, cand, threshold=max(70, threshold - 10))
        if best:
            ext = best.get("externalIds") or {}
            doi2 = (ext.get("DOI") or "").strip() or None
            arx2 = (ext.get("ArXiv") or "").strip() or None
            if arx2:
                r = arxiv_by_id(arx2)
                if r:
                    return BibResult(
                        raw=raw,
                        ok=True,
                        source=f"SemanticScholar→arXiv(score={score})",
                        matched_title=r.title,
                        bibtex=format_arxiv_to_bibtex(r),
                        ris=format_arxiv_to_ris(r),
                    )
            if doi2:
                m = crossref_work(doi2)
                if m:
                    return BibResult(
                        raw=raw,
                        ok=True,
                        source=f"SemanticScholar→DOI/Crossref(score={score})",
                        matched_title=((m.get("title") or [""])[0] if m.get("title") else None),
                        bibtex=bibtex_from_crossref(m),
                        ris=ris_from_crossref(m),
                    )

    # 5) OpenAlex -> (prefer DOI)
    if title:
        oa = openalex_search(title, rows=10)
        cand = [(it.get("title") or "", it) for it in oa if it.get("title")]
        best, score = best_title_match_from_candidates(title, cand, threshold=max(70, threshold - 10))
        if best:
            doi_url = (best.get("doi") or "").strip()
            doi3 = doi_url.replace("https://doi.org/", "").replace("http://doi.org/", "") if doi_url else None
            if doi3:
                m = crossref_work(doi3)
                if m:
                    return BibResult(
                        raw=raw,
                        ok=True,
                        source=f"OpenAlex→DOI/Crossref(score={score})",
                        matched_title=((m.get("title") or [""])[0] if m.get("title") else None),
                        bibtex=bibtex_from_crossref(m),
                        ris=ris_from_crossref(m),
                    )

    # 6) Crossref title search as last resort
    if title:
        best, score = best_crossref_match(title, threshold=max(65, threshold - 15))
        if best:
            doi4 = (best.get("DOI") or "").strip()
            m = crossref_work(doi4) if doi4 else None
            if m:
                return BibResult(
                    raw=raw,
                    ok=True,
                    source=f"Crossref(标题匹配, score={score})",
                    matched_title=((m.get("title") or [""])[0] if m.get("title") else None),
                    bibtex=bibtex_from_crossref(m),
                    ris=ris_from_crossref(m),
                )

    return BibResult(
        raw=raw,
        ok=False,
        source="",
        matched_title=None,
        bibtex=None,
        ris=None,
        message="没有可靠匹配。建议：直接粘贴 DOI / arXiv ID，或给更完整标题。",
    )


def split_entries(raw: str) -> List[str]:
    raw = (raw or "").strip()
    if not raw:
        return []

    lines = [l.rstrip() for l in raw.splitlines()]
    entries: List[str] = []
    cur = ""
    for line in lines:
        if not line.strip():
            continue
        if re.match(r"^\s*(\[\d+\]|\d+\.)\s+", line) and cur:
            entries.append(cur.strip())
            cur = line.strip()
        else:
            cur = (cur + " " + line.strip()).strip() if cur else line.strip()
    if cur:
        entries.append(cur.strip())

    # if user pasted one-per-line without numbering
    if len(entries) == 1 and "\n" in raw:
        maybe = [l.strip() for l in raw.splitlines() if l.strip()]
        if len(maybe) >= 2:
            return maybe
    return entries


# ==========================
# Streamlit UI
# ==========================


st.set_page_config(page_title="BibTeX Converter", page_icon="📚", layout="centered")

st.title("📚 BibTeX/RIS 自动转换工具")
st.markdown(
    """
**支持输入：** DOI / arXiv ID / arXiv 链接 / 论文标题 / 一段参考文献。

**检索顺序：** DOI(Crossref) → arXiv → arXiv标题匹配 → Semantic Scholar → OpenAlex → Crossref标题兜底。

**小技巧：**
- 直接贴 **DOI** 或 **arXiv ID** 最稳。
- 只用标题时，建议越完整越好（不要只截一半）。
"""
)

with st.sidebar:
    st.header("设置")
    threshold = st.slider("标题匹配阈值（越高越严格）", min_value=60, max_value=95, value=85, step=1)
    export_fmt = st.radio("导出格式", ["BibTeX (.bib)", "RIS (.ris)"], horizontal=True)
    realtime = st.toggle("实时模式（输入停顿后自动检索）", value=False)
    st.caption("提示：实时模式会更频繁调用外部接口。")

raw_text = st.text_area(
    "输入（可多条，换行或编号分隔）：",
    placeholder="例如：\n2110.14051\n\nA unified survey on anomaly, novelty, open-set...\n\n10.1007/s11633-023-1459-z",
    height=200,
    key="raw_input",
)


def should_run_realtime() -> bool:
    if not realtime:
        return False
    if not raw_text.strip():
        return False
    if st_autorefresh is None:
        st.warning("当前环境未安装 streamlit-autorefresh，实时模式不可用；请用按钮转换。")
        return False
    return True


# Track input stability for "实时模式"
if "prev_raw" not in st.session_state:
    st.session_state.prev_raw = ""
if "last_change" not in st.session_state:
    st.session_state.last_change = 0.0

if raw_text != st.session_state.prev_raw:
    st.session_state.prev_raw = raw_text
    st.session_state.last_change = time.time()


run_now = False

if should_run_realtime():
    st_autorefresh(interval=1000, key="auto_refresh")
    if time.time() - float(st.session_state.last_change) >= 0.8:
        run_now = True
    else:
        st.info("检测到你还在输入中…停顿约 1 秒后会自动检索。")
else:
    run_now = st.button("开始转换")


if run_now:
    entries = split_entries(raw_text)
    if not entries:
        st.warning("请输入内容。")
    else:
        with st.spinner("正在检索…"):
            results = [resolve_one(e, threshold=threshold) for e in entries]

        is_ris = export_fmt.startswith("RIS")
        ok_out = [ (r.ris if is_ris else r.bibtex) for r in results if r.ok and (r.ris if is_ris else r.bibtex) ]
        if ok_out:
            title = "✅ 汇总 RIS" if is_ris else "✅ 汇总 BibTeX"
            st.subheader(title)
            merged = "\n\n".join(ok_out)
            st.code(merged, language=("text" if is_ris else "latex"))
            st.download_button(
                "下载引用文件",
                data=merged,
                file_name=("references.ris" if is_ris else "references.bib"),
                mime=("application/x-research-info-systems" if is_ris else "application/x-bibtex"),
            )

        st.subheader("逐条结果")
        for i, r in enumerate(results, start=1):
            label = f"[{i}] " + ("✅" if r.ok else "❌")
            if r.matched_title:
                label += f" {r.matched_title}"
            with st.expander(label, expanded=(len(results) == 1)):
                st.markdown(f"**输入：** {r.raw}")
                if r.source:
                    st.markdown(f"**来源：** {r.source}")
                if r.ok and (r.bibtex or r.ris):
                    tab_bib, tab_ris = st.tabs(["BibTeX", "RIS"])
                    with tab_bib:
                        if r.bibtex:
                            st.code(r.bibtex, language="latex")
                        else:
                            st.info("该条目暂未生成 BibTeX。")
                    with tab_ris:
                        if r.ris:
                            st.code(r.ris, language="text")
                        else:
                            st.info("该条目暂未生成 RIS。")
                else:
                    st.error(r.message or "转换失败")

st.markdown("---")
st.caption("数据源：arXiv API / Crossref / Semantic Scholar / OpenAlex（吴老二还在测试，目前不抓取 Google Scholar 页面）")
