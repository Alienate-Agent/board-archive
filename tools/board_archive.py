#!/usr/bin/env python3
"""Mirror the 1f916.ai material bearing on the artwork, verbatim, with no key.

Design decisions, recorded here because they are the point:

  NO CREDENTIAL, EVER. Every read is unauthenticated, so any stranger can
  reproduce this archive byte for byte. A mirror only its operator could make
  is worth less than one anybody can check. /api/rail-events returns 401
  without a key and is therefore out of scope rather than fetched with one.

  VERBATIM. Nothing is shrunk, sampled or summarised. The harness that feeds
  the citizen bounds everything on purpose; this does the opposite on purpose.

  OBSERVED HISTORY, NOT FINAL STATE. Every fetch is timestamped, so a later
  reader can see that #5264 had 12 votes when Alienate commented and 47 later.
  A daily overwrite would lose exactly the thing a record is for.

  CONTENT-ADDRESSED, ON THE CONTENT AND NOT THE CLOCK. Every response this
  board serves opens with its own `now` and `now_utc`, so hashing raw bytes
  deduplicates nothing: the first scheduled run re-stored 32 of 43 bodies that
  had not changed a word, 1.36 MB, projecting to ~2.5 GB a year. Bodies are
  therefore addressed by a hash taken with the volatile fields removed, and the
  verbatim body is written once, the first time that content appears.
  Nothing observable is lost: every read records its own fetch time AND the
  board's `now` from that response, so the board's clock per fetch is kept in
  the manifest rather than in a duplicate megabyte.

  FAILURES ARE RECORDED, NOT DROPPED. An audit of the citizen's own harness on
  2026-09-17 found fetches that vanished silently on a non-200. A failed read
  here is written into the manifest as a failure with its status. An archive
  that cannot say what it missed is not an archive.

  IT NEVER TOUCHES THE CITIZEN. Nothing written here enters Alienate's context.
  Its only reach is 1f916.ai; it cannot read a repository. This is structural,
  not a discipline.

Usage: board_archive.py <output-dir>
"""
import json, hashlib, pathlib, sys, time, datetime, ssl, urllib.request, urllib.error, urllib.parse

BASE = "https://1f916.ai"
UA = "alienate-board-archive (public mirror; unauthenticated)"
PACE = 0.8

# Key-free surfaces. /api/rail-events is deliberately absent: it needs a key.
SURFACES = ("/api/pulse", "/api/front", "/api/new", "/api/official", "/api/docket",
            "/api/surface", "/api/grants", "/api/porch", "/api/attest", "/api/offers",
            "/api/listings", "/treasury", "/api/seals?citizen=Alienate&label=dossier",
            "/api/citizen/Alienate")

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = ssl.create_default_context()

def get(path, tries=4):
    """One unauthenticated read, with backoff on the board's edge limiter.

    Added 2026-09-19 after a run launched two minutes behind another took 18
    HTTP 429s and wrote 18 honest failures into its manifest. We are a guest on
    someone else's board: a 429 is a request to wait, not a fact about the
    world, and recording it as a permanent gap would put a hole in the record
    that the board never intended. Retry-After is obeyed when offered."""
    waits = (5, 20, 60)
    for attempt in range(tries):
        try:
            url = path if path.startswith("https://") else BASE + path
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
                return r.status, r.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                ra = e.headers.get("Retry-After") if e.headers else None
                try: wait = max(int(ra), 1) if ra else waits[attempt]
                except (TypeError, ValueError): wait = waits[attempt]
                time.sleep(min(wait, 120)); continue
            return e.code, None, f"HTTP {e.code}" + (f" after {attempt+1} tries" if attempt else "")
        except Exception as e:
            if attempt < tries - 1:
                time.sleep(waits[attempt]); continue
            return 0, None, f"{type(e).__name__}: {e}"[:200]
    return 0, None, "exhausted"

def thread_walk(pid):
    """Every comment on a post, following the board's own cursor to the end.

    The board serves `since` as a created_at:id keyset and its surface now
    advertises string OR number for it (Sol Advisor, 2026-09-17, after Tidemark
    broke on the change). The cursor is passed back exactly as served and never
    coerced. Pages are kept whole and in order."""
    pages, since, seen = [], None, set()
    for _ in range(80):
        path = f"/api/post/{pid}" + (f"?since={since}" if since is not None else "")
        st, body, err = get(path)
        pages.append({"path": path, "status": st, "body": body, "error": err})
        if st != 200 or not body: break
        try: d = json.loads(body)
        except Exception: break
        nxt = d.get("next_since")
        if not d.get("has_more") or nxt is None or nxt in seen: break
        seen.add(nxt); since = nxt
        time.sleep(PACE)
    return pages

# Fields the board stamps on every response regardless of whether anything
# changed. Excluded from the content hash only; never stripped from a stored body.
VOLATILE = ("now", "now_utc", "checked_at", "onchain_checked_at", "you", "wake")

def content_hash(text):
    """A hash of what the response SAYS, ignoring when it said it."""
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            return hashlib.sha256(json.dumps({k: v for k, v in d.items()
                                  if k not in VOLATILE}, sort_keys=True).encode()).hexdigest()
    except Exception:
        pass
    return hashlib.sha256(text.encode()).hexdigest()

def store(out, text):
    """Write the verbatim body once per distinct content. Returns
    (content_hash, body_hash, wrote_new)."""
    ch = content_hash(text)
    idx = out / "objects" / ch[:2] / f"{ch}.json"
    if idx.exists():
        return ch, hashlib.sha256(idx.read_bytes()).hexdigest(), False
    idx.parent.mkdir(parents=True, exist_ok=True); idx.write_text(text)
    return ch, hashlib.sha256(text.encode()).hexdigest(), True

# The artwork's own public account on Bluesky (added 2026-09-24, operator
# instruction). The artist posts there unsigned and Margin posts signed, so it
# is part of the performance's public record, and a Bluesky post can be
# deleted. Read through Bluesky's public AppView with no credential, like
# everything else here. Scope is the account's own feed and profile: the API
# embeds the post being replied to when the account replies to someone, and
# that is kept verbatim as served, but no one else's feed is walked.
BSKY = "https://public.api.bsky.app/xrpc/"
BSKY_ACTOR = "taasoart.bsky.social"

def bluesky_reads():
    """Profile, then the whole author feed, following Bluesky's own cursor to
    the end. Returns [(url, status, body, error), ...] in order."""
    out = []
    u = f"{BSKY}app.bsky.actor.getProfile?actor={BSKY_ACTOR}"
    st, body, err = get(u); out.append((u, st, body, err)); time.sleep(PACE)
    cursor, seen = None, set()
    for _ in range(50):
        u = f"{BSKY}app.bsky.feed.getAuthorFeed?actor={BSKY_ACTOR}&limit=100" + (
            f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        st, body, err = get(u); out.append((u, st, body, err))
        if st != 200 or not body: break
        try: nxt = json.loads(body).get("cursor")
        except Exception: break
        if not nxt or nxt in seen: break
        seen.add(nxt); cursor = nxt; time.sleep(PACE)
    return out

def main():
    out = pathlib.Path(sys.argv[1]).resolve()
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
    entries, new_objects, failures = [], 0, 0

    def record(path, st, body, err):
        nonlocal new_objects, failures
        row = {"path": path, "status": st,
               "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        if body is not None:
            ch, bh, fresh = store(out, body)
            row["content_sha"] = ch          # names the object file
            row["body_sha"] = bh             # sha of the bytes actually stored there
            row["bytes"] = len(body.encode())
            try:
                d = json.loads(body)
                if isinstance(d, dict) and d.get("now_utc"):
                    row["server_now"] = d["now_utc"]   # the board's own clock, kept per read
            except Exception:
                pass
            row["stored"] = fresh            # False = identical content already held
            new_objects += 1 if fresh else 0
        else:
            row["error"] = err or "no body"; failures += 1
        entries.append(row)

    for s in SURFACES:
        st, body, err = get(s); record(s, st, body, err); time.sleep(PACE)

    # Scope: the citizen's own posts, and every thread it has spoken in.
    # Both lists come from its public record, so the scope is derived from the
    # board rather than chosen here.
    st, body, _ = get("/api/citizen/Alienate")
    pids = set()
    if st == 200 and body:
        d = json.loads(body)
        pids |= {p["id"] for p in d.get("posts", []) if p.get("id")}
        pids |= {c["post_id"] for c in d.get("comments", []) if c.get("post_id")}
    for pid in sorted(pids):
        for pg in thread_walk(pid):
            record(pg["path"], pg["status"], pg["body"], pg["error"])
        time.sleep(PACE)

    bsky = bluesky_reads()
    for u, st, body, err in bsky:
        record(u, st, body, err)

    manifest = {"fetched_at": now.isoformat(), "base": BASE, "authenticated": False,
                "scope": {"surfaces": len(SURFACES), "threads": len(pids),
                          "thread_ids": sorted(pids),
                          "bluesky": {"actor": BSKY_ACTOR, "reads": len(bsky)}},
                "counts": {"reads": len(entries), "failures": failures,
                           "new_objects": new_objects},
                "reads": entries}
    mf = out / "snapshots" / now.strftime("%Y-%m-%d") / f"{stamp}.json"
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text(json.dumps(manifest, indent=1))
    print(f"{stamp}: {len(entries)} reads, {failures} failed, {new_objects} new objects -> {mf}")
    return 1 if failures and failures == len(entries) else 0

if __name__ == "__main__":
    sys.exit(main())
