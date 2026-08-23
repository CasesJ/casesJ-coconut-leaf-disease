# Recommendation System

`POST /recommendations/fertilizer` creates fertilizer, treatment, and prevention guidance from a disease name and confidence score.

## Upload-screen behavior

- **50% or higher:** default guidance appears directly below the disease detection.
- **Below 50%:** no recommendation appears on the upload screen. The saved record appears in Expert Review under **Needs Review**.

Showing high-confidence guidance does not verify the result. Every upload stays pending until an expert verifies it.

## Expert guidance

An expert can edit fertilizer, treatment, prevention notes, and an expert note for a selected record. Saving an expert recommendation creates a record-level snapshot, verifies the record, records an audit event, and notifies the farmer.

The low-confidence farmer-review endpoint remains available for authenticated API clients, but the farmer upload UI intentionally does not show recommendations below 50%.
