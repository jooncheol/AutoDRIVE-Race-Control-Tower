# RCT Bridge Dispatch Bias Evaluation

This document records the fairness checks for AutoDRIVE Race Control Tower (RCT) when forwarding `Bridge` traffic between the simulator and the mock DevKit endpoints.

## Goal

Verify whether RCT introduces bias between `V1` and `V2` while relaying `Bridge` messages.

The fairness criterion used here is simple:

- `V1` and `V2` should report the same `Bridge Hz` in the frontend under the same test conditions.
- If both values match, the relay path is treated as unbiased for that scenario.

## Test Setup

### Shared conditions

- Same RCT build and same Socket.IO / Engine.IO versions
- Same frontend monitor view
- Same `bridge_sample.json` payload
- `mock_devkit.py` used as the DevKit-side endpoint
- `mock_simulator.py` used as the simulator-side client

### Metric

- Frontend `Bridge Hz` shown for `Roboracer A / V1`
- Frontend `Bridge Hz` shown for `Roboracer B / V2`

## Test Case 1 - Simulator-side bias check

Topology:

```text
AutoDRIVE Simulator -> RCT -> mock_devkit (FIFO mode)
```

Purpose:

- Check whether the simulator-to-RCT path or RCT-to-DevKit fanout favors one vehicle over the other.

Procedure:

1. Start `mock_devkit.py` in `FIFO` mode.
2. Start `mock_simulator.py`.
3. Connect the frontend monitor view to RCT.
4. Observe the `Bridge Hz` values for `V1` and `V2`.

Expected result:

- `V1 Bridge Hz == V2 Bridge Hz`

Observed result:

- `V1 Bridge Hz == V2 Bridge Hz`

Conclusion:

- No simulator-side bias was observed in this scenario.

## Test Case 2 - RCT-side bias check

Topology:

```text
mock_simulator -> RCT -> mock_devkit (FIFO mode)
```

Purpose:

- Check whether RCT itself favors one vehicle while handling inbound simulator Bridge frames and outbound DevKit Bridge responses.

Procedure:

1. Start `mock_devkit.py` in `FIFO` mode.
2. Start `mock_simulator.py`.
3. Connect the frontend monitor view to RCT.
4. Observe the `Bridge Hz` values for `V1` and `V2`.

Expected result:

- `V1 Bridge Hz == V2 Bridge Hz`

Observed result:

- `V1 Bridge Hz == V2 Bridge Hz`

Conclusion:

- No RCT-side bias was observed in this scenario.

## Final Conclusion

Both fairness tests produced equal `Bridge Hz` values for `V1` and `V2`.

- Test Case 1: passed, no bias detected
- Test Case 2: passed, no bias detected

This indicates that, under the tested FIFO conditions, RCT does not show measurable fairness bias between `V1` and `V2`.
