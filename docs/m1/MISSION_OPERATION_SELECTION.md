# M1 Mission operation selection

The Mission view represents one selected operation. The legacy `view()` response and the
strict `mission.view_state` response use the same deterministic selector so that a caller
cannot see an intent from one operation together with observations from another one.

The selected intent is the `operation.intent` envelope in the Mission outbox with the greatest
`created_at`; a tied timestamp is resolved by the greatest `message_id`. This ordering comes
from envelope data and does not depend on receipt order. Every `operation_id` must map to one
`correlation_id`, and every correlation used by an intent must identify one operation. A mapping
with more than one value is an explicit `MissionViewSelectionError`; the view does not guess.

Once the correlation is selected, projections are limited to inbox envelopes with that
correlation. The selected snapshot is the greatest `world_revision`, then `observed_at`,
`produced_at`, and `message_id`. The selected forecast is the greatest `produced_at`, then
`predicted_for` and `message_id`. Terminal events are limited to terminal states and ordered by
`occurred_at`, `contract_revision`, and `message_id`; a same-rank conflict between terminal
states yields no terminal result rather than selecting `SUCCEEDED` by accident. Contradictory
terminal states for the same contract revision, or anywhere in the selected operation, likewise
yield no terminal result; repeated `SUCCEEDED` terminal delivery remains valid. A later
non-terminal event cannot hide a selected terminal event.

The confirmed robot is the selected intent's `preferred_executor`; a snapshot containing only
another robot does not become confirmed evidence. The forecast must identify that executor and,
when a confirmed state exists, use the same spatial frame and calibration before an arrival belief
is exposed. An incompatible forecast therefore produces no arrival sample or prediction manifest.
The operator target uses the dummy pose only when it is comparable with the selected snapshot frame
and calibration; with no confirmed snapshot it remains the pending target branch for the selected
intent.

This is correlation scoping, not full world coherence or lineage validation. In particular, the
M1 golden session intentionally combines its world-revision-2 snapshot with its world-revision-1
forecast. A future invalidation and lineage policy must decide when a newer snapshot invalidates
an otherwise valid forecast; this selector does not make that decision.
