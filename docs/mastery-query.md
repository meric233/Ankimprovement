# Mastery query (USMLE Step 1 project)

The **mastery query** is our committed engine change. It is a new backend call
that returns, per **topic**, the number of cards **mastered** and the **average
recall**, fast enough to power the dashboard on a 50,000-card collection.

- **Topic** = a tag at a configurable prefix + depth. Default for this project:
  the AnKing **First Aid** section tags
  (`#AK_Step1_v11::#FirstAid::<NN_System>`), which map 1:1 onto the USMLE Step 1
  content outline (16 sections).
- **Mastered** = a *review* card whose interval has reached Anki's "mature"
  threshold (`interval >= 21` days). This is Anki's own definition of a
  well-learned card and is stable day to day.
- **Average recall** = mean FSRS retrievability *R* (0–1) over all cards in the
  topic, computed "right now". Brand-new / unseen cards (no FSRS memory state)
  are counted in the topic total with `R = 0`.

Backend RPC: `StatsService.MasteryByTopic(MasteryByTopicRequest) ->
MasteryByTopicResponse`, exposed in Python as
`col.mastery_by_topic(search=..., tag_prefix=..., topic_depth=...)`.

## Why this had to be a Rust change, not a Python add-on

1. **FSRS lives in Rust.** A card's memory state (stability/difficulty) and the
   retrievability formula `R = (1 + FACTOR·t/S)^(-DECAY)` are implemented in the
   Rust core and the `fsrs` crate (`rslib/src/scheduler/`, `memory_state.rs`).
   The per-card `memory_state` is parsed from a packed JSON blob in the `cards`
   table by the Rust storage layer. A Python add-on would have to *reimplement*
   FSRS retrievability and re-parse that blob — duplicating engine math that can
   (and does) change between FSRS versions, and drifting out of sync.

2. **Performance on 50k cards.** The query must aggregate every card (load
   memory state, compute *R*, join to note tags, bucket by topic) fast enough to
   power a live dashboard. Doing this in Rust over the SQLite layer is a single
   in-process pass; the same work from Python would mean tens of thousands of
   PyO3/protobuf round-trips (one per card) plus Python-side FSRS math — orders
   of magnitude slower.

3. **One engine, two apps.** Desktop (Python/Qt) and AnkiDroid (Kotlin) share
   this Rust core through the same protobuf interface. Implementing the query
   once in Rust means **both** clients call the identical, tested code path; a
   Python plugin would only ever exist on desktop.

4. **Consistency & correctness.** Reusing the engine's own
   `current_retrievability_seconds`, `seconds_since_last_review`, card type /
   interval semantics, and tag parsing guarantees our numbers match what the
   scheduler actually believes — no separate, slightly-wrong shadow
   implementation.

The query is **read-only**: it performs no `transact`, writes nothing, and adds
no undo entry, so it cannot corrupt the collection or disturb undo/redo (proven
by the `query_is_read_only_and_undo_safe` test).

## Upstream files touched (and merge risk)

| File | Change | Merge risk |
|---|---|---|
| `proto/anki/stats.proto` | Added one `rpc MasteryByTopic` line to `StatsService` + 3 new messages | **Low** — additive; conflicts only if upstream edits the same service block |
| `rslib/src/stats/mod.rs` | Added `mod mastery;` | **Very low** — one line |
| `rslib/src/stats/service.rs` | Added one trait method `mastery_by_topic` delegating to the impl | **Low** — additive method in an `impl` block |
| `rslib/src/stats/mastery.rs` | **New file** — all query logic + tests | **None** — new file, nothing upstream to conflict with |
| `pylib/anki/collection.py` | Added one `mastery_by_topic` wrapper method | **Low** — additive method |

Everything else (`_backend_generated.py`, `*_pb2.py`, `OUT_DIR/backend.rs`) is
**generated** by `./ninja pylib` and is never hand-edited, so it carries no
merge risk.

**Overall:** the footprint is one new file plus four small additive edits. A
future rebase onto upstream Anki should be low-effort; the only realistic
conflict point is the `StatsService` block in `stats.proto` if upstream adds
RPCs there too, which is a trivial textual merge.

## Tests

- Rust (`rslib/src/stats/mastery.rs`, run `cargo test -p anki --lib stats::mastery`):
  - `topic_for_tag_respects_prefix_and_depth`
  - `topic_for_tag_skips_non_matching_and_handles_empty_prefix`
  - `groups_by_section_and_counts_mastered_and_new`
  - `card_with_two_section_tags_counts_in_both_topics`
  - `depth_two_splits_into_subsections`
  - `query_is_read_only_and_undo_safe`
- Python (`pylib/tests/test_stats.py::test_mastery_by_topic`): end-to-end call
  through the protobuf bridge.
