# SDD ledger — plan: /Users/leomeng/Ohme/v2_repos/log_viewer/docs/superpowers/plans/2026-08-04-log-viewer.md

Repo: /Users/leomeng/Ohme/v2_repos/log_viewer (git init'd this session; commits explicitly authorised by owner for this repo only)
Branch: feat/log-viewer
Merge base: 0421495 (docs: spec + plan)

Task 1: complete (commits 0421495..b9d62d9, review clean) — scaffold + log_line.py parser, 10/10 tests
Task 1: minor (deferred): LogLine.raw holds ANSI/EOL-stripped text, not literal bytes — plan-mandated; a future save/replay feature must not assume round-trip
Task 1: minor (resolved): pyproject entry point references ohme_log_viewer.__main__:main which Task 8 creates — confirmed against plan, no action
Task 2: complete (commits b9d62d9..3c4ec0e, review clean) — is_noise() in log_line.py, 15/15 tests
Task 2: minor (deferred): test import of is_noise sits mid-file, not at module head (PEP 8) — plan-mandated by the brief's own test block; batch-fix candidate for final review
Task 3: complete (commits 3c4ec0e..32d2c60, review clean) — log_buffer.py LogBuffer, 24/24 tests, no findings
Task 4: review found 1 Important (plan-mandated): read_line() loop unbounded on newline-less stream — blocks Disconnect, defeats Task 5 cycle timeout, grows buffer
Task 4: RULING (human, asked): fix governs over plan. Plan amended — read_line takes a deadline on entry; new test test_read_line_gives_up_on_a_stream_that_never_completes_a_line; suite 38 -> 39, downstream 46 -> 47
Task 4: minor (deferred): list_usb_ports() has no test coverage (gap inherited from plan)
Task 4: minor (deferred): open() also catches ValueError, untested path
Task 4: minor (deferred): rstrip("\r") strips all trailing CRs, not exactly one delimiter — benign for this fixed protocol
Task 4: fix round 1/5 (1 addressed, 0 open; commits 3195870..02f076c) — deadline taken once on entry, checked after newline search, buffered lines not starved
Task 4: complete (commits 32d2c60..02f076c, review clean after 1 fix round) — serial_link.py, 39/39 tests
CONTROLLER NOTE: owner granted standing authorisation mid-session — proceed with recommended options, do not ask again this session. Applies to future plan-mandated findings: take the recommended fix rather than escalating.
Task 5: review found 1 Important (plan-mandated): duplicate identical except SerialLinkError / except OSError bodies in LogReader.run()
Task 5: RULING (standing authorisation): take recommended fix. Plan amended to a single `except (SerialLinkError, OSError)` clause.
Task 5: minor (deferred): no test covers write_line raising -> error signal (structurally correct by trace, untested)
Task 5: minor (deferred): LogReader.run/stop/_sleep_interruptibly have no direct test coverage; verified by code reading + Task 9 smoke test only
Task 5: minor (deferred): test_read_failure_propagates uses a raw OSError the production SerialLink would never surface
Task 5: fix round 1/5 (1 addressed, 0 open; commits 31a8484..db551aa) — single combined clause, OSError retained, control flow unchanged
Task 5: complete (commits 3f855e8..db551aa, review clean after 1 fix round) — reader.py, 47/47 tests
Task 6: complete (commits df0fc6a..79912f7, review clean) — ui/log_pane.py LogPane, 47/47 pytest unchanged (no tests by design), headless script prints "log pane OK"
Task 6: WARN resolved: anti-yank scroll not exercised by headless script — already covered by Task 9 smoke-test item 10 (scroll up stops autoscroll, return to bottom resumes). No action.
Task 6: minor (deferred): render() clears+scrolls even for an empty iterable — harmless given its always-scroll contract
Task 7: review spec-FAILED, 1 Important (plan-mandated): sync() overwrites check state on every call, contradicting brief wording "leaves check state alone otherwise"; reviewer demonstrated a live revert
Task 7: ADJUDICATED (standing authorisation). Reviewer's demo drove the panel with NO buffer wired, so `enabled` was stale. Verified in the real integration: MainWindow._on_tag_toggled writes the buffer as its FIRST statement, and Qt signals are synchronous in-thread, so the flush timer cannot interleave -> `enabled` is never stale. Spec (design doc L151) makes LogBuffer the single source of truth with views as projections; the reviewer's proposed fix (init check state only on row creation) would create a SECOND source of truth for filter state and prevent resync. RULING: code is correct, the brief's WORDING was wrong. Plan + docstring corrected to state mirror semantics; Task 7 verification gains mirror assertions (both directions, must not emit); Task 8 verification gains a "toggle survives a flush" assertion. Finding converted to tested behaviour rather than dismissed.
Task 7: fix round 1/5 (1 addressed, 0 open; commits 49d0ca9..2da12be) — docstring only, logic byte-identical; re-reviewer independently re-ran and confirmed both mirror directions pass
Task 7: CONTROLLER VERIFIED adjudication independently: TagPanel wired to a real LogBuffer, checkbox clicked, 3 flushes -> no revert. Ruling confirmed by evidence.
Task 7: complete (commits 79912f7..2da12be, review clean after 1 fix round) — ui/tag_panel.py, 47/47 pytest unchanged
Task 8: review spec-FAILED, 2 Important (both plan-mandated):
  (a) main_window.py "No serial port selected" used showMessage(), bypassing _report_error — guaranteed erased by the next 100ms flush, i.e. functionally invisible
  (b) _disconnect() ignored reader.wait(5000)'s return value and called deleteLater() regardless — destroying a QThread Qt still considers running is fatal
Task 8: RULING (standing authorisation): take recommended fixes, both real. Plan amended — (a) routes through _report_error; (b) parks a non-stopping reader in self._orphaned_readers and reports, never deletes it. Added _READER_STOP_TIMEOUT_MS constant and a verification assertion for the no-port path.
Task 8: minor (deferred): implementer self-review missed finding (a) despite quoting the rule it broke
Task 8: minor (deferred): pyproject declares [tool.black]/[tool.ruff] config but dev extras install neither, so no lint pass is possible in the venv
Task 8: fix round 1 implementer reported BLOCKED — correctly. My new no-port assertion assumed zero serial ports; this machine enumerates /dev/cu.usbmodem2101 (MCU VIRTUAL COM DEMO, 1FC9:0094 = NXP i.MX RT), so _connect took the real-open path. My assertion bug, not the implementer's. Fixed: clear the combo first + assert no reader started. Deterministic regardless of environment.
NOTE FOR TASK 9: real hardware appears attached to this machine. BUT extract_logs is DESTRUCTIVE on the device — firmware deletes archived log files and clears the ring buffer after extraction. Do NOT run the on-device smoke test autonomously; hand it to the owner with that side effect stated.
Task 8: fix round 1/5 (2 addressed, 0 open; commits 2633c64..6027469) — only showMessage left is inside _update_status; null-then-stop ordering preserved; park path safe from closeEvent
Task 8: complete (commits 26a27bc..6027469, review clean after 1 fix round) — ui/main_window.py + __main__.py, 47/47 pytest, headless "main window OK"
Task 8: minor (deferred, STRONG candidate for final fix wave — ~1 line): a parked orphaned reader's line_received/error/finished stay connected, so if it later unblocks it would feed the buffer while the UI reads "Disconnected". Pre-existing, not new breakage. Fix: disconnect its signals before parking.
Task 8: minor (deferred): reader.wait(_READER_STOP_TIMEOUT_MS) blocks the GUI thread up to 5s on a stuck reader. Pre-existing.
Task 9: plan AMENDED — smoke test is owner-run, not implementer-run (extract_logs destroys device logs); README gains a prominent warning that connecting consumes the device's logs.
Task 9: complete (commits 288a074..0e34451, review clean) — README.md, 47/47 tests. Smoke test NOT run: handed over as a 13-point checklist in task-9-report.md (owner must supply a device whose logs may be cleared).
ALL 9 TASKS COMPLETE. Proceeding to final whole-branch review.

=== FINAL WHOLE-BRANCH REVIEW (opus) — "Ready to merge: With fixes" ===
Verified by direct headless measurement: buffer/pane parity at 50k, _pending contract, unparsed round-trip, retroactive filtering, clear-preserves-filter, sync mirror adjudication. All hold.
IMPORTANT #1 (PLAN DEFECT #5): log_pane _AT_BOTTOM_SLACK=4 commented "in pixels" — QPlainTextEdit's scrollbar is in LINES. A 3-line mouse-wheel notch sits inside the dead zone, so anti-yank is defeated for the commonest scroll gesture. Measured.
IMPORTANT #2: orphaned-reader leak is worse than the 1-line signal fix. self._link is created once and REUSED: _disconnect closes it while the orphan is mid-read, and a later Connect reopens the SAME object, so the orphan reads the new session's stream and sends its own extract_logs — doubling a DESTRUCTIVE command. Demonstrated live.
IMPORTANT #3: All/None emits one signal per tag, each triggering a full pane render. Measured 3.94s / 4.27s frozen GUI at 50k lines x 30 tags. Integration seam invisible to per-task reviews.
Triage of 11 deferred: #1 MUST FIX; #2,#3,#6,#7,#10 FIX LATER; #4,#5,#8,#9,#11 NOT A PROBLEM.
Reviewer disproved the plan's premise that Qt thread glue needs hardware — drove a real LogReader headlessly in ~20 lines. That glue currently has ZERO executed verification since the smoke test is unrun.
DISPATCHING ONE FIX WAVE (per skill: one fixer, not one per finding).

=== FIX-WAVE RE-REVIEW (opus) — All 10 addressed. Ready to merge: YES ===
Re-reviewer independently re-ran: both linters, all 3 plan headless scripts extracted verbatim from the committed plan, the new test module, verify-orphan-isolation.py (13/13), and a fresh auto-scroll check against the live widget.
Deviation judgements: finding 4 relabel ("shown" -> "in view") SOUND — a subtracted error counter has no eviction hook and could go negative; ruff UP/RUF fixes SAFE — all four UP035 moves are annotation-only under `from __future__ import annotations`, verified no runtime type use.
PARKED (residual Minor, no second fix wave per skill): an [error] notice appended within the 100ms window after a tag toggle is wiped by the deferred render, while _last_error keeps it in the status bar. RULING: park. The underlying behaviour (render() rebuilds from the buffer, which holds no error lines, so any toggle erases error notices) is PRE-EXISTING and unchanged; only the ordering window is new. Requires an error inside a 100ms window of a toggle. Symptom identical to the pre-existing case. Not load-bearing.
PARKED (residual Minor): ruff `select` unpinned, so a ruff upgrade may surface new lint churn. Not a correctness risk.
BRANCH STATUS: proven as far as possible without hardware. The 16-item on-device smoke test is UNRUN — colour coding, real port enumeration, (unparsed) contents, unplug recovery, reconnect (item 15, the only device check of the per-connection SerialLink), and the long run past the 50k cap all remain unverified.
