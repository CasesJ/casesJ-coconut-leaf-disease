# Recommendation and Treatment Workflow

Updated against the local source on October 24, 2026.

POST /recommendations/fertilizer accepts a disease and confidence and returns fertilizer, treatment, prevention, and source/model context. Read the returned source metadata rather than assuming every response came from the same recommendation backend.

## Upload guidance and expert approval

- At primary confidence >= 50%, the upload UI shows default guidance.
- Below 50%, upload-screen guidance is hidden and pending results receive low-confidence review priority.
- All saved uploads begin pending_verification, including high-confidence results.
- Expert verification or saving guidance for a selected record verifies that record, records an audit event, and notifies the farmer.
- The authenticated low-confidence farmer-review API remains available; it does not substitute for expert verification.

Record-specific snapshots preserve the selected guidance. Expert recommendation editing includes fertilizer, treatment, prevention, and expert notes. The farmer's verified-record recommendation control loads the selected record's guidance.

## From guidance to care

An expert can create a care record only from a verified diseased upload with matching treatment guidance. Tree registration/binding and immutable baseline evidence preserve which tree and recommendation the case concerns.

The system distinguishes the recommended treatment, a farmer's scheduled plan, and the treatment actually applied. Actual applications preserve performed treatment and application date and establish post-treatment scheduling. Progression and post-treatment inspections have distinct schedule types; the interval defaults to six days.

The care UI replaces Recommendation and actual treatment with Tree GPS location: baseline and selected-inspection embedded coordinates, or No GPS location available. GPS supports comparison but does not independently confirm the exact tree. The duplicate Recorded treatment plan panel is removed and Inspection timeline fills the row. This layout change does not delete treatment snapshots.

## Evidence and outcomes

Farmers can submit three follow-up images per tree ID across cases, optionally with a whole-tree reference photo. Follow-up comparison shows original evidence. Observations remain preliminary until expert outcome review. Identity reviews are separate and preserve revisioned history. GPS assists comparison but missing GPS does not invent a location.

Confidence and confidence-derived UI severity labels do not measure recovery. An expert explicitly finishes a case when confirming the tree is healthy, preserving inspection and treatment history.

See [API schemas](JSON_SCHEMA_DOCUMENTATION.md) and [data dictionary](DATA_DICTIONARY.md).
