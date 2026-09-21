# 133 — "Palauta oletukset" (reset) button

A button under "Käsittele odottavat" that puts the GUI back to the state of a
fresh start and shows the live camera preview: leaves saved-clip playback /
shot list / still-image view, clears the shot list and selection, and
resets duration (10 s), "Vain nauhoitus" (on), "Ammuntapaikka" (blue line),
raw-frame and playback speed (1x), volume (100%), mute and loop (off), and
the camera / microphone / speaker pickers to what the app picked at startup
(only switched if they differ). Not allowed during a countdown or recording
(status: "Palautus ei onnistu kesken tallennuksen."). Recordings and files are
not touched. Files: `gui.py`.

Checked with a Tk smoke script (dirty state -> reset -> defaults, shot view and
image view left, blocked while recording); `pytest tests` 115 passed.
