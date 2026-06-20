---
id: learning.skill_synthesis
version: 1.0.0
status: active
owner: brain-skills
model_policy: structured_fast
output_schema: SynthesizedSkill
cache_policy: stable_system_prompt
---

You are given distilled examples of how one business owner handled a recurring
situation.

Write one reusable skill with a trigger, action in the business voice, short
example phrase, dimension, kebab-case slug, short name, and confidence from 0
to 1.

Base it only on the examples. Do not invent policies.
