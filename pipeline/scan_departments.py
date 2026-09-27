"""Read each program's faculty listing: ranks for people we hold, and new hires.

    python -m pipeline.scan_departments                    # crawl, write build/department_scan_<date>.csv
    python -m pipeline.scan_departments --text-dir DIR     # also read pages saved from a browser
    python -m pipeline.scan_departments --apply ranks      # rank changes (either direction) -> roster
    python -m pipeline.scan_departments --apply all        # ranks, and tenure-line new hires

One polite fetch per page, a handful of pages per program: the stored URL and
the faculty/people/directory pages it links to. A page that names at least
LISTING_MIN of the program's current roster is treated as its faculty listing.

For each roster person found on a page, the nearest title is read. A title of
assistant, associate or full professor is a rank and, with --apply, becomes
the person's rank in either direction: the department page is the better
source (Alex, 2026-09-27), and five people a Scholar profile's loose
"Professor" had promoted turned out to be associates. Anything else (of
practice, clinical, adjunct, visiting, research, teaching, lecturer,
instructor, emeritus, affiliate, a named chair) is not tenure-line and is
reported, not applied.

New hires: tenure-line titles on a listing page whose name matches nobody we
hold (at any program, current or departed) are added to the roster when
--apply is given, per the 2026-09-27 policy: trust the department page, take
corrections. A name that matches someone at another program is reported as a
possible move instead. Dead URLs and pages a script cannot read (JavaScript
directories) are reported for a person with a browser; --text-dir takes the
text they save, named <department_id>__anything.txt with the URL on line 1.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import BUILD_DIR, ROSTER_DIR
from .match_openalex import jaro_winkler, read_csv, write_csv

UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/128.0 Safari/537.36 planning-citation-metrics (planning-citations@austin.utexas.edu)")}
LISTING_MIN = 2          # roster people a page must name to count as the program's faculty listing
MAX_PAGES = 6
LINK_WORDS = re.compile(r"\b(faculty|people|our people|directory|staff|who we are|our team|profiles?)\b", re.I)
COMMON_PATHS = ("people", "faculty", "faculty-staff", "faculty-and-staff", "directory", "about/people", "our-people")

RANKS = {"assistant professor": "assistant", "associate professor": "associate",
         "full professor": "full", "professor": "full"}
ABBREV = [(re.compile(r"\bassoc\.?\s+prof(?:essor|\.)?", re.I), "associate professor"),
          (re.compile(r"\bass(?:is)?t\.?\s+prof(?:essor|\.)?", re.I), "assistant professor"),
          (re.compile(r"\bprof\.\s", re.I), "professor ")]
HONORIFIC = re.compile(r"^(?:dr|prof|professor|mr|mrs|ms|mx)\.?\s+", re.I)
# "Daniel Rose Professor of Urban Economics", "Class of 1958 Career Development Professor":
# a named chair says nothing reliable about rank. Words before "Professor" on the
# line that are neither a rank word nor the person's own name mark one.
CHAIR_WORDS = re.compile(r"\b(chair|professorship|distinguished|endowed|career development|foundation|family|memorial|class of \d{4})\b", re.I)
TITLE_RE = re.compile(r"\b(assistant professor|associate professor|full professor|professor|lecturer|instructor|"
                      r"professor of practice|teaching professor|research professor|clinical professor|adjunct|"
                      r"emerit[ua]s?|visiting|affiliate|senior lecturer|postdoctoral|fellow|dean|director)\b", re.I)
NOT_TENURE = re.compile(r"\b(of practice|clinical|adjunct|visiting|research (assistant|associate|professor)|"
                        r"teaching (assistant|associate|professor)|lecturer|instructor|emerit\w*|affiliate|"
                        r"postdoc\w*|fellow|honorary|courtesy|in residence|part[- ]time)\b", re.I)
NAME_RE = re.compile(r"((?:[A-Z][a-zA-Z'’\-\.]+ ){1,3}[A-Z][a-zA-Z'’\-]+)")
NOT_NAME_WORDS = {"assistant", "associate", "professor", "department", "planning", "urban", "regional", "school",
                  "college", "university", "faculty", "director", "chair", "program", "email", "phone", "office",
                  "research", "interests", "education", "contact", "the", "of", "and", "graduate", "undergraduate",
                  "studies", "policy", "design", "architecture", "landscape", "community", "city", "environmental",
                  "read", "more", "view", "profile", "bio", "full", "emeritus", "emerita", "dean", "lecturer",
                  "curriculum", "vitae", "spotlight", "stories", "story", "student", "students", "center", "centre",
                  "leadership", "thought", "campus", "search", "publications", "publication", "press", "meet",
                  "recent", "news", "events", "idea", "affiliated", "faculy", "staff", "team", "welcome", "about",
                  "home", "apply", "admissions", "alumni", "people", "institute", "lab", "laboratory", "seminar"}


def norm(s: str) -> str:
    s = (s or "").replace("\u2019", "").replace("'", "")            # D'Ignazio and D\u2019Ignazio -> dignazio
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s.lower())).strip()


def text_of(page: str) -> str:
    """Visible text with line structure kept: listings put a name on one line
    and the title on the next."""
    page = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr|/td|/th|/dd|/dt)[^>]*>", "\n", page, flags=re.I)
    page = re.sub(r"<[^>]+>", " ", page)
    page = html.unescape(page)
    page = re.sub(r"[ \t\xa0]+", " ", page)
    return re.sub(r"\n\s*\n+", "\n", page)


def classify_title(window: str) -> tuple[str | None, str | None]:
    """(rank or None, the title text) for the first title in a text window.
    Rank is None for anything not tenure-line, so a lecturer never becomes a
    professor because the word appears."""
    for pat, rep in ABBREV:
        window = pat.sub(rep, window)
    m = TITLE_RE.search(window)
    if not m:
        return None, None
    # Judge the title's own line, not a fixed span: a span reaches into the
    # previous person's "of Practice" and turns the next assistant professor
    # into a non-tenure title.
    ls = window.rfind("\n", 0, m.start()) + 1
    le = window.find("\n", m.end())
    phrase = window[ls:le if le >= 0 else len(window)]
    title = m.group(1).lower()
    if NOT_TENURE.search(phrase) or title not in RANKS:
        return None, title
    if title == "professor":
        # a named chair precedes the word ("Daniel Rose Professor of..."); a
        # departmental chair follows it ("Professor and Chair") and is fine
        before = phrase[:phrase.lower().find("professor")]
        # one or more capitalised words immediately before "Professor", not
        # separated from it by punctuation: "Germeshausen Professor", "Daniel
        # Rose Professor". A person's own name is separated by a comma or a
        # line break in every listing we have seen ("Ada Lovelace, Professor").
        tail = before.rstrip()
        named = CHAIR_WORDS.search(before) or (
            re.search(r"(?:^|\s)(?:[A-Z][\w\.\-]*\s+){1,4}$", tail + " ")
            and not re.search(r"(,|;|\||\.|AICP|Ph\.?D\.?|\)|//)\s*$", tail)
            and not re.fullmatch(r"\s*(Full|Assistant|Associate|Adjunct|Visiting|Clinical)?\s*", tail))
        if named:
            return None, "named chair"          # rank unreadable from a chair title
    return RANKS[title], title


def find_person(text: str, display_name: str, last_name: str) -> int:
    """Offset of the person's name in the text, or -1. Tries 'First Last',
    'Last, First', and First + Last with something in between (a middle name)."""
    parts = display_name.split()
    first, last = parts[0], (last_name or parts[-1])
    for pat in (re.escape(display_name), rf"{re.escape(last)},\s+{re.escape(first)}",
                rf"\b{re.escape(first)}\s+(?:[A-Z][\w\.\-]*\s+)?{re.escape(last)}\b"):
        m = re.search(pat, text)
        if m:
            return m.start()
    return -1


def rank_on_page(text: str, display_name: str, last_name: str) -> tuple[str | None, str | None, str]:
    """(rank, title, snippet) for a roster person on a page, or (None, None, '').

    The title must be on the name's own line or within the next two, and the
    name's line must be short: a news paragraph mentioning someone near the
    word "Professor" is not a listing entry. Every occurrence is tried, so a
    news item above the directory does not hide the entry below it."""
    parts = display_name.split(); first, last = parts[0], (last_name or parts[-1])
    pats = [re.escape(display_name), rf"{re.escape(last)},\s+{re.escape(first)}",
            rf"\b{re.escape(first)}\s+(?:[A-Z][\w\.\-]*\s+)?{re.escape(last)}\b"]
    fallback = (None, None, "")
    for pat in pats:
        for m in re.finditer(pat, text):
            ls = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end()); line_end = len(text) if line_end < 0 else line_end
            line = text[ls:line_end]
            extra = len(line.strip()) - (m.end() - m.start())
            if len(line) > 110 or extra > 45:
                continue                                        # prose or a headline, not an entry
            nxt = text.find("\n", line_end + 1); nxt2 = text.find("\n", nxt + 1) if nxt >= 0 else -1
            window = text[ls:(nxt2 if nxt2 >= 0 else len(text))]
            rank, title = classify_title(window)
            snippet = re.sub(r"\s+", " ", window[:160]).strip()
            if rank:
                return rank, title, snippet
            if title and not fallback[1]:
                fallback = (None, title, snippet)
    return fallback


def names_with_tenure_titles(text: str) -> list[tuple[str, str]]:
    """(name, rank) for every tenure-line title on the page that has a
    name-like line just before it. Strict on purpose: a false new hire puts a
    stranger on the site."""
    out = []
    for m in TITLE_RE.finditer(text):
        ls = text.rfind("\n", 0, m.start()) + 1
        le = text.find("\n", m.end())
        rank, title = classify_title(text[ls:le if le >= 0 else len(text)])
        if not rank:
            continue
        before = text[max(0, m.start() - 140):m.start()]
        lines = [l.strip(" ,|•·-") for l in before.split("\n") if l.strip()]
        cand = None
        for line in reversed(lines[-3:]):
            clean = HONORIFIC.sub("", line.strip()).strip()
            nm = NAME_RE.fullmatch(clean)
            words = clean.split()
            if (nm and not (set(norm(clean).split()) & NOT_NAME_WORDS) and 2 <= len(words) <= 4
                    and not clean.isupper()                                   # SPOTLIGHT STORIES
                    and sum(1 for w in words if w[:1].isupper()) == len(words)):
                cand = clean; break
        if cand:
            out.append((cand, rank))
    seen, uniq = set(), []
    for n, r in out:
        if norm(n) not in seen:
            seen.add(norm(n)); uniq.append((n, r))
    return uniq


def known_person(name: str, people: list[dict]) -> dict | None:
    """The roster person this page name is, if any: same last name, and a
    given name that agrees with one of ours.

    "Agrees" means equal, an initial of it, a close variant (Jaro-Winkler),
    or a short form -- "Nora" for Leonora, "Beth" for Elizabeth -- checked
    against every given-name token we hold, so "J. Phillip Thompson" on a
    page matches the roster's "J. Phillip Thompson" even though our first_name
    field says Phillip. Deliberately generous: treating a near-name as someone
    we already hold can cost a missed new hire, never a stranger on the site."""
    toks = norm(name).split()
    if len(toks) < 2:
        return None
    given, last = toks[:-1], toks[-1]
    best, score = None, 0.0

    def agrees(a: str, b: str) -> float:
        if a == b:
            return 1.0
        if len(a) <= 2 and b.startswith(a[0]) or len(b) <= 2 and a.startswith(b[0]):
            return 0.9                                            # an initial
        if len(a) >= 3 and len(b) >= 3 and (a.endswith(b) or b.endswith(a)):
            return 0.9                                            # a short form
        return jaro_winkler(a, b)

    for p in people:
        ours = norm(p["display_name"]).split()
        if not ours or ours[-1] != last:
            pl = norm(p.get("last_name") or "").split()
            if not pl or pl[-1] != last:
                continue
        ours_given = ours[:-1] or norm(p.get("first_name") or "").split()
        s = max((agrees(g, o) for g in given for o in ours_given), default=0.0)
        if s >= 0.85 and s > score:
            best, score = p, s
    return best


# ------------------------------------------------------------------ crawl

def fetch(url: str, timeout: int = 20) -> tuple[str | None, str]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.geturl(), r.read(800000).decode("utf-8", "replace")
    except Exception as e:                      # dead URL, timeout, TLS: all "cannot read"
        return None, f"{type(e).__name__}"


def candidate_pages(base: str, page: str) -> list[str]:
    out = []
    for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', page, flags=re.S | re.I):
        lab = re.sub(r"<[^>]+>", " ", label)
        if (LINK_WORDS.search(lab) or LINK_WORDS.search(href)) and not re.search(r"student|alumni|news|event", lab + href, re.I):
            out.append(urllib.parse.urljoin(base, href))
    root = base if base.endswith("/") else base.rsplit("/", 1)[0] + "/"
    out += [urllib.parse.urljoin(root, p + "/") for p in COMMON_PATHS]
    seen, uniq = set(), []
    for u in out:
        u = u.split("?")[0]
        if u not in seen and urllib.parse.urlparse(u).netloc == urllib.parse.urlparse(base).netloc:
            seen.add(u); uniq.append(u)
    return uniq[:MAX_PAGES - 1]


def scan_department(dept: dict, roster: list[dict], everyone: list[dict], saved: list[tuple[str, str]]) -> list[dict]:
    """Rows for one program: a status row, a row per roster person, rows for
    tenure-line names not on the roster."""
    rows = []
    pages: list[tuple[str, str]] = list(saved)          # (url, text) from a browser pass, if any
    status = "ok"
    if dept.get("url"):
        final, page = fetch(dept["url"])
        if final is None:
            status = f"dead_url ({page})"
        else:
            pages.append((final, text_of(page)))
            for u in candidate_pages(final, page):
                time.sleep(0.8)
                f2, p2 = fetch(u)
                if f2:
                    pages.append((f2, text_of(p2)))
    elif not saved:
        status = "no_url"
    # which pages are the listing?
    def named_on(text):
        return sum(1 for p in roster if find_person(text, p["display_name"], p["last_name"]) >= 0)
    listing = [(u, t) for u, t in pages if named_on(t) >= LISTING_MIN]

    def distinguishes_ranks(text):
        """Laval's English page calls every professeur 'Professor'. If a page
        gives our people titles and none of them is assistant or associate,
        it is not telling us ranks, and its 'Professor' means nothing."""
        seen = [classify_title(text[find_person(text, p["display_name"], p["last_name"]):][:300])[1]
                for p in roster if find_person(text, p["display_name"], p["last_name"]) >= 0]
        seen = [t for t in seen if t]
        return len(seen) < 2 or any(t in ("assistant professor", "associate professor") for t in seen)
    if status == "ok" and not listing:
        status = "no_listing_found" if pages else "unreadable"
    rows.append({"department_id": dept["department_id"], "department": dept["short_name"], "status": status,
                 "url": (listing[0][0] if listing else dept.get("url")), "kind": "status"})
    flat = {u for u, t in listing if not distinguishes_ranks(t)}
    for p in roster:
        best = (None, None, "", "")
        for u, t in (listing or pages):
            rank, title, snip = rank_on_page(t, p["display_name"], p["last_name"])
            if rank == "full" and u in flat:
                rank, title = None, "professor (page does not distinguish ranks)"
            if rank:
                best = (rank, title, snip, u); break
            if title and not best[1]:
                best = (None, title, snip, u)
        rank, title, snip, u = best
        kind = ("not_found" if not title and not snip else
                "chair_title?" if title == "named chair" else
                "rank_ok" if rank == p["rank"] else
                "promotion" if rank and RANK_ORDER[rank] > RANK_ORDER[p["rank"]] else
                "demotion" if rank else "non_tenure_title?")
        rows.append({"department_id": dept["department_id"], "department": dept["short_name"], "status": status,
                     "url": u, "person_id": p["person_id"], "name": p["display_name"], "roster_rank": p["rank"],
                     "site_title": title or "", "site_rank": rank or "", "kind": kind, "snippet": snip})
    seen_new = set()
    for u, t in listing:
        names = names_with_tenure_titles(t)
        ours = sum(1 for n, _ in names if (kp := known_person(n, everyone)) and any(kp["person_id"] == p["person_id"] for p in roster))
        unknown = [(n, r) for n, r in names if not known_person(n, everyone)]
        # A program's own listing names mostly people we hold. More unknown
        # tenure-line names than known ones means a college- or school-wide
        # directory (Iowa State's design school: 70 strangers beside our 10),
        # and its strangers are architects and landscape faculty, not hires.
        broad = len(unknown) > max(3, ours)
        for name, rank in names:
            kp = known_person(name, everyone)
            if kp and any(kp["person_id"] == p["person_id"] for p in roster):
                continue                                 # one of ours, handled above
            key = norm(name)
            if key in seen_new:
                continue
            seen_new.add(key)
            kind = "possible_move" if kp else ("new_hire?" if broad else "new_hire")
            rows.append({"department_id": dept["department_id"], "department": dept["short_name"], "status": status,
                         "url": u, "person_id": kp["person_id"] if kp else "", "name": name, "roster_rank": "",
                         "site_title": rank, "site_rank": rank, "kind": kind,
                         "snippet": "page lists more unknown tenure-line names than ours; likely college-wide" if broad else ""})
    return rows


RANK_ORDER = {"assistant": 0, "associate": 1, "full": 2}
FIELDS = ["department_id", "department", "status", "url", "person_id", "name", "roster_rank",
          "site_title", "site_rank", "kind", "snippet"]


def load_saved(text_dir: Path | None) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list] = {}
    if not text_dir or not text_dir.exists():
        return out
    for f in sorted(text_dir.glob("*.txt")):
        did = f.name.split("__", 1)[0]
        lines = f.read_text(encoding="utf-8", errors="replace").split("\n", 1)
        out.setdefault(did, []).append((lines[0].strip(), lines[1] if len(lines) > 1 else ""))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text-dir", type=Path, help="pages saved from a browser: <department_id>__*.txt, URL on line 1")
    ap.add_argument("--only", help="comma-separated department_ids")
    ap.add_argument("--out", type=Path, default=BUILD_DIR / f"department_scan_{date.today().isoformat()}.csv")
    ap.add_argument("--apply", choices=("ranks", "all"),
                    help="ranks: write rank changes in either direction; all: also add tenure-line new hires")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)

    _, depts = read_csv(ROSTER_DIR / "department.csv")
    _, people = read_csv(ROSTER_DIR / "person.csv")
    _, affs = read_csv(ROSTER_DIR / "affiliation.csv")
    by_pid = {p["person_id"]: p for p in people}
    current = [a for a in affs if not a["end_date"] and a["is_primary"] == "1" and a["appointment_type"] == "regular"]
    roster: dict[str, list[dict]] = {}
    for a in current:
        p = by_pid.get(a["person_id"])
        if p:
            roster.setdefault(a["department_id"], []).append({**p, "rank": a["rank"]})
    saved = load_saved(args.text_dir)
    if args.only:
        want = set(args.only.split(","))
        depts = [d for d in depts if d["department_id"] in want]

    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(lambda d: scan_department(d, roster.get(d["department_id"], []), people,
                                                        saved.get(d["department_id"], [])), depts))
    rows = [r for rs in results for r in rs]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    status = {r["department"]: r["status"] for r in rows if r["kind"] == "status"}
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"{len(depts)} programs: " + ", ".join(f"{v} {k}" for k, v in sorted(
        {s.split(' ')[0]: list(status.values()).count(s) for s in set(status.values())}.items())))
    print("rows: " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())))
    print(f"-> {args.out}")
    if not args.apply:
        return

    # ---- apply: promotions, and tenure-line new hires from listing pages ----------
    today = date.today().isoformat()
    aff_fields, affs = read_csv(ROSTER_DIR / "affiliation.csv")
    pfields, people = read_csv(ROSTER_DIR / "person.csv")
    next_aff = max(int(a["affiliation_id"]) for a in affs) + 1
    next_pid = max(int(p["person_id"]) for p in people) + 1
    promoted = added = 0
    for r in rows:
        if r["kind"] in ("promotion", "demotion"):
            for a in affs:
                if a["person_id"] == r["person_id"] and not a["end_date"] and a["is_primary"] == "1":
                    a["end_date"] = today
                    a["source"] = f"{a['source']} | closed {today}: {r['kind']} read from department page {r['url']}"
                    affs.append({"affiliation_id": next_aff, "person_id": a["person_id"], "department_id": a["department_id"],
                                 "rank": r["site_rank"], "appointment_type": "regular", "is_primary": 1, "start_date": today,
                                 "end_date": None, "source": f"rank verified {today}; department page {r['url']}: \"{r['site_title']}\""})
                    next_aff += 1; promoted += 1
                    break
        elif r["kind"] == "new_hire" and r["status"] == "ok" and args.apply == "all":
            parts = r["name"].split()
            people.append({k: None for k in pfields} | {"person_id": next_pid, "first_name": parts[0],
                          "last_name": parts[-1], "middle_name": " ".join(parts[1:-1]) or None,
                          "display_name": f"{parts[0]} {parts[-1]}",
                          "notes": f"added {today} from the department page {r['url']} (\"{r['site_title']}\")"})
            affs.append({"affiliation_id": next_aff, "person_id": next_pid, "department_id": r["department_id"],
                         "rank": r["site_rank"], "appointment_type": "regular", "is_primary": 1, "start_date": today,
                         "end_date": None, "source": f"rank verified {today}; new hire from department page {r['url']}"})
            next_aff += 1; next_pid += 1; added += 1
    write_csv(ROSTER_DIR / "affiliation.csv", aff_fields, affs)
    write_csv(ROSTER_DIR / "person.csv", pfields, people)
    print(f"applied: {promoted} rank changes, {added} new hires" + (" (new hires held; --apply all adds them)" if args.apply == "ranks" else ""))


if __name__ == "__main__":
    main()
