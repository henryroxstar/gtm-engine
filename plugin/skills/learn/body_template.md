# Learn from new material

This skill ingests customer decks, whitepapers, call notes, or website copy, deterministically classifies claims against your existing profile knowledge, and allows you to update your profile with exact diffs and explicit approval.

## The procedure (execute in order)

**Step 1 — Ingest and prepare the document.**
If the user pasted raw text, save it as a Markdown file (e.g. `notes.md`) in the intake directory. If the user provided a file:
- Markdown (`.md`): use directly.
- Raw text or notes (`.txt`): save or rename as `.md` (e.g. `notes.md`) in the intake directory before calling `classify` to avoid `MaterialUnreadable` exceptions.
- PDF or Word document (`.pdf`, `.docx`): the intake CLI automatically converts it using `docling convert` if available. If `docling` is missing, ask the user to provide text or install `docling`.

<details>
<summary>Technical details: intake file location</summary>
Pasted text or converted files are written under content/&lt;profile&gt;/material-intake/&lt;slug&gt;/.
</details>

**Step 2 — Classify claims against live knowledge.**
Run deterministic classification:

```bash
uv run python -m gtm_core.material_intake classify --profile <active> --file <path-to-document> --stage
```

Read the four buckets from the classification summary:
1. **Already covered**: points your knowledge already makes.
2. **New, sourced**: new claims backed by numbers, citations, or verifiable proof.
3. **New, unsourced**: claims not found in your knowledge and without explicit source backing.
4. **Candidate cells**: buyer personas and triggers that could expand your hook matrix.

**Step 3 — Show the exact changes.**
For each topic with candidate updates, stage the candidate file:

```bash
uv run python -m gtm_core.knowledge_staging stage --profile <active> --topic <topic> --from <candidate-file>
```

Then inspect the diff against live profile knowledge:

```bash
uv run python -m gtm_core.knowledge_staging diff --profile <active> --topic <topic>
```

Always show the exact changes in plain English and get the user to approve each change before running promote. Translate raw diff markers into clear, founder-readable statements:
- *"Your case-study page would gain one story: …"*
- *"Your product page would change one number: 12% → 9%"*
- *"Your hook matrix would add a new angle for Platform Directors: …"*

**Step 4 — Ask for approval per change.**
You must obtain approval before updating any live profile knowledge. Ask the operator for each proposed topic using structured questions or chat:
- **Approve**: accept the changes and promote them to live profile knowledge.
- **Skip**: discard this candidate update.
- **Edit**: revise the wording before adopting.

In unattended runs or when running headless without an operator, take the default to skip (do not approve).

**Step 5 — Promote only approved topics.**
Only after you receive explicit confirmation to approve, promote the staged topic into live profile knowledge:

```bash
uv run python -m gtm_core.knowledge_staging promote --profile <active> --topic <topic>
```

Never promote any topic without explicit approval.

**Step 6 — Close the run.**
Report what was learned and updated:
<!-- operator -->
> "Updated your profile knowledge with N approved change(s). Skipped M change(s). Say 'undo my last change' to put any of it back."
<!-- /operator -->

<details>
<summary>Staging details and audit trail</summary>
Staged candidate files and the classification report were stored in content/&lt;profile&gt;/material-intake/.
Audit logs are appended to content/&lt;profile&gt;/history.jsonl.
</details>
