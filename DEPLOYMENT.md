# Deploying ICT Protege Automation 1.8.1

## Reload login fix

Version 1.8.1 sends Logout before closing a healthy session. If the controller retains
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

1. Back up Home Assistant, including the old custom component, before upgrading.
2. Extract the deployment ZIP. Copy its `custom_components/ict_automation` folder to
   `/config/custom_components/ict_automation`, replacing the existing component files.
   Do not copy tests or development dependencies into Home Assistant.
3. Restart Home Assistant. Confirm the integration reports version 1.8.1 and loads.
4. Match checksum settings at both ends. Default is 8 Bit Sum; None is available
   under Configure > Edit Connection Settings. Keep command/status/event ACK settings
   enabled as documented in README.md.
5. Review arming feature options, explicit trouble records, and affected automations.

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
