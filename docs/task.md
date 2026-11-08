# Technical - Sanctions

## Context

Kesko Senukai works with ~2,000 suppliers across LT/LV/EE and onboards new ones regularly. We must make sure we never transact with sanctioned entities and that we flag elevated-risk counterparties - and we have to be able to re-check the whole base every quarter.

Today this is manual and slow.

## Your task

Build a working POC of a **Supplier Sanctions & Risk Screening Agent**. Given a supplier (name, country, and optionally a registration number), it screens them against public sanctions/watchlists and returns an auditable decision a human can act on.

Use whatever LLM/tools you like - we build with Claude - and any public data. Favour something runnable and pragmatic over something complete.

## Requirements

1. **Use real, authoritative public data.** Find and use actual open sources (e.g. the EU consolidated sanctions list, OFAC SDN, OpenSanctions, local lists). Don't fabricate data.
2. **Return structured, validated output** per supplier: `status` (`clear` / `review` / `likely_hit`), matched entity (name, list, programme), match confidence, evidence (source + link/snippet), and recommended action. Use structured-output/function calling, validate the schema, and handle the case where the model returns invalid or extra fields.
3. **Handle messy matching:** name variants, transliteration, partial/fuzzy matches, very common names. Explain your matching approach and how you set thresholds.
4. **Evaluate it with tests.** Show how you'd run this repeatably (promptfoo or your own harness), and tell us your target numbers and why.

## Deliverables

- A working POC (repo / script / notebook) you can run or screen-share.
- A short writeup (a few slides) covering: architecture; LLM; data sources; security model; eval set + results; how you'd ship it on Azure; rough cost of infrastructure; how you'd prove value before scaling.
- Come ready to present and then make some changes live with us.
