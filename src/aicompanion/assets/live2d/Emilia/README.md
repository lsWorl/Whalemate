# Emilia Fan-Art Source

Created on 2026-10-07 with the built-in image generation tool, using the user's
supplied character screenshot as a visual reference. The user approved the artwork.

`emilia-fanart.png` is a newly generated 1024 x 1536 RGBA illustration with a
transparent background. It is fan art, not an official Re:Zero asset.

## Generation Prompt

Create a new original Emilia fan illustration: full-body frontal neutral pose,
silver hair, violet eyes, half-elf ears, white flower ornament and violet ribbons,
white-and-violet embroidered outfit, gold sleeve details, white thigh-high boots,
relaxed separated arms, closed-mouth gentle expression, clean anime cel shading,
transparent background, no text or watermark, entire figure visible. Use the
user's screenshot as design reference, not as a trace or a watermark-removal target.

## Rigging Status

This is one flattened PNG, not a layered PSD, not a Cubism model, and not a moc3.
The app provides a clearly labeled illustration preview. It does not fake mouth
animation or claim this preview has completed Live2D rigging.

2026-10-24: a separate custom animated-illustration mode now uses this source plus
generated blink/mouth patches. See `animation-source.md` for the exact prompts,
rendering limits and movement design. It is intentionally not called a Cubism
model. Static preview and actual Cubism support remain separate modes.

To become a real Live2D character it still requires separated and redrawn parts
(back hair, body, neck, face, eyes and lids, brows, mouth, front hair, sleeves and
accessories), deformation meshes, parameter keys, physics, expressions and Cubism
runtime export. Keep original artwork unchanged when making these working assets.

Suggested runtime parameters: ParamAngleX/Y/Z, ParamBodyAngleX, ParamEyeLOpen,
ParamEyeROpen, ParamEyeBallX/Y, ParamBrowLY/RY, ParamMouthOpenY, ParamMouthForm,
ParamCheek and ParamBreath. These are design targets, not parameters already bound.
