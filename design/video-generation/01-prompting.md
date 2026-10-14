# Video generation: how MiniMax H3 reads a prompt

Research, 2026-10-10. It feeds the `video-generation` skill's prompt sections. Every claim cites its
source; T-marked sources are third-party.

## Why

Live, 2026-10-10: a clip was made with the requester's photo as `reference_image`. MiniMax's task
record shows `input_image_count: 1`, so the photo arrived and was billed, yet the clip showed a
different animal in a different bed and room. The prompt never named the picture and described a
new subject and scene from scratch. The adapter's `content[]` shape matches the documented one
([C]); what failed was the prompt.

## Sources

- [C] Create task, `POST /v2/video_generation`: https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create.md (EN: https://platform.minimax.io/docs/api-reference/video-generation-v2-create.md)
- [G] Video generation guide: https://platform.minimax.io/docs/guides/video-generation.md
- [IR] H3 Context-IR (prompt enhancement endpoint): https://platform.minimaxi.com/docs/api-reference/video-generation-v2-h3-context-ir.md
- [HL] H3 highlights and example gallery: https://platform.minimaxi.com/docs/guides/video-prompt.md
- [LD] Local deploy guide: https://platform.minimax.io/docs/guides/local-deploy-h3.md
- [HF] Model card and license: https://huggingface.co/MiniMaxAI/MiniMax-H3
- [PB] Base-mode prompt guide: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md
- [PR] Reference-mode prompt guide: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md
- [SK] MiniMax's own prompt-writing skill: https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md
- [OLD] Hailuo-era APIs (not H3): https://platform.minimax.io/docs/api-reference/video-generation-t2v.md

## Findings

1. **Name references by type and order.** The hosted API's own examples write «人物参考图1 图2»,
   «音色参考音频1», «人物资产参考图2» ([G], [C], [HL]); the structured guides write `<Picture 1>`,
   `<Video 1>`, `<Audio 1>` and number in connection order ([PR], [LD]). Each reference should say
   what it carries: identity, scene, motion, camera or voice ([LD], [PR]).
2. **Identity is kept by describing it, consistently with the picture**, at the subject's first
   clear appearance, plus a statement that identity is kept ([PR], [HL]: «保持身份一致»). New
   actions or backgrounds asked for are not fidelity losses ([PR]). What the model does with a
   prompt that contradicts a reference is not documented.
3. **First and last frame** is a separate mode, mutually exclusive with any `reference_*` role ([C]).
   Its prompt describes the motion from the frame, not the frame again ([PB], [C]); ratio is
   always adaptive ([C]). `video_create` offers it as `first_frame` since 2026-10-10 (00-design §9).
4. **Camera**: natural-language moves (push in, pull out, pan, truck, tilt, pedestal, arc, tracking,
   static, shake, POV, roll) with amplitude and speed, not stacked labels ([PB]). [G] still mentions
   bracket labels, a Hailuo-era convention ([OLD]); conflict noted.
5. **Sound** is generated with the picture ([HF]). Spoken or sung lines go in word for word, in the
   original language, `<d>[Language] ...</d>` with a speaker ([PB]). Eleven languages are listed as
   stable, Chinese among them; Cantonese is not named ([HF]).
6. **On-screen text** goes in double quotes, verbatim ([PB]).
7. **Length and language**: up to 7000 characters per `text` item ([C]); structured guides write
   English with dialogue and lyrics in their original language ([PR], [SK]); the hosted examples
   are Chinese free text ([C]).
8. **No negative prompt and no `prompt_optimizer`** on H3 ([C]); the old Hailuo flag does not apply
   ([OLD]). [IR] returns an enhanced structured prompt; the model card calls it critical to quality
   ([HF]). Whether the hosted endpoint applies it internally is not documented.

## Not done, and why

- First-frame mode in `video_create`: built 2026-10-10 (00-design §9); `last_frame` is not offered.
- Calling [IR] before every clip: an extra provider call per clip, unmeasured; worth an A/B once
  first-frame mode exists.
- Cantonese lines: unmeasured; the skill tells her to warn when the words matter.
