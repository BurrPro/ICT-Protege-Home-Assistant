# Changelog

## 1.9.2 - 2026-09-06

- Add GitHub Actions coverage for Python 3.12 through 3.14 and automatic versioned
  releases from the `Release` branch.
- Treat an acknowledged control as successful even if its immediate status refresh
  or service-session restoration subsequently fails.
- Send door and output controls through the existing authenticated service session,
  avoiding unnecessary logout/login and monitoring rebuilds for each action.
- Identify the exact rejected operation in controller NACK errors, including timed
  door Unlock, status, login and monitoring requests.
- Re-subscribe monitoring after a user command without redundantly refreshing every
  configured record; the normal safety poll retains periodic refresh coverage.
- Disconnect and reconnect automatically when restoring the service session fails,
  while never retrying a control command whose outcome may already be known.

## 1.9.1 - 2026-09-06

- Do not block a Toggle door when cached lock feedback still shows the timed output
  active after its pulse has ended.
- Retain the two-second duplicate-press guard and fresh-status availability check.

## 1.9.0 - 2026-09-06

- Add per-door Normal lock / Toggle (timed pulse) configuration for new and existing doors.
- Toggle buttons use ICT timed Unlock and the controller-defined Lock Activation Time.
- Preserve door contact feedback and normal controls for unconverted doors.
- Reject concurrent/repeated presses, stale feedback and activation while the lock output is active.
- Include door types in raw configuration and clean up replaced/removed controls.

## 1.8.1 - 2026-09-06

- Log out before closing healthy sessions during reload, unload and temporary validation.
- Recover a retained login with one confirmed logout/login sequence; never accept an already-logged-in NACK as PIN verification.
- Treat persistent session errors as retryable setup failures instead of invalid credentials.
- Avoid redundant options writes when finishing configuration changes.
- Add regression tests for retained sessions, reload, rejected PINs and bounded recovery.

## 1.8.0 - 2026-09-06

- Correct normal disarm, Away, Stay and Force Arm command mappings so normal area
  controls do not send 24-hour disarm commands.
- Decode actual area mode, delay states, failure reasons and separate 24-hour state;
  use Home Assistant's modern alarm state API.
- Expose optional Force Arm as Custom Bypass; remove unsupported Night, Vacation
  and Trigger actions.
- Serialize all service requests and require confirmed login/command responses;
  correlate status replies by type and ID, surface NACKs, and disconnect on timeout
  without retrying control commands.
- Restore service login and monitoring after user controls; reconnect and resubscribe
  after socket loss without cancelling the supervisor.
- Replay initial cached status, unsubscribe removed entities, isolate callback errors
  and report unavailable for disconnected or stale records.
- Correct trouble status decoding and configure trouble inputs independently.
- Drive bypass selection from actual status instead of optimistic command feedback.
- Scan through ID gaps, reject unrelated responses, fix swapped Scan All limits, and
  validate setup/PIN/port/record inputs.
- Scope registry identifiers per configuration entry and migrate existing entity IDs.
- Retain configurable None/8 Bit Sum framing, with 8 Bit Sum the default; distinguish
  requested status replies from unsolicited data acknowledgements.
- Add offline protocol, lifecycle, platform, flow and registry regression tests,
  deployment guidance and a Home Assistant 2024.11 minimum version declaration.
