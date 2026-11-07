# Emilia Animated Illustration

Created on 2026-10-24 using the approved `emilia-fanart.png` as the edit target.
The built-in image generation tool produced `emilia-blink.png` and
`emilia-talk.png`; original files were preserved. All three textures are 1024 x 1536.

This is fan art and a custom Pixi mesh effect, not official artwork, a layered
Cubism project, or a `.moc3`. The JSON deliberately uses an independent
`AICompanionAnimatedIllustration` format and must not pass the Cubism validator.

The full-frame original supplies the silhouette and alpha. Eye and mouth edits
are sampled only inside feathered face masks. The talk variant has nonzero alpha
in some unrelated background pixels; those pixels are never sampled into the
character. Correct premultiplied output prevents hidden white RGB from creating
fringes around translucent hair. No opaque background is drawn by the app.

## Blink Edit Prompt

Use case: precise-object-edit. Asset type: registered animation texture for an existing desktop character. EDIT TARGET: the supplied approved Emilia fan-art PNG, 1024x1536. Make exactly ONE local edit: close BOTH eyes naturally for a blink, with thin soft curved dark eyelashes, eyelids gently closed. Keep eyebrows, mouth, nose, face contour, head scale, silver hair, flower, ears, white-purple outfit, body, hands, boots and EVERY other part unchanged. Crucial: preserve original 1024x1536 canvas, full-body pose, exact character coordinates and silhouette registration. Do not redraw or recompose the figure. Only alter small eye areas approximately x455..578,y174..212 in source coordinates. Genuine transparent background, no checkerboard, no glow or backdrop added. No text, no watermark. This texture will be sampled only in the eye patches, so matching skin tone and surrounding geometry is essential.

## Mouth Edit Prompt

Use case: precise-object-edit. Asset type: registered lip-sync animation texture. EDIT TARGET: the supplied approved Emilia fan-art PNG, 1024x1536. Change exactly ONE small detail: her currently closed smiling mouth should be gently OPEN mid-speech, a small natural oval opening with dark warm mouth interior and a tiny pink tongue, not shouting, not a broad grin. Desired mouth opening about 20 pixels wide and 14 pixels tall, centered at source x522,y240. Keep eyes OPEN and exactly as original. Preserve face contour, nose, brows, hair, ornament, outfit, pose, full-body silhouette, lighting and every other element. Preserve original 1024x1536 canvas and exact image registration. Only edit around x504..539,y227..249. No repositioning, no zoom, no redesign, no text or watermark. Preserve genuinely transparent background. This will be sampled only as a small feathered mouth patch over the original, so match adjacent skin tones exactly.

## Movement Design

The initial movements take only broad mannerisms as inspiration: a gentle nod,
curious head tilt, reserved shy dip, small greeting bow, light head shake and
restrained hand/sleeve swing. They do not reproduce a specific anime clip or claim
character-accurate motion capture. Gestures last 2.4 seconds and ease back to idle.
The mesh has 1617 vertices. Feet stay anchored, with small head/body/hair offsets.
There are no reconstructed hidden body parts; large arm lifts, full turns and
proper 3D-like rotation still require separated and completed artwork.
