# How agents write (notes, the postdoc's analyses)

For every agent that writes text a scientist reads: the analysts' `notes.md`, the postdoc's `analysis.md`, replies about results. These rules come on top of `AGENTS.md` → Rules (no numbers of your own, no invented interpretations, uncertainty explicit), which they never loosen. Keep the notes short; these rules are about what the words claim, not about writing more.

1. **Observation, interpretation, speculation look different.** An observation says what the data show and where the number comes from (the measurement, the key). An interpretation or idea says how sure it is (high, medium, low, speculative) and what it leans on. Plot impressions are marked "(plot)".
2. **The verb matches the evidence.** One measurement "is consistent with", "suggests", "points to"; it does not "show", "prove" or "confirm". A closest reference is not an identification.
3. **No cause from a trend.** "The lattice parameter rises with the Pt content across S-1 to S-4" is an observation; "Pt expands the lattice" is an interpretation, with its certainty and the alternatives (strain, another phase, a different synthesis batch).
4. **No difference is not sameness.** Say "the same within σ" only when both values have a σ and the comparison comes from `labpipe dataset compare`. Without σ (EDS exports have none), small differences cannot be judged: say so instead of "similar" or "different".
5. **Every estimate with its uncertainty, and its kind.** σ from the fit, the spread of several measurements, or none recorded. When comparing samples, say how many measurements each value rests on.
6. **Zero, missing and below detection are different.** Use the state the results give: not measured, not analysed yet, failed, below detection (e.g. EDS values reported negative, taken as 0), not applicable. Never write 0 for missing, and never drop a value silently.
7. **Report what did not work.** Failed, out-of-date or contradicting measurements and null results belong in the text when they matter to the question, not only the ones that fit.
8. **Exploratory is labelled.** A pattern you noticed while looking (not asked about, not planned in `context/`) is exploratory: say so, and say what measurement would test it.
9. **Limitations are concrete.** "EDS is normalised to 100 % and has no σ, so the 2 at% Pd difference between S-3 and S-4 cannot be judged", not "the results may contain errors". Name the likely direction of an effect only when the results support it.
10. **Name the alternative.** When an interpretation has a credible alternative explanation, give it, and what would tell them apart.
11. **What is unknown stays unknown.** If the data cannot answer something, say so and list the missing analysis or measurement. Do not fill the gap with a plausible guess.
12. **One term per concept, units always.** Use the method page's terms (`docs/methods/<method>.md`), the unit with every number, and the precision as printed (no rounding, no extra digits). Define an abbreviation the first time.

Adapted from the `scientific-writing` skill of K-Dense-AI/claude-scientific-skills (MIT licence, commit 593377b: "Evidence-bound scientific writing principles"), reworded for labpipe's notes and analyses.
