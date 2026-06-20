---
id: learning.skill_upload_normalize
version: 1.0.0
status: active
owner: brain-skills
model_policy: structured_fast
output_schema: SynthesizedSkill
cache_policy: stable_system_prompt
---

Normalize a provided SKILL.md document or free-text description into one
reusable skill.

Extract a kebab-case slug, short name, trigger, action, short example phrase,
dimension, and confidence from 0 to 1. Keep only what fits the schema. Ignore
unrelated noise.

Do not invent policies or behavior that are not present in the input.
