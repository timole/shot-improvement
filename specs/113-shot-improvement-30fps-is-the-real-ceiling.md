# 113 — Shot-improvement: confirmed ~30fps is this camera's real ceiling

## What

No code change. Documents the conclusion of the spec 109/112 fps
investigation: this camera's real, unique-frame delivery rate at
1280x720 is genuinely ~30fps on this system, confirmed independently
several ways, and the app's current setup (manual exposure + focus)
is the right place to stop.

## Why

The user recorded a reference clip with OBS
(`recordings/2026-09-18 20-31-28.mp4`) that reports 60fps at 1280x720
- seemingly proof 60fps was achievable on this exact hardware "a
moment ago," directly contradicting spec 112's "genuine ceiling"
conclusion. Investigated further as asked, including a new strategy:
capturing directly via ffmpeg's own DirectShow input (`-f dshow`),
bypassing OpenCV's `cv2.VideoCapture` wrapper entirely.

## What was found

`ffmpeg -f dshow -list_options true -i video="c922 Pro Stream Webcam"`
confirms the driver genuinely **advertises**
`vcodec=mjpeg min s=1280x720 fps=5 max s=1280x720 fps=60.0002` as a
capability - this is real, not an OpenCV negotiation failure as
suspected in spec 112.

But advertised capability and actual delivered frames are two
different things. Capturing directly through that exact ffmpeg dshow
mode (`-vcodec mjpeg -video_size 1280x720 -framerate 60`) still only
delivered 300 real frames over ~10 real seconds - the same ~30fps as
`cv2.VideoCapture` always got, just with the output container labeled
"60 fps" because that's the value requested, independent of what the
source actually delivered.

The same check against the user's own OBS reference file settles it:
of 354 consecutive frame pairs, **50.6% are near-identical**
(mean pixel difference 0.063, median 0.001 - i.e. bit-for-bit or
near-bit-for-bit duplicates). OBS is doing exactly what raw ffmpeg
does - receiving ~30 real unique frames/second from the camera and
duplicating every other one to produce a file that *declares* 60fps.
A duplicated frame carries zero new motion information; for this
app's actual purpose (measuring fast motion - puck speed, pose
tracking), padding to a declared 60fps would be pure cosmetic
smoothness, not more real data to work with.

**Decision (user's call, given this evidence): accept ~30fps as the
real ceiling.** Keep spec 109/112's manual exposure + focus (a real,
validated improvement in its own right - consistent behavior
regardless of ambient light, no autofocus hunting), and stop chasing a
"60fps" number that, on this hardware, would only ever be real frames
padded with duplicates.

## Files

None changed - `core/recorder.py` stays as spec 112 left it.

## Test plan

N/A - investigation only, decision documented for future reference so
this ground isn't re-covered without cause. If genuinely higher real
fps is wanted later, the honest next steps (not pursued here) would be
different hardware, or accepting a much lower resolution to see if
that shifts the real ceiling (untested - the DirectShow capability
list's *advertised* fps at other resolutions was never a reliable
signal here either, so this would need the same "duplicate-frame
check", not just trusting a container's declared rate).
