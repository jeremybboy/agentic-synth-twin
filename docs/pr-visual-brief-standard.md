# PR visual-brief standard

Every implementation PR must make its boundary inspectable without reading the full diff. Add one concise technical brief and one synchronized visual summary. Use verified repository and PR facts only; label proposals, automated checks, and human acceptance separately.

## Required technical brief

Use this order:

1. PR title, repository, starting branch or commit, and verified status.
2. Goal and user-visible workflow.
3. Transparency/change ledger:
   - current state;
   - user-visible change;
   - code and data layers touched;
   - explicitly untouched or out of scope;
   - persistence and undo impact;
   - audio and realtime impact;
   - C2PA and provenance impact;
   - automated evidence required;
   - human acceptance still required.
4. Implementation phases when sequencing helps review.
5. Non-goals and stop conditions.
6. Automated checks and manual acceptance steps.
7. Explicit instruction not to merge automatically.

## Required visual summary

Use a wide, readable diagram with four to eight numbered panels. Show concrete actions and state transitions rather than decorative icons. Always include the bottom row `CURRENT STATE -> CHANGES IN THIS PR -> UNCHANGED / OUT OF SCOPE -> END RESULT`, plus a compact layer-impact strip when architecture or provenance boundaries matter.

Use a dark neutral base, flat semantic color, compact typography, and no gradients, shadows, glossy surfaces, fake product chrome, or unsupported success marks. Keep the image and technical brief synchronized after scope changes. Store the final asset in `docs/assets/` and link it from both the brief and PR description.

## Review rule

Automated checks establish implementation evidence only. Listening quality, browser behavior, MIDI hardware behavior, and any other perceptual or device-dependent claim remain manual acceptance gates. Open the PR ready for owner review, provide the direct link, and never merge or enable auto-merge on the owner's behalf.
