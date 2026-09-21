# shot-improvement

A Windows desktop app for reviewing ice-hockey shots. It films practice
with a webcam and microphone, detects each shot from the sound of the
stick hitting the puck, measures the puck's speed, marks the hands on
the video, and publishes the clips to a private web page.

Everything is processed on the laptop; only the finished clips are
uploaded.

## Features

- **Capture**: 640x360 at 30 fps from a Logitech C922 webcam (fixed
  exposure and focus, so the frame rate stays steady) plus its
  microphone. Frames are written to disk as they arrive, so long clips
  don't fill the memory.
- **Shot detection from audio**: finds the loud, high-frequency impact
  of each shot, pairs it with the puck hitting the far end, and computes
  the puck speed from the rink length. Ignores footsteps and other
  noise.
- **Annotated video**: yellow boxes on the player's hands (on-device
  MediaPipe pose model), a spectrogram of the audio with each shot's speed.
- **Show stick**: the "Show stick" button under the video draws the
  stick as a light-green line between the player's two hands - straight
  when the stick is straight, an arc when it bends - on the frame being
  viewed, so the bend of a shot can be studied frame by frame.
- **Shot browser**: right after a recording, a list of the detected
  shots with a picture and speed for each. "Toista raakakuvaa" plays the
  raw frames from two seconds before the shot at 0.25x to 2x speed.
- **Record now, process later**: "Vain nauhoitus" (on by default) only
  saves the frames while recording, so capture stays at full frame rate.
  "Käsittele odottavat" then turns the saved recordings into videos.
- **Small files for mobile networks**: fast, low-bitrate mp4s (a 10 s
  clip is about 0.3 MB).
- **Cloud and web page**: each raw and annotated clip is uploaded to
  Azure Blob Storage, and deleting a clip locally deletes it in the
  cloud. The private page at <https://shot.timolehtonen.tech> (Microsoft
  sign-in) lists the recordings. While the app is processing a new
  recording, an open page shows the fastest shot, a countdown, and then
  plays the newest clip automatically.

## Architecture

![Architecture diagram](docs/architecture.png)

Capture and all processing run on the laptop (Edge). Only the finished
clips and a small status file go to Azure Blob Storage, which the web
page reads. The diagram's source is [docs/architecture.drawio](docs/architecture.drawio);
`tools/export_diagrams.py` regenerates the PNG.

## Running the app

Requires Windows, Python 3.12, a Logitech C922 webcam, and the
[Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
signed in (`az login`) for cloud sync.

First-time setup, from your home directory in PowerShell:

```
cd ~\Documents\Projektit\shot-improvement
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

Start the app:

```
cd ~\Documents\Projektit\shot-improvement
venv\Scripts\python gui.py
```

The window shows the camera, and the app logs which camera and
microphone it picked. Press the record button; after the countdown it
records for the chosen number of seconds (10 by default).

To record one clip from the command line instead:

```
venv\Scripts\python record.py 10
```

Recordings go to `recordings/`, saved-but-unprocessed ones to
`pending/`, and logs to `logs/shot-improvement.log` (all ignored by
git).

## Tests

```
venv\Scripts\python -m pytest tests
```

The web server's tests need the packages in
`server/requirements.txt` and `server/requirements-dev.txt`.

## More

- [specs/](specs/) - a numbered write-up of every change and why.
- [docs/azure.md](docs/azure.md) - how the Azure web page is set up and
  redeployed.
