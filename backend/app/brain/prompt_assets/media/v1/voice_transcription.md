---
id: media.voice_transcription
version: 1.0.0
status: active
owner: media-runtime
model_policy: structured_fast
output_schema: VoiceTranscriptOutput
cache_policy: stable_system_prompt
---

Transcribe this voice message exactly.
Output only JSON matching this shape:

```json
{
  "transcript": "exact transcription text"
}
```

Do not add explanation, formatting outside JSON, or translation.
