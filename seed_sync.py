"""One-off: seed the local sync server from the desktop collection.

Mirrors what the desktop GUI does on Sync -> Upload:
  1. log in to the self-hosted server
  2. full-upload the collection (server is empty)
  3. upload media (blocks until complete)

Safe: a full *upload* only reads local data and writes to the server.
Requires the desktop GUI to be closed (collection must not be locked).
"""

import os
import time

from anki.collection import Collection
from anki.sync import SyncOutput  # == sync_pb2.SyncCollectionResponse

COL_PATH = os.path.expanduser(
    "~/Library/Application Support/Anki2/User 1/collection.anki2"
)
ENDPOINT = "http://127.0.0.1:27701/"
USERNAME = "test"
PASSWORD = "test"

R = SyncOutput.ChangesRequired


def name(req: int) -> str:
    try:
        return SyncOutput.ChangesRequired.Name(req)
    except Exception:
        return str(req)


def main() -> None:
    print(f"opening collection: {COL_PATH}")
    col = Collection(COL_PATH)
    try:
        print(f"logging in to {ENDPOINT} as {USERNAME!r} ...")
        auth = col.sync_login(USERNAME, PASSWORD, ENDPOINT)
        print(f"  ok (hkey len={len(auth.hkey)})")

        print("checking collection sync state ...")
        out = col.sync_collection(auth, False)
        if out.server_message:
            print("  server message:", out.server_message)
        print(f"  required = {name(out.required)}")

        if out.required in (R.FULL_UPLOAD, R.FULL_SYNC):
            print("performing full upload of collection ...")
            col.close_for_full_sync()
            col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
            col.reopen(after_full_sync=True)
            print("  collection uploaded")
        elif out.required == R.NORMAL_SYNC:
            print("  normal sync completed by sync_collection()")
        elif out.required == R.NO_CHANGES:
            print("  already in sync")
        elif out.required == R.FULL_DOWNLOAD:
            raise SystemExit(
                "Unexpected FULL_DOWNLOAD (local collection appears empty?) - aborting"
            )

        print("starting media sync (3.2 GB / ~33k files - this will take a while) ...")
        # sync_media() runs asynchronously on the backend; kick it off then poll
        # media_sync_status() until it is no longer active. Keep the collection
        # open the whole time (closing it aborts the transfer).
        col.sync_media(auth)

        last = ""
        started = time.time()
        # give the background task a moment to flip 'active' on
        time.sleep(1.0)
        while True:
            st = col.media_sync_status()
            p = st.progress
            line = f"checked={p.checked} added={p.added} removed={p.removed}"
            if line != last:
                print(f"  [{int(time.time() - started)}s] {line}")
                last = line
            if not st.active:
                break
            time.sleep(2.0)

        print(f"  media sync finished after {int(time.time() - started)}s")
        print("DONE - server seeded (collection + media).")
    finally:
        col.close()


if __name__ == "__main__":
    main()
