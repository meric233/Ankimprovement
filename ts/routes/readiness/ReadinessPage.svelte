<!--
Copyright: Ankitects Pty Ltd and contributors
License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
-->
<script lang="ts">
    import type { StudyDashboardResponse, TopicMastery } from "@generated/anki/stats_pb";
    import { bridgeCommand, bridgeCommandsAvailable } from "@tslib/bridgecommand";

    export let dashboard: StudyDashboardResponse;
    export let prefix: string;

    const pct = (x: number): string => `${Math.round(x * 100)}%`;

    $: memory = dashboard.memory;
    $: coverage = dashboard.coverage;
    $: performance = dashboard.performance;
    $: topics = [...dashboard.topics].sort(
        (a, b) => a.averageRecall - b.averageRecall,
    );

    // Buttons only work in the desktop app (they call back into Qt); hide on
    // platforms without the bridge (e.g. AnkiDroid) so nothing throws.
    const canAct = bridgeCommandsAvailable();

    const topicCoverage = (t: TopicMastery): number =>
        t.totalCards > 0 ? t.cardsSeen / t.totalCards : 1;

    // The least-covered topic = lowest cards_seen / total_cards; on a tie, the
    // one with more cards (bigger opportunity).
    $: leastCovered = dashboard.topics
        .filter((t) => t.totalCards > 0)
        .reduce<TopicMastery | null>((min, t) => {
            if (!min) return t;
            const dt = topicCoverage(t) - topicCoverage(min);
            if (dt < 0) return t;
            if (dt === 0 && t.totalCards > min.totalCards) return t;
            return min;
        }, null);

    function startReview(): void {
        bridgeCommand("review");
    }
    function learnLeastCovered(): void {
        if (leastCovered) {
            bridgeCommand(`studyTopic:${leastCovered.topic}`);
        }
    }

    // All three cards project forward with no study; the first is "today", the
    // rest assume no reviewing in between.
    const horizonDefs = [
        { days: 0, label: "Today" },
        { days: 5, label: "+5 days" },
        { days: 10, label: "+10 days" },
    ];
    const memoryFor = (days: number) =>
        memory?.horizons.find((h) => h.days === days);
    const performanceFor = (days: number) =>
        performance?.horizons.find((h) => h.days === days);
    const readinessFor = (days: number) =>
        dashboard.readiness.find((r) => r.horizonDays === days);

    // Memory-decay over-confidence signal: how far current recall falls over the
    // horizons with no review.
    $: memHorizons = memory?.horizons ?? [];
    $: laterDays = memHorizons.length > 1 ? memHorizons[memHorizons.length - 1].days : 10;
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
        Outline: First Aid sections (USMLE Step 1) · three separate scores
    </p>

    <p class="outline horizon-note">
        Each score is shown for <strong>today</strong> and projected forward with
        <strong>no further studying</strong> (+5 / +10 days) — the drop is the
        over-confidence signal.
    </p>

    {#if canAct}
        <div class="actions">
            <button class="action review" on:click={startReview}>
                ▶ Review
            </button>
            <button
                class="action learn"
                on:click={learnLeastCovered}
                disabled={!leastCovered}
                title={leastCovered
                    ? `Study ${short(leastCovered.topic)} (${pct(
                          topicCoverage(leastCovered),
                      )} covered)`
                    : "No topic data yet"}
            >
                📚 Learn least-covered topic
                {#if leastCovered}
                    <span class="sub-inline"
                        >{short(leastCovered.topic)} · {pct(
                            topicCoverage(leastCovered),
                        )} covered</span
                    >
                {/if}
            </button>
        </div>
    {/if}

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
            {#if performance && performance.available}
                {#if performance.degraded}
                    <div class="degraded-badge">⚠ Estimate — AI off</div>
                {/if}
                <div class="horizons">
                    {#each horizonDefs as slot}
                        {@const h = performanceFor(slot.days)}
                        <div class="horizon">
                            <div class="horizon-label">{slot.label}</div>
                            <div class="big">{h ? pct(h.mean) : "—"}</div>
                            {#if h && !performance.degraded}
                                <div class="sub">
                                    {pct(h.rangeLow)}–{pct(h.rangeHigh)}
                                </div>
                            {/if}
                        </div>
                    {/each}
                </div>
                <div class="sub studied">
                    {#if performance.degraded}
                        0.9 × Memory (no per-card AI signal)
                    {:else}
                        {performance.scoredCards} / {performance.totalCards} cards
                        scored
                    {/if}
                </div>
                {#if performance.degraded && performance.degradedNote}
                    <p class="caveat inline degraded-note">
                        ⚠ {performance.degradedNote}
                    </p>
                {/if}
            {:else}
                <div class="abstain">🔒 Pending</div>
                <div class="sub studied">
                    {performance
                        ? `${performance.scoredCards} / ${performance.totalCards} cards scored`
                        : "Not measured yet"}
                </div>
                {#if performance}
                    <ul class="missing">
                        {#each performance.blockedReasons as reason}
                            <li>{reason}</li>
                        {/each}
                    </ul>
                {/if}
            {/if}
            {#if !(performance && performance.degraded)}
                <p class="hint">
                    Blend of <strong>0.75 × Memory + 0.25 × per-card rephrasing
                        score</strong>. The range comes from varying the weights
                    between (0.85, 0.15) and (0.65, 0.35).
                </p>
                <p class="caveat inline">
                    ⚠ These weights are <strong>arbitrary</strong>. They put a
                    <em>qualitative</em> learning-science idea (durable memory
                    drives most transfer, but performance on reworded questions
                    matters too) into a <em>quantitative</em> form so we can show a
                    number — they are <strong>not</strong> fitted. Honest weights
                    need real held-out testing data we don't have yet.
                </p>
            {/if}
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
            {#if dashboard.readinessAvailable}
                <div class="horizons">
                    {#each horizonDefs as slot}
                        {@const r = readinessFor(slot.days)}
                        <div class="horizon">
                            <div class="horizon-label">{slot.label}</div>
                            <div class="big">{r ? pct(r.pPass) : "—"}</div>
                            {#if r}
                                <div class="sub">
                                    ≈{pct(r.meanProjectedRecall)} correct
                                </div>
                            {/if}
                        </div>
                    {/each}
                </div>
                {#if performance && performance.degraded}
                    <p class="caveat inline degraded-note">
                        ⚠ Based on a compromised Performance estimate (AI off) —
                        less accurate. Enable AI rephrasing for a true reading.
                    </p>
                {/if}
                {#if memGap > 0.1}
                    <p class="warn">
                        ⚠ Over-confidence: pass chance drops {pct(memGap)} in {laterDays}
                        days without review — {pct(dashboard.fragileFraction)} of
                        your recallable cards are low-stability (crammed).
                    </p>
                {/if}
            {:else}
                <div class="big locked">🔒</div>
                <div class="sub">pending</div>
                <ul class="missing">
                    {#each dashboard.readinessBlockedReasons as reason}
                        <li>{reason}</li>
                    {/each}
                </ul>
            {/if}
            <p class="hint">
                Pass probability (no range — it is already a probability) =
                Performance × coverage, mapped to reported practice-exam outcomes
                (60% correct is the cut score). {dashboard.gradedReviews} graded reviews
                so far.
            </p>
        </section>
    </div>

    <p class="caveat">
        Note: the 0.75 / 0.25 Performance weights (and the ± band) are
        <strong>arbitrary</strong> — they express a qualitative learning-science
        mechanism in quantitative form, and are not fitted. Accurate weights can
        only come from real held-out testing data, which we do not yet have.
    </p>

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
                    <th class="num">Coverage</th>
                    <th class="num">Mastered</th>
                    <th class="num">Avg recall</th>
                </tr>
            </thead>
            <tbody>
                {#each topics as t}
                    <tr class:least={leastCovered && t.topic === leastCovered.topic}>
                        <td>{short(t.topic)}</td>
                        <td class="num">{t.totalCards}</td>
                        <td class="num">{pct(topicCoverage(t))}</td>
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
    .actions {
        display: flex;
        gap: 0.6rem;
        flex-wrap: wrap;
        margin: 0 0 1.25rem;
    }
    .action {
        display: inline-flex;
        align-items: baseline;
        gap: 0.5rem;
        padding: 0.55rem 0.9rem;
        font-size: 0.95rem;
        font-weight: 600;
        color: #fff;
        background: var(--accent, #4c8bf5);
        border: none;
        border-radius: 8px;
        cursor: pointer;
    }
    .action.learn {
        background: #2e9e6b;
    }
    .action:hover {
        filter: brightness(1.07);
    }
    .action:disabled {
        opacity: 0.5;
        cursor: default;
    }
    .action .sub-inline {
        font-size: 0.75rem;
        font-weight: 400;
        opacity: 0.9;
    }
    tr.least td {
        background: color-mix(in srgb, #2e9e6b 12%, transparent);
    }
    .studied {
        margin-top: 0.6rem;
    }
    .sub {
        font-size: 0.85rem;
        opacity: 0.75;
        margin-top: 0.2rem;
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
    .caveat.inline {
        margin: 0.5rem 0 0;
        font-size: 0.72rem;
        line-height: 1.35;
        font-style: normal;
        opacity: 0.75;
    }
    .degraded-badge {
        display: inline-block;
        margin-bottom: 0.5rem;
        padding: 0.15rem 0.5rem;
        font-size: 0.72rem;
        font-weight: 700;
        border-radius: 999px;
        color: #8a5a00;
        background: color-mix(in srgb, orange 22%, transparent);
    }
    .caveat.inline.degraded-note {
        color: #8a5a00;
        opacity: 0.95;
    }
    .caveat {
        margin: 0.9rem 0 0;
        font-size: 0.78rem;
        font-style: italic;
        opacity: 0.65;
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
