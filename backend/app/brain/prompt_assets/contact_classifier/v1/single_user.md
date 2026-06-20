---
id: contact_classifier.single_user
version: 1.0.0
status: active
owner: onboarding-runtime
model_policy: structured_fast
output_schema: ContactClassification
cache_policy: stable_system_prompt
---

Classify this contact based on their conversation with the seller.

<contact_info>
Name: {display_name}
Is group: {is_group}
</contact_info>

<messages>
{messages_text}
</messages>

Return valid JSON:
{{
  "contact_type": "customer" | "supplier" | "personal" | "work" | "group",
  "confidence": 0.0 to 1.0,
  "reasoning": "Brief explanation in English"
}}
