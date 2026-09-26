# Model Validation Scope

The read-only report validator supports RM_INPUT workbooks whose `rm_model` is exactly `Logistic3` or `Logistic4`, compared case-insensitively.

## Logistic4

The exported parameter set must contain exactly one recognized value for each of:

- amplitude -> `param_a / 100` when `effect_scale` is `percent`; otherwise the fractional `param_a` is unchanged
- slope -> `param_b`
- ec50 -> `param_c`
- bottom -> `param_d / 100` when `effect_scale` is `percent`; otherwise the fractional `param_d` is unchanged

All four are required. Unknown, malformed, missing, or duplicate parameter fields fail validation.

## Logistic3

The exported parameter set must contain exactly one recognized value for each of:

- amplitude -> `param_a / 100` when `effect_scale` is `percent`; otherwise the fractional `param_a` is unchanged
- slope -> `param_b`
- ec50 -> `param_c`

An exported bottom is optional only when it is numeric and equals the fixed value `0`. Unknown, malformed, missing, or duplicate parameter fields fail validation.

## Unsupported validator models

`Sigmoid3`, `Sigmoid4`, and every other model produce an explicit unsupported-model failure. This is the current validator's acceptance scope. It is not a claim that OpenMRA itself cannot execute those models.

These conversions are provenance checks between the input and export. They are not scientific proof that a SigmaPlot parameter has the same mathematical meaning in OpenMRA.
