<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import type { StudyDashboardResponse } from "@generated/anki/stats_pb";

    export let dashboard: StudyDashboardResponse;
    export let prefix: string;

    const pct = (x: number): string => `${Math.round(x * 100)}%`;

    $: memory = dashboard.memory;
    $: coverage = dashboard.coverage;
    $: topics = [...dashboard.topics].sort(
        (a, b) => a.averageRecall - b.averageRecall,
    );

    // Every score card shows the same horizon columns: now, and two "no study"
    // projections. The first is "today"; the rest assume no reviewing in between.
    const horizonDefs = [
        { days: 0, label: "Today" },
        { days: 5, label: "+5 days" },
        { days: 10, label: "+10 days" },
    ];
    const readinessFor = (days: number) =>
        dashboard.readiness.find((r) => r.horizonDays === days);
    const memoryFor = (days: number) =>
        memory?.horizons.find((h) => h.days === days);

    // The over-confidence gap: today's pass chance minus the furthest "no study"
    // projection. A large drop means current readiness is fragile.
    $: today = dashboard.readiness.find((r) => r.horizonDays === 0);
    $: later = [...dashboard.readiness]
        .filter((r) => r.horizonDays > 0)
        .sort((a, b) => b.horizonDays - a.horizonDays)[0];
    $: gap = today && later ? today.pPass - later.pPass : 0;
    $: laterDays = later?.horizonDays ?? 10;

    // Ungated memory-decay over-confidence signal (shown even while readiness
    // abstains): how far current recall falls over the horizons with no review.
    $: memHorizons = memory?.horizons ?? [];
    $: memGap =
        memory && memory.studiedCards > 0 && memHorizons.length > 1
            ? memHorizons[0].meanRecall - memHorizons[memHorizons.length - 1].meanRecall
            : 0;

    const short = (topic: string): string =>
        topic.startsWith(prefix) ? topic.slice(prefix.length) : topic;
</script>

<div class="readiness">
    <h1>Readiness</h1>
    <p class="outline">
        Outline: First Aid sections (USMLE Step 1) · three separate scores, each
        with a range
    </p>

    <p class="outline horizon-note">
        Each score is shown for <strong>today</strong> and projected forward
        with <strong>no further studying</strong> (+5 / +10 days) — the drop is
        the over-confidence signal.
    </p>

    <div class="scores">
        <!-- 1. Memory -->
        <section class="card">
            <h2>Memory</h2>
            {#if memory && memory.studiedCards > 0}
                <div class="horizons">
                    {#each horizonDefs as slot}
                        {@const h = memoryFor(slot.days)}
                        <div class="horizon">
                            <div class="horizon-label">{slot.label}</div>
                            <div class="big">{h ? pct(h.meanRecall) : "—"}</div>
                            {#if slot.days === 0}
                                <div class="sub">
                                    likely {pct(memory.ciLow)}–{pct(
                                        memory.ciHigh,
                                    )}
                                </div>
                            {/if}
                        </div>
                    {/each}
                </div>
                <div class="sub studied">
                    {memory.studiedCards} cards studied
                    {#if memGap > 0.05}
                        · <span class="drop">−{pct(memGap)} by +{laterDays}d</span>
                    {/if}
                </div>
            {:else}
                <div class="abstain">No data yet</div>
                <div class="sub">No cards studied yet.</div>
            {/if}
            <p class="hint">
                Chance you recall a fact now (FSRS), and how it decays with no
                review.
            </p>
        </section>

        <!-- 2. Performance -->
        <section class="card">
            <h2>Performance</h2>
            <div class="horizons">
                {#each horizonDefs as slot}
                    <div class="horizon">
                        <div class="horizon-label">{slot.label}</div>
                        <div class="big locked">—</div>
                    </div>
                {/each}
            </div>
            <div class="sub studied">Not measured yet</div>
            <p class="hint">
                Chance of answering a new, exam-style question right. Needs
                held-out questions (paraphrase test) — built Phase 3 (Sunday).
                Kept separate from Memory on purpose.
            </p>
        </section>

        <!-- 3. Readiness -->
        <section class="card readiness-card">
            <h2>
                Readiness <span class="tag">probability of passing</span>
            </h2>
            <p class="clarify">
                Chance of <strong>passing</strong> (score ≥ 60%) — not your
                predicted exam score.
            </p>
            <div class="horizons">
                {#each horizonDefs as slot}
                    {@const r = readinessFor(slot.days)}
                    <div class="horizon">
                        <div class="horizon-label">{slot.label}</div>
                        {#if dashboard.readinessAvailable && r}
                            <div class="big">{pct(r.pPass)}</div>
                            <div class="sub">
                                range {pct(r.rangeLow)}–{pct(r.rangeHigh)}
                            </div>
                            <div class="sub">
                                ≈{pct(r.meanProjectedRecall)} correct
                            </div>
                        {:else}
                            <div class="big locked">🔒</div>
                            <div class="sub">pending</div>
                        {/if}
                    </div>
                {/each}
            </div>
            {#if dashboard.readinessAvailable && gap > 0.1}
                <p class="warn">
                    ⚠ Over-confidence: your pass chance drops {pct(gap)} in {laterDays}
                    days without review — {pct(dashboard.fragileFraction)} of your
                    recallable cards are low-stability (crammed).
                </p>
            {:else if !dashboard.readinessAvailable}
                <ul class="missing">
                    {#each dashboard.readinessBlockedReasons as reason}
                        <li>{reason}</li>
                    {/each}
                </ul>
            {/if}
            <p class="hint">
                Pass probability calibrated to reported practice-exam outcomes
                (60% correct is the cut score, where pass/fail is most volatile).
                {dashboard.gradedReviews} graded reviews so far.
            </p>
        </section>
    </div>

    <!-- Coverage (supporting metric for the give-up rule, §7c) -->
    {#if coverage}
        <section class="coverage-bar">
            <div class="coverage-head">
                <span><strong>Coverage</strong> — {pct(coverage.fraction)} of the
                    outline seen</span>
                <span class="sub">{coverage.cardsSeen} / {coverage.totalCards}
                    outline cards</span>
            </div>
            <div class="bar"><div
                    class="fill"
                    style="width: {Math.round(coverage.fraction * 100)}%"
                ></div></div>
        </section>
    {/if}

    {#if dashboard.nextBestTopic}
        <p class="next-best">
            Study next: <strong>{short(dashboard.nextBestTopic)}</strong>
        </p>
    {/if}

    <!-- Coverage / mastery map -->
    {#if topics.length > 0}
        <h2 class="map-title">Coverage map</h2>
        <table class="map">
            <thead>
                <tr>
                    <th>Topic</th>
                    <th class="num">Cards</th>
                    <th class="num">Mastered</th>
                    <th class="num">Avg recall</th>
                </tr>
            </thead>
            <tbody>
                {#each topics as t}
                    <tr>
                        <td>{short(t.topic)}</td>
                        <td class="num">{t.totalCards}</td>
                        <td class="num">{t.cardsMastered}</td>
                        <td class="num">{pct(t.averageRecall)}</td>
                    </tr>
                {/each}
            </tbody>
        </table>
    {/if}
</div>

<style lang="scss">
    .readiness {
        max-width: 900px;
        margin: 0 auto;
        padding: 1.5rem;
        font-family: var(--font, sans-serif);
        color: var(--fg, #222);
    }
    h1 {
        margin: 0 0 0.25rem;
    }
    .outline {
        margin: 0 0 1.25rem;
        opacity: 0.7;
        font-size: 0.9rem;
    }
    .scores {
        display: grid;
        grid-template-columns: 1fr 1fr 1.4fr;
        gap: 1rem;
        align-items: stretch;
    }
    @media (max-width: 720px) {
        .scores {
            grid-template-columns: 1fr;
        }
    }
    .card {
        background: var(--canvas-elevated, #f7f7f7);
        border: 1px solid var(--border, #ddd);
        border-radius: 10px;
        padding: 1rem;
    }
    .card h2 {
        margin: 0 0 0.5rem;
        font-size: 1rem;
        opacity: 0.8;
    }
    .card h2 .tag {
        font-size: 0.7rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        opacity: 0.7;
        padding: 0.1rem 0.4rem;
        border: 1px solid var(--border, #ccc);
        border-radius: 999px;
        vertical-align: middle;
    }
    .clarify {
        margin: 0 0 0.6rem;
        font-size: 0.8rem;
        opacity: 0.8;
    }
    .big {
        font-size: 2.2rem;
        font-weight: 700;
        line-height: 1.1;
    }
    .horizon .big {
        font-size: 1.5rem;
    }
    .horizon-note {
        margin-top: -0.75rem;
        margin-bottom: 1rem;
    }
    .studied {
        margin-top: 0.6rem;
    }
    .sub {
        font-size: 0.85rem;
        opacity: 0.75;
        margin-top: 0.2rem;
    }
    .decay {
        margin-top: 0.5rem;
        font-size: 0.9rem;
    }
    .decay .drop {
        color: #c0392b;
        font-weight: 600;
    }
    .big.locked {
        opacity: 0.45;
        font-weight: 400;
    }
    .coverage-bar {
        margin-top: 1.25rem;
    }
    .coverage-head {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 1rem;
        font-size: 0.9rem;
        margin-bottom: 0.35rem;
    }
    .bar {
        height: 10px;
        border-radius: 6px;
        background: var(--border, #e4e4e4);
        overflow: hidden;
    }
    .fill {
        height: 100%;
        background: var(--accent, #4c8bf5);
        border-radius: 6px;
    }
    .hint {
        font-size: 0.75rem;
        opacity: 0.55;
        margin: 0.6rem 0 0;
    }
    .horizons {
        display: flex;
        gap: 0.75rem;
        flex-wrap: wrap;
        justify-content: space-between;
    }
    .horizon {
        min-width: 4.5rem;
    }
    .horizon-label {
        font-size: 0.8rem;
        opacity: 0.8;
        margin-bottom: 0.15rem;
    }
    .abstain {
        font-size: 1.3rem;
        font-weight: 600;
        opacity: 0.8;
    }
    .missing {
        margin: 0.5rem 0 0;
        padding-left: 1.1rem;
        font-size: 0.85rem;
    }
    .warn {
        margin: 0.8rem 0 0;
        padding: 0.5rem 0.7rem;
        border-radius: 8px;
        background: color-mix(in srgb, orange 18%, transparent);
        font-size: 0.85rem;
    }
    .next-best {
        margin: 1.25rem 0 0;
        font-size: 1rem;
    }
    .map-title {
        margin: 1.5rem 0 0.5rem;
        font-size: 1.1rem;
    }
    .map {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.9rem;
    }
    .map th,
    .map td {
        text-align: left;
        padding: 0.4rem 0.6rem;
        border-bottom: 1px solid var(--border, #eee);
    }
    .map .num {
        text-align: right;
        font-variant-numeric: tabular-nums;
    }
</style>
