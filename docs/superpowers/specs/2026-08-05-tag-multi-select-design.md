# Tag panel: flip a whole selection in one click

**Date:** 2026-08-05
**Status:** approved

## Problem

Hiding several tags costs one click per tag. With thirty tags in the panel —
routine on a busy device — narrowing the view to the two or three tags a
diagnosis needs is a dozen clicks or more. `All` and `None` help only at the
extremes.

## Behaviour

The tag list becomes multi-select — shift+click for a run, ctrl/cmd+click to add
or drop one, drag to rubber-band — and a check state applied to any row of the
selection applies to the whole selection.

- **Toggling the checkbox of a row that is part of the selection** sets every
  selected row to that row's new state. Ten tags hidden in one click.
- **Space** does the same, taking its direction from the current row's new
  state.
- **A row that is not in the selection toggles alone**, exactly as today. The new
  behaviour fires only when the click lands inside the user's own selection, so
  the existing single-tag gesture is untouched.
- **The selection survives the click**, making it a working set that can be
  flipped off and back on rather than something the first click consumes.
- `All` and `None` are unchanged, and no widget is added. The list carries a
  tooltip naming the gesture, because nothing on screen advertises it.

Rejected alternatives: a pair of *Hide selected* / *Show selected* buttons
(discoverable, but two more widgets in a 200 px panel), and a *solo* gesture that
isolates one tag (fast for one tag, no help for hiding five of thirty).
Discoverability is the accepted cost of the chosen option.

## Qt behaviour this depends on

Established by probe rather than assumption, because the implementation turns on
it (`QListWidget`, `ExtendedSelection`, PySide6 6.x):

| Gesture | Finding |
|---------|---------|
| Click on a check indicator | `itemChanged` fires while the multi-row selection is **still intact**; Qt collapses the selection to the clicked row *after* the slot returns |
| Space | `itemChanged` fires for the current row, selection intact, and the selection **survives** |
| Re-selecting rows afterwards | Emits no `itemChanged`, so restoring a selection cannot disturb the filter |

The first finding is what keeps this small: the handler reads `selectedItems()`
directly. No mouse-event override, no custom delegate. The third is what makes
restoring the selection safe.

## Design

All of it lives in `ui/tag_panel.py`.

`_on_item_changed` becomes: take the changed row's new state; if that row is in
the current selection, apply the state to the other selected rows and emit
`tag_toggled` for each row that actually changed. Rows already in the target
state emit nothing, so the buffer sees no redundant writes. Propagation guards
re-entrancy — each `setCheckState` re-enters `itemChanged` — so a row cannot emit
twice.

The selection is restored after Qt's collapse, which happens once the slot has
returned; a zero-delay timer is the hook for that.

`ui/main_window.py` and `log_buffer.py` are unchanged. One `tag_toggled` per
changed tag is already the shape `set_all` (All/None) produces, and
`_on_tag_toggled` already defers to a single re-render at the next 100 ms flush —
that deferral was measured at 3.9 s of freeze when done inline across 30 tags.
The batching problem this feature would otherwise create is therefore already
solved upstream of it.

## Boundaries

Unchanged: the panel stays a projection of `LogBuffer`'s filter state, emitting
one `tag_toggled` per change and holding no filter state of its own. Selection is
view state and belongs to the panel alone — the buffer neither knows nor cares
which rows are selected.

## Risks

- **The gesture is invisible.** Accepted; the tooltip is the mitigation.
- **`sync()` runs every 100 ms while streaming**, rewriting every row's check
  state with signals suppressed. The probe indicates it does not disturb
  selection, but a regression there would make the panel unusable during a live
  session, so it gets a test rather than trust.

## Testing

New `tests/test_tag_panel.py`, headless on Qt's offscreen platform, driven by
real `QTest` clicks and keystrokes — the same approach `tests/test_log_reader.py`
takes to the reader thread, and for the same reason: the behaviour worth testing
is Qt's, so stubbing Qt would test nothing.

- unchecking one row of a three-row selection emits `tag_toggled(tag, False)` for
  all three
- checking one row of a partly-unchecked selection enables all of them
- a row outside the selection toggles alone
- Space propagates across the selection
- rows already in the target state emit nothing
- the selection survives a propagated click
- the selection survives a `sync()` flush
- `sync()` still emits nothing
