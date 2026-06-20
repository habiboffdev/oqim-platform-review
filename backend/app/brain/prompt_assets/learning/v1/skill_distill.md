---
id: learning.skill_distill
version: 1.0.0
status: active
owner: brain-skills
model_policy: structured_fast
output_schema: DistilledBatch
cache_policy: stable_system_prompt
---

Distill each observed turn pair into one factual sentence:

`Owner did X when the other party said or did Y.`

Be concrete. Tag one dimension:
price, delivery, stock, payment, greeting, objection, followup, or general.

Return one item per input pair and preserve each given index.
