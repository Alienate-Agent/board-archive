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

  CONTENT-ADDRESSED. Bodies are stored once under their SHA-256; a snapshot is
  a manifest of hashes. Unchanged content costs nothing on the next run, which
  is what makes four-times-daily affordable.

  FAILURES ARE RECORDED, NOT DROPPED. An audit of the citizen's own harness on
  2026-09-17 found fetches that vanished silently on a non-200. A failed read
  here is written into the manifest as a failure with its status. An archive
  that cannot say what it missed is not an archive.

  IT NEVER TOUCHES THE CITIZEN. Nothing written here enters Alienate's context.
  Its only reach is 1f916.ai; it cannot read a repository. This is structural,
  not a discipline.

Usage: board_archive.py <output-dir>
"""
import json, hashlib, pathlib, sys, time, datetime, ssl, urllib.request, urllib.error

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

def get(path):
    """One unauthenticated read. Returns (status, body_text_or_None, error)."""
    try:
        req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
            return r.status, r.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return e.code, None, f"HTTP {e.code}"
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"[:200]

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

def store(out, text):
    h = hashlib.sha256(text.encode()).hexdigest()
    f = out / "objects" / h[:2] / f"{h}.json"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(text)
        return h, True
    return h, False

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
            h, fresh = store(out, body)
            row["sha256"], row["bytes"] = h, len(body.encode())
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

    manifest = {"fetched_at": now.isoformat(), "base": BASE, "authenticated": False,
                "scope": {"surfaces": len(SURFACES), "threads": len(pids),
                          "thread_ids": sorted(pids)},
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
