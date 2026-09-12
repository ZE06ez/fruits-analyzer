# P1B Hardware Acceptance Checklist

更新时间：2026-09-08

本文档是 P1B 阶段真实硬件、预览链路、科学采集数据和完整采集放行的正式验收表。它用于现场联调和最终 release gate，不是普通 TODO list。

事实优先级固定为：当前真实代码 > 当前测试 > 最新文档 > 历史描述。软件功能已实现、unit test 通过、fake adapter 通过或 simulation 通过，都不能自动代表真实硬件验收 PASS。没有真实硬件证据的项目默认 `NOT TESTED`。

## Status Definitions

| Status | Meaning |
| --- | --- |
| `NOT TESTED` | 尚未用真实设备或指定测试流程执行。默认状态。 |
| `IN PROGRESS` | 正在现场测试，证据尚未完整或仍有待确认项。 |
| `PASS` | 已按本文档步骤执行，满足 PASS 标准，并记录证据。 |
| `FAIL` | 已执行但未满足 PASS 标准，必须记录失败原因和后续动作。 |
| `BLOCKED` | 当前缺少硬件、adapter、环境或前置验收，无法执行。 |
| `N/A` | 经项目负责人确认本硬件版本不适用，必须写明原因。 |

## Overall Acceptance Status

| Acceptance Domain | Status | Evidence | Blocking |
| --- | --- | --- | --- |
| RGB Camera | `NOT TESTED` |  |  |
| DVP2 Multispectral Camera | `NOT TESTED` |  |  |
| STM32 Communication | `NOT TESTED` |  |  |
| Fan | `NOT TESTED` |  |  |
| LED3 | `NOT TESTED` |  |  |
| Two-channel Tungsten | `NOT TESTED` | PB7/PB8 software control added after 2026-09-11 bench confirmation. | Requires现场确认 SSR wiring, 12V cutoff, thermal safety, single-channel auto-off, all-off, and dual-channel policy. |
| Door / Actuator | `NOT TESTED` |  |  |
| Filter Wheel | `NOT TESTED` |  |  |
| Dark Reference | `NOT TESTED` |  |  |
| White Reference | `NOT TESTED` |  |  |
| Dark / White Compatibility | `NOT TESTED` |  |  |
| Multi-band Capture | `NOT TESTED` |  |  |
| Sample Stage | `BLOCKED` | P1B-7.5B software boundary implemented; repository evidence still shows `SAMPLE_STAGE_PROTOCOL_UNKNOWN`. | Real sample stage controller/protocol/HOME/position feedback contract is unknown. |
| Multi-view Capture | `BLOCKED` |  | Real sample stage remains blocked by `SAMPLE_STAGE_PROTOCOL_UNKNOWN`; only software/simulated acceptance may be run before real controller/protocol evidence exists. |
| Scientific Data Integrity | `NOT TESTED` |  |  |
| Metadata Integrity | `NOT TESTED` |  |  |
| Safety | `NOT TESTED` |  |  |
| True Capture Workflow | `NOT TESTED` | P1B-8 software entry implemented for readiness-gated `/api/capture/start`. | Production release remains blocked until all required domains pass; multi-view remains blocked by SampleStage protocol unknown. |

## Test Environment

Fill this section before running any acceptance item.

### Computer

| Field | Recorded Value |
| --- | --- |
| OS |  |
| Python version |  |
| OpenCV version |  |
| Application commit SHA |  |
| Git branch | `module-pro1` |
| Test date |  |
| Tester |  |
| Test location |  |
| Browser |  |
| Notes |  |

### RGB Camera

Current development target: `3840x2160`, `25 FPS`, `MJPG`. Record the actual returned values; do not copy the target values as evidence.

| Field | Recorded Value |
| --- | --- |
| FriendlyName |  |
| Device index |  |
| StableId |  |
| VID |  |
| PID |  |
| Reliable serial |  |
| Requested resolution |  |
| Actual resolution |  |
| Requested FPS |  |
| Actual FPS |  |
| FOURCC |  |
| Transport |  |
| Evidence |  |

### DVP2 Camera

Known target identity from current project context: DO3THINK/度申 MGV231M-H2, `UserID=GP23400004963`, SDK serial `DSGP23400004963`, MAC `B4-61-D3-14-6E-18`, IP `169.254.25.110`. Record what the SDK actually reports during the test.

| Field | Recorded Value |
| --- | --- |
| Model |  |
| FriendlyName |  |
| UserID |  |
| Serial |  |
| MAC |  |
| IP |  |
| SDK path |  |
| DLL path |  |
| PixelFormat |  |
| Width |  |
| Height |  |
| Evidence |  |

### STM32

| Field | Recorded Value |
| --- | --- |
| COM port |  |
| Baudrate | `115200 8N1` |
| Firmware profile |  |
| Protocol |  |
| Firmware/version INFO |  |
| PPR |  |
| Filter wheel slots |  |
| Handshake result |  |
| STATUS revision |  |
| Evidence |  |

## Common Acceptance Record Template

Copy this block under each executed AC item.

```text
Status: NOT TESTED / IN PROGRESS / PASS / FAIL / BLOCKED / N/A
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
  - screenshot:
  - log:
  - generated PNG:
  - metadata:
  - console output:
Failure Reason:
Follow-up:
```

## AC-RGB — RGB Camera Acceptance

Overall status: `NOT TESTED`

### AC-RGB-01 Device Discovery

Steps:

1. Start the main application.
2. Open Device Preparation or Camera Settings.
3. Click RGB camera rediscovery/probe.
4. Inspect the discovered RGB camera.
5. Confirm the bound device is the intended physical target camera.

PASS criteria:

- A real RGB device is found.
- The software does not silently fallback to `device_index=0`.
- The current binding matches the target camera.
- Status correctly distinguishes `detected`, `available`, `opened`, and `streaming`.

Record:

| Field | Value |
| --- | --- |
| device index |  |
| FriendlyName |  |
| stableId |  |
| VID/PID |  |
| detected / available / opened / streaming |  |
| result |  |

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-RGB-02 Preview Start / Stop / Restart

Steps:

1. Start RGB Preview.
2. Stop RGB Preview.
3. Start RGB Preview again.
4. Stop RGB Preview again.
5. Repeat at least 3 complete cycles.

PASS criteria:

- Each start succeeds.
- Each stop succeeds.
- Camera handle is released after stop.
- Restart can still acquire images.
- No duplicate preview worker remains.
- No background browser requests remain after stop.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-RGB-03 Parameter Apply

Parameters to test:

- Exposure
- Gain
- White Balance
- Auto Exposure
- Auto White Balance

PASS criteria:

- Supported parameters are actually applied.
- UI readback matches the actual camera state.
- Unsupported parameters are clearly reported as unsupported.
- The system does not report fake accepted states.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-RGB-04 Preview Latency

Steps:

1. Open RGB Preview.
2. Move a hand quickly left/right in front of the camera, or show a clearly changing clock/video.
3. Observe for at least 30 seconds.
4. Record diagnostics from the UI.

Suggested engineering targets:

- `sourceAgeMs` should ideally stay under 150 ms.
- Perceived end-to-end latency should ideally stay under 250 ms.

PASS criteria:

- The live preview does not continuously fall farther behind real motion.
- Diagnostics remain visible and credible.
- PASS is based on measured data, not hidden or modified diagnostics.

Required measurements:

| Metric | Value |
| --- | --- |
| sourceAgeMs avg / p95 |  |
| captureDurationMs avg / p95 |  |
| resizeDurationMs avg / p95 |  |
| jpegEncodeDurationMs avg / p95 |  |
| serverDurationMs avg / p95 |  |
| browserFetchDurationMs avg / p95 |  |
| measuredFps |  |
| droppedFrames |  |
| perceived latency |  |

If `serverDurationMs` is low but `sourceAgeMs` remains high, record possible bottleneck as DirectShow / UVC / camera driver buffering.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-RGB-05 Scientific PNG Capture

Steps:

1. Execute one formal RGB frame capture through the protected coordinator path.
2. Inspect output file and metadata.
3. Confirm preview JPEG/cache did not enter the formal dataset.

PASS criteria:

- Output file suffix is `.png`.
- No `.jpg` or `.jpeg` file is used as formal capture output.
- Formal resolution is correct.
- Frame is RGB `uint8`.
- The saved file can be reopened.
- Metadata points to the correct PNG path.
- Preview JPEG is absent from formal dataset metadata.

Record:

| Field | Value |
| --- | --- |
| filename |  |
| resolution |  |
| dtype |  |
| filesize |  |
| metadata path |  |

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-DVP2 — Multispectral Camera Acceptance

Overall status: `NOT TESTED`

Before testing: BasedCam3 must be fully closed.

### AC-DVP2-01 Device Detection

PASS criteria:

- DVP2 SDK enumerates the target device.
- The enumerated identity matches the intended camera.
- The software does not infer camera presence from ordinary network link state.

Record:

| Field | Value |
| --- | --- |
| model |  |
| UserID |  |
| serial |  |
| MAC |  |
| IP |  |
| SDK |  |
| DLL |  |

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-DVP2-02 Open / Stream

Steps:

1. Detect DVP2.
2. Start preview.
3. Stop preview.
4. Start preview again.
5. Stop preview again.
6. Repeat at least 3 complete cycles.

PASS criteria:

- The camera opens successfully.
- The camera streams frames continuously.
- Handle is released after stop.
- Restart succeeds.
- No abnormal occupation occurs when BasedCam3 is not running.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-DVP2-03 Frame Diagnostics

Steps:

1. Open DVP2 preview.
2. Observe diagnostics for at least 30 seconds.
3. Record frame and timing metrics.

PASS criteria:

- `frameId` keeps changing.
- `sourceAgeMs` does not continuously accumulate.
- Preview does not get farther and farther behind.
- Dropped frames are allowed when they keep latency low.

Required measurements:

| Metric | Value |
| --- | --- |
| frameId range |  |
| sourceTimestamp |  |
| sourceAgeMs avg / p95 |  |
| captureDurationMs avg / p95 |  |
| resizeDurationMs avg / p95 |  |
| jpegEncodeDurationMs avg / p95 |  |
| serverDurationMs avg / p95 |  |
| browserFetchDurationMs avg / p95 |  |
| measuredFps |  |
| droppedFrames |  |

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-DVP2-04 Exposure / Gain

Steps:

1. Change exposure.
2. Change gain.
3. Observe image response.
4. Confirm requested and actual values.

PASS criteria:

- DVP2 SDK accepts supported values.
- The actual image changes in a reasonable way.
- Values can be read back.
- Requested / actual are recorded correctly.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-DVP2-05 Raw Scientific PNG

Steps:

1. Execute one formal multispectral single-frame capture.
2. Inspect output image and metadata.

PASS criteria:

- Output is PNG.
- Image is single-channel.
- Original dtype is preserved.
- Current `Mono8` remains `uint8`.
- Future `uint16` must not be converted to `uint8`.
- No JPEG path is used.
- Metadata is correct.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-STM32 — STM32 Communication Acceptance

Overall status: `NOT TESTED`

### AC-STM32-01 Serial Connection

PASS criteria:

- Correct COM port is selected.
- Serial configuration is `115200 8N1`.
- Open succeeds.
- Disconnect and reconnect both succeed.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-STM32-02 Handshake

Steps:

1. Execute PING / INFO / STATUS using the current real firmware protocol.
2. Record raw or parsed output.

PASS criteria:

- ACK correlation is correct.
- INFO parses correctly.
- STATUS parses correctly.
- Firmware profile is validated.
- CRC is correct.

Record:

| Field | Value |
| --- | --- |
| firmware |  |
| PPR |  |
| status revision |  |
| timestamp |  |
| raw frame/log |  |

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-STM32-03 Fresh STATUS

Steps:

1. Before a mechanical command, record `STATUS revision = N`.
2. Execute the command.
3. Require a post-command STATUS with `revision > N`.

PASS criteria:

- Old STATUS cache is not used to judge motion success.
- Timeout or missing fresh STATUS is reported as failure or blocked, not success.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-STM32-04 Disconnect / Timeout

Steps:

1. Deliberately disconnect STM32 or create a timeout.
2. Observe backend and UI behavior.
3. Reconnect and recover.

PASS criteria:

- Application does not crash.
- Software does not report fake success.
- UI shows the error.
- Reconnection is possible.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-FAN — Fan Acceptance

Overall status: `NOT TESTED`

Steps:

1. Set Fan to 100%.
2. Observe physical fan behavior.
3. Set Fan to 0%.
4. Repeat at least 3 times.

PASS criteria:

- Fan physically starts.
- Fan physically stops.
- UI status matches hardware state.
- Command failure is not displayed as success.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-LED — LED3 Acceptance

Overall status: `NOT TESTED`

Steps:

1. Send LED mask `0x04`.
2. Observe LED3 ON.
3. Send LED mask `0x00`.
4. Observe LED3 OFF.
5. Repeat at least 3 times.

PASS criteria:

- Actual LED3 turns on and off correctly.
- UI status and fresh STATUS readback match the observed output.
- Command failure is not displayed as success.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-TUNGSTEN — Two-Channel Tungsten Acceptance

Overall status: `NOT TESTED`

Known software mapping:

- PB7 / original LED1 / `led_mask` bit0 `0x01` = Tungsten 1 SSR.
- PB8 / original LED2 / `led_mask` bit1 `0x02` = Tungsten 2 SSR.
- PB9 / LED3 / `led_mask` bit2 `0x04` remains LED3.
- All channels use current firmware `LED_SET=0x12`; there is no accepted separate tungsten command.

Steps:

1. Confirm 12V light power can be physically cut immediately and safety cover/shielding is closed.
2. Connect STM32 from the main application; do not run `manual_stm32_test.py` concurrently.
3. Turn fan on and confirm fresh STATUS reports fan duty > 0.
4. Use the Web tungsten panel to turn Tungsten 1 on for 5 seconds with operator safety confirmation.
5. Confirm PB7/SSR/Tungsten 1 turns on, PB8 and PB9 do not change unexpectedly, and fresh STATUS reports `led1_duty=100`.
6. Confirm backend auto-off turns Tungsten 1 off and fresh STATUS reports `led1_duty=0`.
7. Repeat steps 4-6 for Tungsten 2 / PB8 / `led2_duty`.
8. With LED3 on, execute normal tungsten all-off and confirm LED3 remains on while both tungsten channels are off.
9. Execute Emergency Stop and confirm fresh STATUS reports `led_mask=0x00` for all light outputs.
10. Attempt a dual-tungsten request while `allowDualTungsten=false` and confirm it is rejected without sending a dual-on command.

PASS criteria:

- Web UI and fresh STATUS match observed PB7/PB8/PB9 outputs.
- Manual tungsten on is limited to 1-5 seconds and backend auto-off works if the browser is closed.
- Closing commands are never blocked by ordinary interlocks.
- Failure to confirm off shows the physical 12V cutoff warning.
- Dual tungsten remains blocked until power capacity, cold inrush, cooling, and protective interlocks are accepted.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-DOOR — Door / Actuator Acceptance

Overall status: `NOT TESTED`

Steps:

1. Extend actuator.
2. Wait for configured duration.
3. STOP actuator.
4. Retract actuator.
5. Wait for configured duration.
6. STOP actuator.
7. Issue a new command before the previous timer would fire and verify old timer isolation.

PASS criteria:

- Physical actuator direction is correct.
- STOP physically takes effect.
- Backend timer sends STOP correctly.
- A new command is not accidentally stopped by an old timer.
- No endpoint position is marked confirmed unless real endpoint feedback exists.

Mandatory note: current `DOOR_SET` ACK only means command accepted. Without endpoint feedback, do not mark fully extended or fully retracted as confirmed.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-WHEEL — Filter Wheel Acceptance

Overall status: `NOT TESTED`

### AC-WHEEL-01 Manual Movement

Steps:

1. Use low speed.
2. Move `+1 slot`.
3. Require fresh STATUS.
4. Move `-1 slot`.
5. Require fresh STATUS.

Expected movement is approximately `+22.5°` and `-22.5°` for the current 16-slot mapping.

PASS criteria:

- Physical direction is correct.
- Movement angle is reasonable.
- STATUS position / target update.
- Fresh STATUS verification succeeds.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-WHEEL-02 `motion_not_started`

Steps:

1. Create or reproduce a condition where ACK is OK but fresh STATUS position and target do not change as expected.
2. Observe backend result.

PASS criteria:

- System returns FAIL / `motion_not_started`.
- System sends STOP.
- System does not blindly retry `MOVE_REL`.
- UI does not show movement success.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-WHEEL-03 SET_ORIGIN

Steps:

1. Operator manually aligns filter slot 1 with the optical axis.
2. Click SET_ORIGIN.
3. Query fresh STATUS.

PASS criteria:

- Logical position is reset to zero.
- Documentation and UI keep the correct meaning: SET_ORIGIN is not automatic HOME sensor.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-WHEEL-04 Repeated Slots

Steps:

1. Execute `+1`, `+1`, `+1`, `-1`, `-1`, `-1`.
2. Observe physical and reported position.

PASS criteria:

- Final position returns near the starting position.
- No obvious accumulated offset.
- Software direction and physical mechanical direction match.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

### AC-WHEEL-05 Emergency Stop

Steps:

1. Start filter wheel motion.
2. Execute STOP or emergency safe stop.

PASS criteria:

- Filter wheel stops.
- UI/backend record STOP result.
- Failure to stop remains visible and blocks further acceptance.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-CAL-DARK — Dark Reference Acceptance

Overall status: `NOT TESTED`

Use real DVP2 + real filter wheel. Capture lights must be off.

Flow:

```text
Band 1 -> wheel position -> camera settings -> settle -> raw PNG
Band 2 -> wheel position -> camera settings -> settle -> raw PNG
...
```

PASS criteria:

- Every enabled band has a PNG.
- No JPEG is used.
- `bandId` is correct.
- `wavelength` is correct.
- Exposure/gain are correct.
- Dark metadata is correct.
- `calibrationId` is correct.
- Failure or cancellation does not mark the sequence completed.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-CAL-WHITE — White Reference Acceptance

Overall status: `NOT TESTED`

Use a real white reference board.

PASS criteria:

- Every enabled band has a PNG.
- No JPEG is used.
- `bandId` and wavelength mapping are correct.
- Exposure/gain are correct.
- White metadata is correct.
- `calibrationId` is correct.
- White intensity is reasonable.
- There is no large saturated area.
- Uniformity diagnostics are meaningful.
- Failure or cancellation does not mark the sequence completed.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-CAL-MATCH — Dark / White Compatibility

Overall status: `NOT TESTED`

For every enabled band, verify Dark, White, and Sample records match on:

- `bandId`
- `wavelength`
- `exposure`
- `gain`
- camera identity
- filter configuration
- image width/height
- dtype / PixelFormat

PASS criteria:

- No enabled band is missing.
- `CalibrationSet.calibrationComplete=true` only when all real requirements are satisfied.
- `sameBandSettingsMatched=true` only when actual settings match.
- No value is manually forced to true.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-MULTIBAND — Multispectral Sequence Acceptance

Overall status: `NOT TESTED`

Flow:

```text
HOME / origin-ready
-> Band A
-> settle
-> DVP2 PNG
-> Band B
-> settle
-> DVP2 PNG
-> ...
```

PASS criteria:

- All enabled bands complete.
- Sequence order matches configuration.
- Wheel position matches band mapping.
- Image and metadata match.
- Mid-sequence failure stops subsequent capture.
- Failure is never written as completed.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-MULTIVIEW — Multi-view Software Acceptance

Overall status: `NOT TESTED`

If the real sample stage is not implemented, only simulated SampleStage software acceptance may be run. Do not mark Real Sample Stage PASS.

Example software flow:

```text
View 0°  -> RGB PNG -> Multispectral PNG sequence
View 30° -> RGB PNG -> Multispectral PNG sequence
View 60° -> RGB PNG -> Multispectral PNG sequence
...
```

PASS criteria:

- Each View is distinct.
- RGB files are correct.
- Multispectral files are correct.
- `viewId` mapping is correct.
- `views.json` is correct.
- `metadata.json` is correct.
- All Views share the correct sample-level `calibrationId`.
- Sample rotation and filter wheel rotation remain separate domains.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-STAGE — Real Sample Stage Acceptance

Overall status: `BLOCKED`

Current status: P1B-7.5B has a software/API/UI boundary, but the real sample stage protocol is unknown. `UnimplementedSampleStage` reports `SAMPLE_STAGE_PROTOCOL_UNKNOWN`, and simulation must not be used as Real Sample Stage PASS evidence.

Repository evidence found before P1B-7.5B implementation:

- `UPPER_COMPUTER_STM32_INTEGRATION_REQUEST.md` states that the independent sample rotation stage has no real STM32 control interface.
- Existing STM32 AA55 adapter and `CURRENT_FILTER_WHEEL_MAPPING` only describe the filter wheel motor.
- No repository file confirms the sample stage controller, COM/CAN/Ethernet transport, baudrate, command format, encoder, HOME sensor, status query, absolute/relative move, STOP, busy/moving state, fault state, or speed setting.

Future real-stage acceptance items:

| Item | Status | Required Evidence |
| --- | --- | --- |
| STAGE-01 Connect | `BLOCKED` | Real controller identity, transport and successful connection log |
| STAGE-02 Home | `BLOCKED` | HOME command result and proof of whether it is automatic sensor HOME or operator-assisted origin |
| STAGE-03 Move +30° | `BLOCKED` | Command, observed physical motion and status/result |
| STAGE-04 Move absolute 90° | `BLOCKED` | Absolute target command and observed final angle |
| STAGE-05 Position feedback | `BLOCKED` | Fresh post-command status proving current/target angle, or explicit unsupported contract |
| STAGE-06 Stop | `BLOCKED` | STOP command evidence and physical stop observation |
| STAGE-07 Return home | `BLOCKED` | Return-to-zero command/result and final position |
| STAGE-08 Timeout | `BLOCKED` | Induced timeout and safe failure result |
| STAGE-09 Disconnect recovery | `BLOCKED` | Disconnect behavior, UI/API error, reconnect and recovery |
| STAGE-10 Emergency stop | `BLOCKED` | SampleStage safe_stop included in emergency/cancel path |
| STAGE-11 MultiView 3-view test | `BLOCKED` | View 0/30/60 sequence with stage move -> RGB -> multispectral ordering |
| STAGE-12 Full rotation repeatability | `BLOCKED` | 0..330° repeated cycle measurements and repeatability summary |

PASS criteria:

- Real stage connects through its own adapter.
- HOME and position readback are real.
- Movement angles are correct.
- Timeout, stop, disconnect, and recovery are handled.
- `sample_rotation` and `filter_wheel_rotation` are not mixed.

Acceptance record:

```text
Status: BLOCKED
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason: SAMPLE_STAGE_PROTOCOL_UNKNOWN; real controller/protocol/HOME/position feedback contract missing.
Follow-up:
```

## AC-DATA — Scientific Data Integrity

Overall status: `NOT TESTED`

Inspect a complete sample directory containing RGB, multispectral, Dark, White, and MultiView data.

PASS criteria:

- All formal capture image files are PNG.
- Formal scientific metadata does not reference `.jpg` or `.jpeg` as capture frames.
- Preview JPEG files, if present, are clearly outside the formal dataset.
- Training data import uses scientific PNG data, not preview JPEG.

Required distinction:

```text
Preview:
camera -> latest frame -> resize -> JPEG -> Browser

Scientific:
camera raw frame -> PNG -> dataset
```

Recursive check:

| Check | Result |
| --- | --- |
| RGB formal files are PNG |  |
| Multispectral formal files are PNG |  |
| Dark files are PNG |  |
| White files are PNG |  |
| MultiView formal files are PNG |  |
| No formal metadata `.jpg` reference |  |
| No formal metadata `.jpeg` reference |  |

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-METADATA — Metadata Integrity

Overall status: `NOT TESTED`

Inspect `metadata.json`, `views.json`, and `CalibrationSet`.

Required fields:

- `sample_id`
- `sample_name`
- `fruit_type`
- `variety`
- capture time
- RGB directory
- multispectral directory
- `viewId`
- angle
- `bandId`
- wavelength
- exposure
- gain
- camera identity
- `calibrationId`
- filter config
- Dark
- White

PASS criteria:

- Required metadata exists.
- File paths point to real files.
- Missing or failed captures are represented as missing/failed/partial, not completed.
- Camera identity and filter config are traceable.
- Dark/White references match the intended `CalibrationSet`.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-SAFETY — Safety Acceptance

Overall status: `NOT TESTED`

Test at least:

- camera failure
- DVP2 timeout
- STM32 timeout
- filter wheel failure
- emergency stop
- capture cancel
- partial capture

PASS criteria:

- System enters a safe state.
- Unsafe outputs or motion are stopped.
- The application does not crash.
- Failed or partial capture is not reported as completed.
- Evidence shows the recovery path.

Acceptance record:

```text
Status:
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason:
Follow-up:
```

## AC-TRUE-CAPTURE — True Capture Gate

Overall status: `NOT TESTED`

Production release gate: `BLOCKED`

P1B-8 implements the readiness-gated software entry for `POST /api/capture/start`. This does not equal hardware acceptance PASS. Single-view True Capture may be attempted only when `DeviceManager.capture_readiness()` returns ready for the current `TrueCapturePlan`; multi-view remains blocked while the real SampleStage protocol is unknown.

Only after all required domains pass may the team mark production acceptance:

```text
productionAccepted=true
releaseDecision=PASS
```

Required prerequisites:

| Prerequisite | Required Status | Current Status |
| --- | --- | --- |
| RGB | `PASS` | `NOT TESTED` |
| DVP2 | `PASS` | `NOT TESTED` |
| STM32 | `PASS` | `NOT TESTED` |
| Two-channel Tungsten | `PASS` | `NOT TESTED` |
| Filter Wheel | `PASS` | `NOT TESTED` |
| Dark Reference | `PASS` | `NOT TESTED` |
| White Reference | `PASS` | `NOT TESTED` |
| Multi-band Capture | `PASS` | `NOT TESTED` |
| Real Sample Stage | `PASS` | `BLOCKED` |
| Safety | `PASS` | `NOT TESTED` |
| Metadata | `PASS` | `NOT TESTED` |
| PNG Integrity | `PASS` | `NOT TESTED` |

Rules:

- Do not bypass `DeviceManager.capture_readiness()`.
- Do not route true hardware capture through `/api/complete-capture`.
- Do not treat `trueCapturePrepared=true` as production acceptance; it only reflects the current plan readiness.
- Do not use unit tests, fake adapter tests, or simulation as a replacement for hardware PASS.

Acceptance record:

```text
Status: BLOCKED
Date:
Tester:
Application Commit:
Hardware:
Steps Performed:
Observed Result:
Expected Result:
Measured Values:
Evidence:
Failure Reason: Required hardware acceptance domains are not all PASS.
Follow-up:
```

## Final P1B Hardware Acceptance Sign-off

| Acceptance Domain | Status | Evidence | Blocking |
| --- | --- | --- | --- |
| RGB Camera | `NOT TESTED` |  |  |
| DVP2 Multispectral Camera | `NOT TESTED` |  |  |
| STM32 Communication | `NOT TESTED` |  |  |
| Fan | `NOT TESTED` |  |  |
| LED3 | `NOT TESTED` |  |  |
| Door / Actuator | `NOT TESTED` |  |  |
| Filter Wheel | `NOT TESTED` |  |  |
| Dark Reference | `NOT TESTED` |  |  |
| White Reference | `NOT TESTED` |  |  |
| Dark / White Compatibility | `NOT TESTED` |  |  |
| Multi-band Capture | `NOT TESTED` |  |  |
| Real Sample Stage | `BLOCKED` | P1B-7.5B software boundary only; `SAMPLE_STAGE_PROTOCOL_UNKNOWN`. | Real controller/protocol/HOME/position feedback contract missing. |
| Multi-view Capture | `NOT TESTED` |  | Real stage blocked for hardware PASS. |
| Scientific Data Integrity | `NOT TESTED` |  |  |
| Metadata Integrity | `NOT TESTED` |  |  |
| Safety | `NOT TESTED` |  |  |
| True Capture Workflow | `NOT TESTED` | P1B-8 software entry exists. | Required acceptance domains are not all PASS; production release remains blocked. |

### Release Decision

Current decision:

```text
DO NOT ENABLE TRUE CAPTURE
```

Allowed future decision only after every required critical domain is `PASS`:

```text
APPROVED FOR TRUE CAPTURE INTEGRATION
```
