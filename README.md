# SNCS adaptive-routing audit

Code and de-identified annotation ledger accompanying **Quality and Resource
Tradeoffs in Adaptive Routing for LLM Mathematical Reasoning**, Mai Ye.
Version: 5 November 2026, human-audit manuscript v6. Manuscript submission and
acceptance are separate events; this repository does not claim publication.

## Contents and licences

- `code/`, `reproduce.py`, verification scripts and documentation: **MIT**.
- `annotations/`: 1,216 paired labels and 87 author adjudications,
  **GPL-3.0-only**. Licence scope is explained in `annotations/LICENSE`.
- `expected/`: reported aggregate statistics and the three added audit tables.
- `archive_payload_manifest.json`: fingerprints of the separate Online Resource 2.

The public ledger preserves exact task/answer hashes. It omits task statements,
model-answer texts, rater prose, identities and private return packets. Third-party
evidence is not relicensed by the author's MIT/GPL choices; see `NOTICE.md`.

## Verify the public labels without external data

    python verify_public_annotations.py

This uses Python's standard library, verifies the public manifest and reconstructs
the three strata's pre-adjudication agreement and author-adjudication totals.

## Full stored-output reconstruction

Obtain the accompanying `ESM_2.zip` (Online Resource 2) from the article's
supplementary materials when released by the journal. The manuscript's
supplementary archive is prepared separately; a public journal download URL
is not available at this stage.
Extract it into a separate directory. Install `requirements.txt` before working
offline, then run, with new output directories outside the extracted archive:

    python reproduce.py --input /path/to/ESM_2 --mode verify --output /path/to/new_verify
    python reproduce.py --input /path/to/ESM_2 --mode analysis --output /path/to/new_analysis
    python reproduce.py --input /path/to/ESM_2 --mode rescore --output /path/to/new_rescore

The archive contains copies of the same published reconstruction code. The wrapper
uses the supplied archive as the input root; missing archived outputs are not
replaced by new model calls. It blocks network connections. Dependencies and
recorded runtime versions are retained. Exact source hashes, rather than altered
answers or benchmark gold files, link the score versions.

The original evaluation contains 51,000 trajectories and the follow-up 6,400.
None of the four original primary comparisons passed the frozen rule; the audit
does not reverse that result. Follow-up MATH passes only after post-test audit
and remains near the margin. Fewer requests coexist with more recorded tokens.
The three unresolved materials concern one under-specified GSM8K problem.

Rater A's AI assistance with reasons and rater B's partly AI-assisted checking
are disclosed in annotation provenance. Agreement is not a guarantee of independent
correctness. Follow-up benchmark reuse and service changes limit generalization.

Repository: https://github.com/Matey-ace/sncs-adaptive-routing-audit
