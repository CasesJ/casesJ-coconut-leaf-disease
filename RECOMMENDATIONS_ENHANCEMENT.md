# Recommendation System

Recommendations are generated for a detected disease and confidence value through `POST /recommendations/fertilizer`. A verified record stores a recommendation snapshot so its guidance remains tied to that upload.

Experts can edit fertilizer advice, treatment advice, prevention notes, and an expert note for a selected upload. Saving a record-level expert recommendation verifies that record, preserves the expert snapshot, writes an audit event, and creates a farmer notification.

The UI presents recommendations from Detection Records after verification. Pending records show that expert review is still required.
