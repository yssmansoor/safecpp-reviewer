## Verification results across two corpora

The verification loop re-runs clang-tidy on each LLM-proposed fix and checks
whether the original violation persists. Results from two test corpora:

| Corpus               | Files | Fixes attempted | Verified clean | Rate |
| -------------------- | ----- | --------------- | -------------- | ---- |
| Synthetic (hand-authored) | 7  | 104             | 55             | 53%  |
| Autoware `autoware_utils/math/` | 7 | 14         | 7              | 50%  |

The synthetic suite was authored to exercise rules covered by the YAML rule
store (C-style casts, magic numbers, NULL vs nullptr, missing override,
reinterpret_cast, unsafe string ops, owning raw pointers). The Autoware
slice is real production C++ — math utility headers chosen because they have
minimal ROS dependencies, avoiding the meta-diagnostic noise that dominates
ROS-heavy modules.

The two corpora landing within 3 points of each other suggests the tool
generalizes: it isn't memorizing fixes for fixtures it was tuned against.

### Why 50% and not higher

Three failure modes account for most unresolved fixes:

1. **Partial fixes** (~25% of unresolved). The 7B model returns only the
   changed lines instead of the complete chunk. The verifier's pre-flight
   size-ratio check rejects these before patching, but the LLM doesn't
   recover from this failure mode within the retry budget.
2. **Stale rule_id** (~15%). The fix changes the structure enough that
   clang-tidy now emits a *different* rule on the same line. The original
   rule_id is technically resolved, but the code is not "clean" — and the
   verifier correctly reports it as unresolved.
3. **Cascading meta-diagnostics**. A fix that touches one line sometimes
   causes adjacent unrelated checks to fire. Counted as regressions, not
   unresolved fixes.

A larger model (or a fine-tuned one) would primarily address #1.
