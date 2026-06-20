---
id: media.image_description
version: 1.0.0
status: active
owner: media-runtime
model_policy: media_rich
output_schema: ImageSemanticDescription
cache_policy: stable_system_prompt
---

Return JSON only.

Describe what is visibly in this image in one concise sentence.
Focus on visible product details, labels, quantities, colors, model names, and customer-relevant context.

Also return a typed media evidence capsule:

- `media_evidence.schema_version` must be `media_evidence.v1`.
- `media_evidence.modality` should be `photo`.
- `media_evidence.summary` is a concise human-readable summary.
- `media_evidence.observations` is open-ended evidence, not a fixed business
  taxonomy. Use short `kind` values such as `visible_object`,
  `visible_attribute`, `embedded_text`, `payment_confirmation_visible`,
  `damage_visible`, `address_visible`, or another direct observation when
  needed.
- Observations must describe only what is visible. Do not decide stock,
  availability, payment state, order state, delivery state, or what action the
  seller should take.
- Payment screenshots, product photos, address screenshots, damaged item photos,
  and unknown images are all evidence first. Universal Extraction and owner
  runtimes interpret them later with conversation, catalog, KB, Business Brain,
  OQIM Intelligence, rules, and policy.

Required JSON shape:

```json
{
  "visible_description": "one concise visible description",
  "confidence": 0.0,
  "media_evidence": {
    "schema_version": "media_evidence.v1",
    "modality": "photo",
    "summary": "one concise visible summary",
    "observations": [
      {
        "kind": "visible_object",
        "value": "object name",
        "confidence": 0.0,
        "fields": {}
      }
    ],
    "embedded_text": [],
    "transcript": null,
    "customer_supplied": true,
    "confidence": 0.0
  }
}
```
