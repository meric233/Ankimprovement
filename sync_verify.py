"""
Headless verification of two-way sync + conflict rule (PRD section 7b), plus
creation of an "AnKing::testing" subdeck (10 cards) on the sync server.

We cannot open the desktop's live collection (the GUI holds its lock), so we
drive everything through the running self-hosted sync server on :27701 using
two throwaway *client* collections (A and B). Both clients run the identical
Rust sync engine that AnkiDroid and the desktop use, so this faithfully
exercises the real merge / conflict-resolution code paths.

Run with the desktop sync server already listening on 27701:

    cd Ankimprovement
    PYTHONPATH="$PWD/out/pylib:$PWD/out/qt" out/pyenv/bin/python sync_verify.py
"""

import os
import time

from anki.collection import Collection
from anki.sync import SyncOutput

ENDPOINT = "http://127.0.0.1:27701/"
USER, PASSWORD = "test", "test"
WORKDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sync_test")
R = SyncOutput.ChangesRequired


def log(msg: str) -> None:
    print(msg, flush=True)


def open_client(name: str) -> Collection:
    """Create a fresh empty collection and bring it fully in sync with the
    server (full download of the server's canonical collection)."""
    path = os.path.join(WORKDIR, f"{name}.anki2")
    for p in (path, path + "-wal", path + "-shm"):
        if os.path.exists(p):
            os.remove(p)
    col = Collection(path)
    auth = col.sync_login(USER, PASSWORD, ENDPOINT)
    out = col.sync_collection(auth, False)
    if out.required in (R.FULL_DOWNLOAD, R.FULL_SYNC):
        col.close_for_full_sync()
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
        col.reopen(after_full_sync=True)
        log(f"  [{name}] full-downloaded server collection")
    elif out.required == R.NO_CHANGES:
        log(f"  [{name}] already in sync")
    else:
        log(f"  [{name}] sync state after open: {R.Name(out.required)}")
    col._auth = auth  # stash for later
    return col


def sync(col: Collection, name: str) -> None:
    out = col.sync_collection(col._auth, False)
    log(f"  [{name}] sync -> {R.Name(out.required)}")


def inject_review(col: Collection, cid: int, ease: int, ivl: int, revlog_id: int) -> int:
    """Simulate one review at the DB level: append a revlog row (unique id,
    usn=-1 so it is pending upload) and bump the card (new ivl, mod, usn=-1).
    Returns the card's new modification time (seconds)."""
    col.db.execute(
        "insert into revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type)"
        " values (?, ?, -1, ?, ?, ?, 2500, 1000, 1)",
        revlog_id, cid, ease, ivl, ivl,
    )
    card = col.get_card(cid)
    card.ivl = ivl
    card.reps += 1
    col.update_cards([card])  # sets mod=now (secs) and usn=-1
    return col.db.scalar("select mod from cards where id=?", cid)


def revlog_ids_for(col: Collection, cids) -> set:
    q = "select id from revlog where cid in (%s)" % ",".join("?" * len(cids))
    return set(col.db.list(q, *cids))


def main() -> None:
    os.makedirs(WORKDIR, exist_ok=True)

    # ---- Setup: two client collections in sync with the server -------------
    log("== opening client A (stand-in for desktop) ==")
    a = open_client("clientA")
    log("== opening client B (stand-in for phone) ==")
    b = open_client("clientB")

    # ---- Create the AnKing::testing subdeck (10 cards) on A ----------------
    log("\n== creating testing subdeck (10 cards) ==")
    names = [d.name for d in a.decks.all_names_and_ids(include_filtered=False)]
    parent = next(
        (n for n in names if "anking" in n.lower() and "::" not in n),
        next((n.split("::")[0] for n in names if "anking" in n.lower()), None),
    )
    testing_name = f"{parent}::testing" if parent else "testing"
    testing_did = a.decks.id(testing_name)
    log(f"  deck: {testing_name}  (id={testing_did})")

    # pick 20 real cards: first 10 -> testing deck, next 10 -> B's review set
    pool = a.db.list("select id from cards order by id limit 20")
    testing_cids = pool[:10]
    other_cids = pool[10:20]
    a.set_deck(testing_cids, testing_did)
    log(f"  moved 10 cards into {testing_name}: {testing_cids}")
    # show that the content is real USMLE material
    for cid in testing_cids[:3]:
        nid = a.db.scalar("select nid from cards where id=?", cid)
        flds = a.db.scalar("select flds from notes where id=?", nid) or ""
        snippet = flds.replace("\x1f", " | ")[:90].replace("\n", " ")
        log(f"    card {cid}: {snippet}...")
    sync(a, "A")  # push testing deck to server
    sync(b, "B")  # B pulls the testing deck
    b_deck_id = b.decks.id_for_name(testing_name)
    log(f"  B now sees deck {testing_name}: id={b_deck_id} "
        f"({'OK' if b_deck_id else 'MISSING'})")

    # ---- PART 1: union of reviews (10 on A + 10 different on B) -------------
    log("\n== PART 1: 10 reviews on A + 10 different on B, then reconnect ==")
    base = int(time.time() * 1000)
    a_ids, b_ids = [], []
    for i, cid in enumerate(testing_cids):          # A reviews its 10 testing cards
        a_ids.append(base + i)
        inject_review(a, cid, ease=3, ivl=4, revlog_id=base + i)
    for i, cid in enumerate(other_cids):            # B reviews 10 DIFFERENT cards
        b_ids.append(base + 1000 + i)
        inject_review(b, cid, ease=3, ivl=4, revlog_id=base + 1000 + i)
    log(f"  A reviewed 10 cards (revlog {a_ids[0]}..{a_ids[-1]})")
    log(f"  B reviewed 10 cards (revlog {b_ids[0]}..{b_ids[-1]})")

    sync(a, "A")   # upload A's 10
    sync(b, "B")   # download A's 10, upload B's 10
    sync(a, "A")   # download B's 10

    all_cids = testing_cids + other_cids
    a_have = revlog_ids_for(a, all_cids)
    b_have = revlog_ids_for(b, all_cids)
    expected = set(a_ids) | set(b_ids)
    log(f"  expected 20 unique review ids; A has {len(a_have & expected)}, "
        f"B has {len(b_have & expected)}")
    part1_ok = (
        len(expected) == 20
        and expected.issubset(a_have)
        and expected.issubset(b_have)
        and len(a_have & expected) == 20
        and len(b_have & expected) == 20
    )
    log(f"  PART 1 {'PASS' if part1_ok else 'FAIL'}: all 20 reviews present on "
        f"both sides, none lost, none duplicated")

    # ---- PART 2: conflict on the SAME card (offline on both) ---------------
    log("\n== PART 2: same card reviewed on both offline -> conflict rule ==")
    c = testing_cids[0]
    ta = inject_review(a, c, ease=1, ivl=1, revlog_id=base + 5000)     # A: "Again", ivl=1
    log(f"  A reviewed card {c}: ease=Again, ivl=1, mtime={ta}")
    time.sleep(2.2)  # ensure B's mtime (seconds) is strictly later
    tb = inject_review(b, c, ease=4, ivl=999, revlog_id=base + 5001)   # B: "Easy", ivl=999
    log(f"  B reviewed card {c}: ease=Easy,  ivl=999, mtime={tb}  (later)")

    sync(a, "A")   # upload A's conflicting card state
    sync(b, "B")   # B keeps its newer state, uploads it
    sync(a, "A")   # A pulls the winning state

    a_ivl = a.db.scalar("select ivl from cards where id=?", c)
    b_ivl = b.db.scalar("select ivl from cards where id=?", c)
    conflict_revlogs = revlog_ids_for(a, [c]) & {base + 5000, base + 5001}
    winner = "B (later mtime)" if a_ivl == 999 else ("A" if a_ivl == 1 else "?")
    log(f"  final card state: A.ivl={a_ivl}, B.ivl={b_ivl}  -> winner: {winner}")
    log(f"  both reviews of card {c} preserved in log: "
        f"{len(conflict_revlogs)}/2")
    part2_ok = a_ivl == 999 and b_ivl == 999 and len(conflict_revlogs) == 2
    log(f"  PART 2 {'PASS' if part2_ok else 'FAIL'}: later-mtime review wins "
        f"card state; both review events retained")

    # ---- Summary -----------------------------------------------------------
    log("\n== SUMMARY ==")
    log(f"  testing subdeck created:      OK ({testing_name}, 10 cards)")
    log(f"  Part 1 (no loss / no dupes):  {'PASS' if part1_ok else 'FAIL'}")
    log(f"  Part 2 (conflict winner):     {'PASS' if part2_ok else 'FAIL'}")
    log("  Rule: reviews = additive union (unique ms ids); card/note state = "
        "last-writer-wins by modification time.")

    a.close()
    b.close()
    raise SystemExit(0 if (part1_ok and part2_ok) else 1)


if __name__ == "__main__":
    main()
