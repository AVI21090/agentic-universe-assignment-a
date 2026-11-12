# AI Usage Disclosure

## Tools used

ChatGPT.

## What I used them for

I used ChatGPT to help interpret Assignment A's edge cases, design the SQLite schema and PII tokenisation, draft the pipeline and tests, and prepare the run and submission documentation.

## What I wrote or decided myself

I selected Assignment A. The implementation was substantially drafted with ChatGPT assistance. Before the technical conversation, I will review the implementation and make sure I can explain and modify its design.

## Where the AI was wrong or unhelpful

An early design option considered during AI-assisted work was retaining raw phone numbers in event rows for linking. That would violate the PII-at-rest requirement, so the final design uses keyed HMAC tokens and stores only the last four digits for masked reporting.

## How I verified AI-generated code

On my Windows machine, I ran `python -m pytest -q` and all 23 tests passed. I also generated the seed-43 small dataset, ingested both drops sequentially, and generated all five reports.

With ChatGPT assistance, I additionally checked small datasets with seeds 42 and 43 using sequential and combined-drop ingestion; all five report files matched for each seed. A database inspection found no raw phone numbers or customer names in lead or event fields.