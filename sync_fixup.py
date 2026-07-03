"""Make the AnKing::testing cards fresh/reviewable, then push to the server."""
import os
from anki.collection import Collection
from anki.sync import SyncOutput

ENDPOINT, USER, PASSWORD = "http://127.0.0.1:27701/", "test", "test"
WORKDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sync_test")
R = SyncOutput.ChangesRequired

path = os.path.join(WORKDIR, "fixup.anki2")
for p in (path, path + "-wal", path + "-shm"):
    if os.path.exists(p):
        os.remove(p)

os.makedirs(WORKDIR, exist_ok=True)
col = Collection(path)
auth = col.sync_login(USER, PASSWORD, ENDPOINT)
out = col.sync_collection(auth, False)
if out.required in (R.FULL_DOWNLOAD, R.FULL_SYNC):
    col.close_for_full_sync()
    col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
    col = Collection(path)

card_ids = list(col.find_cards("deck:AnKing::testing"))
col.sched.schedule_cards_as_new(card_ids)
print(f"reset {len(card_ids)} AnKing::testing cards to the new queue")

out = col.sync_collection(auth, False)
if out.required in (R.FULL_DOWNLOAD, R.FULL_SYNC):
    col.close_for_full_sync()
    col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
col.close()
print("pushed AnKing::testing fixup to server")
