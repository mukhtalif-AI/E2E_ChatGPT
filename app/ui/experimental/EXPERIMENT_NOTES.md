# HubGPT Experimental Mode

This is a separate full-site UI copy, opened at `http://127.0.0.1:8781/experimental`.

## Current experiment: isolated workspace and visual refresh

- Warmer, higher-contrast surface colors with a readable light default and a working dark theme.
- Clearer title hierarchy, spacing, cards, form controls, buttons, and focus indicators.
- Responsive horizontal navigation on small screens, skip-to-content link, and reduced-motion support.
- Persistent mode banner with a return link and a note describing the experiment.

The Experimental UI is served from `app/ui/experimental/index.html`; stable mode remains at `/`. Shared server changes are limited to dispatching Experimental requests and routing their files and jobs to the isolated workspace. Job ordering, model selection, and media-generation functions stay the same.

Experimental outputs go under `D:\HubGPT`: scripts and uploads in `Library`, per-run files in `Series/<series>/Episodes/<episode>/` (`00_Input`, `01_Script`, `02_Narration`, `03_Artwork`, and `Output`), and job logs and history in `Activity`. Existing configured output folders remain in use by the regular HubGPT page. Voice model and training files remain shared; Experimental narration audio goes into each episode workspace.

The Activity log view wraps long lines, keeps a bounded display history, requests smaller log chunks, and avoids redrawing an unchanged job list on every status poll. These changes are intended to stop long logs from expanding the page and stalling its repaint.

## Review / promotion

Compare the same pages and settings in regular and Experimental mode. Keep this copy experimental until the user explicitly asks to promote the design.


## Shared HubGPT updates (15 Oct 2026)

The shared genre planner now avoids reusing names when a new script stage is requested. Blank-name AI stories are
renamed to `Genre - generated title` once the story title is available; later job paths and outputs are updated with
the final name. Existing-episode jobs without a script stage retain their selected episode. Genre video jobs expose
the episode `Output` folder as their first result. The stable UI's Open result action may request creation of that
allowed output directory before opening it in Explorer.

Hindi genre scene prompts now keep a compact character anchor while varying viewpoint and can request a targeted
image correction after review. They do not require a rear-view boy or silhouette in every scene. The narration cadence
changes live in shared `app/story.py` and the HubGPT voice copy; the experimental UI is still a separate visual copy.
No live-browser verification has been done for these changes; restart the hub while idle before reviewing them.
