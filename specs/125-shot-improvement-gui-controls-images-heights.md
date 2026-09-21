# 125 — GUI: correct video height, images in the list, raw-frame controls

## What
- **Height**: `_show_frame` shows every frame 640 px wide with the height
  following the frame's own aspect. Annotated clips (640x640: video +
  spectrogram + claps band) were squashed into the fixed 640x360 box.
- **Recordings list** ("Tallenteet") also lists the per-shot images
  (`*-shot-NN.jpg`), sorted with their recording's videos. Double-click /
  "Toista" shows the image in the preview (mode `image`, "Takaisin
  livekuvaan" returns). Deleting an image only removes the local file
  (images aren't synced to the cloud).
- **Raw-frame controls under the video (right column)**: "-1 ruutu",
  "▶ Toista raakakuvaa", "+1 ruutu", speed, and a slider over the
  selected shot's frames with "ruutu N/M  t s". The left column keeps the
  shots list. Stepping or dragging pauses playback.

## Files
`gui.py`.

## Tests
`pytest tests` 112 passed; a Tk smoke script checked the annotated frame
(640x640 shown), list contents, image mode, frame step, slider seek and
2x playback to the end, plus a screenshot of the layout.
