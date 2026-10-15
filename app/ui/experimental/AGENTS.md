# Experimental UI mode rules

- `/experimental` loads this isolated full-site copy. The stable site is `app/ui/index.html`.
- When the user asks to change the website while working in Experimental mode, implement presentation or interaction changes in this directory first. Preserve the stable UI copy unless the user explicitly requests promotion.
- Keep generation order, model selection, media settings, output paths, and job semantics unchanged for visual-only requests. Do not alter `app/tools.py`, voice engines, or the stable page to achieve a redesign.
- If a requested feature truly needs server behavior, isolate it under an experimental module and keep any shared server change to a narrow route dispatch. Record that boundary in `EXPERIMENT_NOTES.md`.
- Before recommending promotion, show the Experimental result for review and keep the stable UI intact until the user asks to promote it.
