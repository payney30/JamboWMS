# Print Output Consistency Fixes

Full cumulative state. No new migration this round.

## The real bug, found from your actual PDFs

Your uploaded PDFs caught something code review alone would have
missed: the previous round's "hide the drawer-group sections from
print" fix on Dispatcher Console had NOT actually worked, despite
reading correctly in the source.

Root cause, found twice in the same spot:
1. The print-hiding CSS rule had been written using JavaScript-style
   "//" line comments inside a style block - not valid CSS syntax
   (CSS only has block comments) - which silently broke the stylesheet
   parser and meant the actual hiding rule never took effect.
2. Converting to a real CSS comment should have fixed it, except that
   comment's own explanatory text happened to describe the comment
   syntax by literally writing out the comment-close sequence as an
   example - which closed the comment early and turned the rest of the
   explanation into garbage CSS, breaking the parser a second time.

Fixed properly this time, and actually verified with a headless-browser
print-media render (not just a read-through) that the target elements
compute to display:none under print media before calling it done.

## Print output made consistent between LOC triage and Dispatcher Console

Comparing your two actual PDFs side by side surfaced real gaps:

- Dispatcher Console's print output never included requester
  name/email/phone or POC info at all (LOC triage's did). Added the
  same always-visible requester/POC header block to Dispatcher
  Console, matching LOC triage's exact pattern - so it's consistent
  live on-screen too, not just on paper.
- LOC triage's print output was missing "Assigned worker."
- Dispatcher Console's print output was missing "Note to requestor."

Both fixed - both screens' print output now shows the same field set.

## PDF filename, per your request

- LOC triage's saved PDF now titles as "NJ LOC - Detailed WO#[number]"
- Dispatcher Console's as "NJ Dispatcher - Detailed WO#[number]"
  (parallel convention, screen-specific text so it's still clear which
  screen a saved PDF came from)

## How to apply

    cd JamboWMS/phase1
    git apply /path/to/CHANGES.diff
    # (only add new files below if you haven't already from a prior round)
    #   alembic/versions/b7f3d1a9c2e4_add_locking_and_note_to_requester.py
    #   alembic/versions/c8e2f4a1b6d3_add_app_settings_table.py
    #   alembic/versions/d3f8a2c1e5b7_widen_priority_check_constraint.py
    #   alembic/versions/e7c4b9d2a1f6_add_geo_pin_drop.py
    #   alembic/versions/f4a8d1c6e3b2_convert_priority_data_narrow_constraint.py
    #   alembic/versions/a1b2c3d4e5f6_add_task_worker_role_and_assignment.py
    #   alembic/versions/b3c5d7e9f1a2_add_tasking_event_type.py
    #   tests/test_enhancement_phase1.py
    #   tests/test_enhancement_phase4.py
    #   tests/test_enhancement_phase5.py
    #   tests/test_enhancement_phase12.py
    #   tests/test_enhancement_phase15.py
    #   tests/test_enhancement_phase20.py
    #   tests/test_enhancement_phase21.py
    alembic upgrade head

No new migration this round.

## Verify after deploying

This is the important one - please actually print/save a PDF from both
screens this time (not just check on-screen) and confirm:

1. Dispatcher Console's PDF: no Status/Note/Reassign/Task-to-worker
   text boxes or buttons anywhere - just the clean summary, Notes, and
   History.
2. Dispatcher Console's PDF now shows requester name/email/phone and
   POC (if applicable) - both on screen and in the printed output.
3. LOC triage's PDF now shows "Assigned worker."
4. Dispatcher Console's PDF now shows "Note to requestor" when one
   exists.
5. Save-as-PDF from LOC triage suggests filename "NJ LOC - Detailed
   WO#[number]"; from Dispatcher Console, "NJ Dispatcher - Detailed
   WO#[number]".

## Test status

**292 passing, 0 failing** - unchanged (these were frontend/CSS/print
fixes; automated tests can't easily catch print-CSS regressions like
this one, which is exactly why I verified this round with an actual
headless-browser print-media render rather than trusting the source
read-through).
