# 0036. The fuzzy dictionary guesses a spelling only for terms of 7+ letters

Status: Accepted
Date: 2026-10-01

## Context

The fuzzy pass ([decision 0030](0030-cleanup-keeps-the-spoken-words.md), task A4 of p9a) put a dictionary term's spelling on a word that is the same word in another case or one letter off. It ran for every term of five letters or more and protected ordinary words only through `COMMON_WORDS`, a hand-written list of about 550 words. Five-letter first names are typical dictionary content, and ordinary words sit one letter from many of them. The review of the integration branch reproduced it: with `Alice` in the dictionary, "they look alike to me" became "they look Alice to me"; `Jones` turned `jokes` into `Jones`, `Laura` turned `lauda` into `Laura`, `Raven` turned `raved` into `Raven`. The pass runs after the cleanup, so the cleanup model cannot prevent it.

## Decision

- The case-only fix keeps working for every term of five letters or more (`alice` becomes `Alice`).
- The one-letter guess (substitution, insertion, deletion, same first letter, exactly one term) runs only for a term of **seven or more letters** (`FUZZY_NEAR_MIN_LEN` in `vox_core.py`, `Terms.FUZZY_NEAR_MIN_LEN`). Both apps and the golden `fuzzydict` rows change together.
- `COMMON_WORDS` stays: it still decides which words the case fix and the guess may touch.
- A short name that speech to text spells wrongly the same way every time (`Yuvraaj`) is a `wrong => right` line in the dictionary.

## Consequences

- Fewer corrections of real misspellings of short names, and no more real words rewritten into them. Long names and terms (`Kubernetes`, `Mohammed`) keep the fix. The cost of a wrong guess (a wrong word in the typed text, silently) is higher than the cost of a missed one (a name spelled slightly wrong, which the user sees and can add a line for).
- A real word one letter from a 7-letter term can still be changed if it is not on the stoplist; the risk is much smaller (fewer words are one edit from a long term) but not zero.

## Alternatives considered

- Check each candidate against a full English wordlist: the right fix in principle, but the apps would need to ship a wordlist (size, a copy for Python and for Java that must agree, language coverage). Not worth it for this pass.
- Raise the threshold to six letters: `Yuvraj`, `Robert`, `Hunter`, `Turner` and similar names still sit one letter from ordinary words (`hunted`, `turned`).
- Make the stoplist longer: it never ends, and a miss is silent.
- Switch the one-letter guess off completely: loses `Kubernetis` becoming `Kubernetes`, which is the case the pass was built for.
