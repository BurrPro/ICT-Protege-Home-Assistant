# Deploying ICT Protege Automation 1.9.2

## Intermittent Toggle action error fix

Version 1.9.2 distinguishes the timed Unlock from the status and session-restoration
requests that follow it. Once ICT acknowledges the timed Unlock, Home Assistant
treats the button press as successful. If a later feedback or monitoring request is
rejected, the integration logs the exact operation and reconnects when required;
it does not send a second pulse.

Toggle, normal door and output controls now use the already authenticated service
session directly. This removes the repeated logout/login/monitoring cycle that was
performed around each button press. Area controls still temporarily authenticate
the user PIN entered in Home Assistant.

If ICT rejects the timed Unlock itself, Home Assistant still reports the failure and
now identifies it as `Timed unlock door <ID>: Invalid command`. That message means
the controller rejected the actual door action and should be investigated in ICT.

## Toggle pulse feedback fix

Version 1.9.1 prevents transient lock-output feedback from disabling the Toggle
button after a successful pulse. The two-second duplicate-press guard remains.
Replace the component and restart Home Assistant to load the fix.

## Toggle door type

After updating and restarting Home Assistant, use Configure > Configure Door Types
> select Door 1 > Toggle (timed pulse). Keep ICT's Lock Activation Time at 1 second
and the reed configured as that ICT Door's contact. A Toggle button replaces the
lock control while the existing Contact sensor remains. Update any dashboard or
automation that used this door's old lock entity to use the new button.

Confirm a single press produces one timed pulse and the contact follows the reed.
There is no automatic pulse retry or inferred opening/closing direction. Other
normal lock doors and the prior reload/login fix remain supported.

## Reload login fix

The 1.8.1 reload fix sends Logout before closing a healthy session. If the controller retains
a previous login, startup performs one Logout and fresh Login, requiring acceptance
of the configured PIN. Persistent session errors retry setup rather than asking for
a new PIN. Replace the component and restart Home Assistant to clear the failed
setup and reload the new code; the existing service PIN can remain unchanged.

## Upgrade changes

- Arm Away now performs normal arming. The old mapping sent Disarm 24 Hour.
- Disarm now disarms only the normal area. The old mapping disarmed both portions.
- Arm Home/Stay now performs Stay arming, and feedback distinguishes Home from Away.
- Replace automations using `alarm_arm_vacation` for force arming with
  `alarm_arm_custom_bypass`, and enable Force Arm in Configure Arming Modes.
- Night and Trigger are no longer advertised. Remove or revise automations that
  call these unsupported actions; they will not be silently mapped to other commands.
- The upgrade retains existing entity IDs while migrating internal identifiers to
  include the configuration entry. Devices previously shared across controller
  entries are separated. Previously suppressed duplicate entities on a second
  controller may appear for the first time.
- Automatically generated physical-input "Trouble" sensors are removed unless the
  trouble record is explicitly present in the independent `troubles` configuration.
  Add the actual ICT trouble record IDs through Configure > Add Trouble Input or
  Scan Trouble Inputs, and review automations using the old inferred sensors.
- The service PIN is now checked at startup. Previously ignored invalid credentials
  or disabled command acknowledgements will surface as setup errors.

## Installation

### Method 1: HACS (Recommended)
1.  Open **HACS** in Home Assistant.
2.  Go to **Integrations** > **Custom repositories** (top right menu).
3.  Paste this repository URL and select **Integration**.
4.  Click **Download**.
5.  **Restart Home Assistant**.

### Method 2: Manual
1.  Download the repository as a ZIP file.
2.  Extract the `custom_components/ict_automation` folder.
3.  Copy this folder into your Home Assistant's `/config/custom_components/` directory.
4.  Restart Home Assistant.


## Acceptance check on the actual installation

The automated tests use simulated controller responses; perform this check at a
suitable time for the site before relying on the updated alarm controls.

- Observe the area's 24-hour state in both ICT and the `tamper_24h_state` attribute.
- Arm Away: confirm normal arming, exit-delay feedback, then Away state.
- Disarm: confirm the normal area disarms and 24-hour monitoring remains armed.
- Arm Home: confirm Stay arming and Home feedback.
- Where authorised, try a rejected PIN: confirm a Home Assistant error and no control
  action. The simulator tests cover this without affecting a real user's lockout.
- Confirm a physical input and an independently configured trouble input update.
- Change input bypass in ICT and confirm Home Assistant follows that state.
- Interrupt and restore the network connection: confirm entities become unavailable,
  reconnect automatically and recover fresh feedback.

Do not use the old integration as an arming workaround: its command mappings can
disarm 24-hour monitoring. If deployment fails, disable the integration and use the
controller's normal interface while investigating.
