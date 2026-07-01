// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
import { studyDashboard } from "@generated/backend";

import type { PageLoad } from "./$types";

// Default outline = AnKing First Aid sections (maps to the USMLE Step 1 outline).
export const FIRST_AID_PREFIX = "#AK_Step1_v11::#FirstAid::";

export const load = (async () => {
    const dashboard = await studyDashboard({
        tagPrefix: FIRST_AID_PREFIX,
        topicDepth: 1,
        readinessHorizonsDays: [0, 5, 10],
    });
    return { dashboard, prefix: FIRST_AID_PREFIX };
}) satisfies PageLoad;
