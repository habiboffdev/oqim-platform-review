---
id: contact_classifier.batch_user
version: 1.0.0
status: active
owner: onboarding-runtime
model_policy: structured_fast
output_schema: ContactClassificationBatch
cache_policy: stable_system_prompt
---

Classify each contact below. Return a JSON array with one object per contact, in the same order.

Contacts:
{contacts_block}

Return a JSON array:
[{{"index": 0, "contact_type": "customer|personal|supplier|work", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}, ...]

Return exactly {count} objects -- one per contact, ordered by index.
