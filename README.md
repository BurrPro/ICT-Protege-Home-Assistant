# ICT Protege Automation for Home Assistant

Version 1.9.1. A local TCP integration for the ICT WX/GX Automation and Control service.
Requires Home Assistant 2024.11 or later.

## Controller service settings

| Setting | Value |
| --- | --- |
| IP Port | 21000 (or the port configured in Home Assistant) |
| Encryption | None |
| Checksum | 8 Bit Sum (default), or None; both ends must match |
| Numbers are Big Endian | Off (little endian) |
| Allow Status Requests When Not Logged In | On |
| User Logon Lock Out Timer If Incorrect PIN Is Supplied | Off, matching the supplied service configuration |
| Ack Commands | On |
| Expect Ack For Status Monitoring | On |
| Resend Status Monitoring If No Ack After 5 Attempts | Off |
| Expect Ack For Events | On |
| Resend Events If No Ack After 5 Attempts | Off |

The integration validates incoming checksums, acknowledges unsolicited data, and
matches requested status replies to the requested record type and ID. Events are
acknowledged if received; this release does not create Home Assistant event entities
or enable an event subscription.

## Install or upgrade

See [DEPLOYMENT.md](DEPLOYMENT.md) for upgrade changes and a short acceptance check.

1. Back up the existing integration folder and Home Assistant configuration.
2. Copy `custom_components/ict_automation` into Home Assistant's `custom_components`
   directory, replacing the previous integration files, or update through HACS after
   this version has been published to your repository.
3. Restart Home Assistant. New installations use **Settings > Devices & Services >
   Add Integration > ICT Protege Automation**.
4. Enter the controller host, port, service PIN and checksum selection. Existing
   entries without a checksum setting use **8 Bit Sum**.
5. Use **Configure** to add or scan records and choose arming features. Connection
   settings can be changed under **Edit Connection Settings**.

Use a service PIN with permission to monitor the configured records. Controls use
that PIN for doors, outputs and input bypass; area controls require the PIN entered
in the alarm panel. PINs must contain 1-6 ASCII digits. Invalid codes are rejected,
never silently truncated or cleaned up.

The service connection logs in for monitoring. Each control transaction confirms
logout, the requested user's login and the control ACK, reads status, then restores
the service login and subscriptions. An ACK confirms acceptance for processing;
entity feedback always comes from controller status. Rejected commands raise a Home
Assistant action error. A timeout disconnects the session and is never treated as
success or automatically retried as a control command.

## Entities and area controls

- Doors: lock and **latched unlock**, plus a contact sensor. Latched unlock retains
  the previous version's behaviour and does not automatically relock.
- Areas: Away = normal arm; Home/Stay = stay arm; Disarm = normal area disarm only.
  These actions do not issue the separate 24-hour disarm commands.
- Optional Force Arm uses Home Assistant's **Custom Bypass** action. Enable it under
  **Configure Arming Modes**. Force-armed feedback remains Away or Home according to
  the controller's actual armed/partial state, with a `force_armed` attribute.
- Night, Vacation and Trigger are not exposed. The previous mappings were incorrect;
  ICT Instant arming is not assumed to mean Home/Night arming.
- Area feedback distinguishes Away, Home, exit delay (`arming`), entry delay
  (`pending`), disarm delay and triggered. Waiting-for-input/code conditions report
  unknown alarm state with a `status_text` reason instead of falsely claiming armed.
- Area attributes include `tamper_24h_state`, `force_armed`, `instant_armed`,
  `stay_armed`, `area_state` and `status_text`. No 24-hour disarm control is exposed.
- Outputs: on/off status and controls.
- Physical inputs: status sensor and controller-confirmed bypass selector.
- Trouble inputs: independently configured status sensors. A trouble record's ID
  is not inferred from a physical input ID.

Entities begin unknown/unavailable until their first valid status. Connection loss
clears cached status, and records without a fresh status for 180 seconds become
unavailable (checked on connection/heartbeat updates). Heartbeats check the service
and a periodic status sweep refreshes records. The first 250 configured records are
subscribed for push updates; additional records use polling. A full sweep can take
longer on large or slow installations.

## Record configuration and scanning

Use the controller's database record/reporting IDs. If `ACPUseDisplayOrder = true`
is explicitly configured on the controller, use its display-order references.
Trouble inputs have their own **Add / Edit / Remove / Scan Trouble Inputs** options.
**Scan All** scans doors, areas, physical inputs and outputs; troubles are scanned
separately so they are not confused with physical inputs.

Scans check every ID up to the selected limit, including beyond gaps. Only a matching
status response confirms existence; an invalid-index NACK skips that record. A
connection/authentication failure stops the scan instead of inventing records.
Large sparse scans take longer than earlier versions, which stopped after five misses.

The raw editor accepts mappings of positive record IDs to names:

```yaml
doors:
  1: Front Door
areas:
  1: Main Area
inputs:
  12: Front Contact
outputs:
  3: External Light
troubles:
  7: Controller Trouble
```

## Development validation

```text
python -m pip install -r requirements-dev.txt
python -B -m unittest discover -s tests -v
```

Tests use a deterministic controller transport simulator and small Home Assistant
API doubles for offline platform, flow and registry tests. They do not represent a
live controller test or a complete Home Assistant runtime test. No PINs or raw
login packets are logged by the integration.


## Garage doors and other toggle controls

For a garage door using an ICT Door record's lock output, choose **Toggle (timed
pulse)** when adding the door, or use **Configure > Configure Door Types**, select
an existing door, and change its type.

For the supplied garage setup, select Door 1 and Toggle. Keep ICT's Lock Activation
Time at 1 second. The integration sends the standard timed Unlock command (group
1, command 1); ICT handles the relay and its duration. It does not send latched
Unlock, a Lock command, or a separate relay-on/relay-off sequence.

A Toggle door exposes a **Toggle button** plus the existing **Contact sensor** on the
same device. Each press requests one pulse, independent of whether the reed is open
or closed. The garage opener decides whether that pulse opens, closes or stops the
door. A single reed reports closed versus not closed; it does not establish travel
direction or fully-open position, so no directional Open/Close/Stop commands or
position estimates are exposed.

Concurrent presses, presses within two seconds of completing the previous request,
are rejected. Failed commands are not automatically repeated. Fresh door status is
required to send a pulse. A transient unlocked status caused by the activation pulse
does not block later presses. This guard is not a door-travel timer.

Changing a door to Toggle replaces its lock entity with a new button entity; update
dashboards and automations for that door to call `button.press`. Its contact entity
ID and device remain the same. Other doors keep normal lock controls by default.
Any separately configured output switch for the same relay is independent: remove
it from the integration if it is no longer needed for direct control.

The raw editor also supports the optional map:

```yaml
doors:
  1: Garage
door_types:
  1: toggle
```

Values are `lock` or `toggle`. Existing doors without a mode use `lock`.
