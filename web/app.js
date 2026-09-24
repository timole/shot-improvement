// Spec 095/096: snapshot.timolehtonen.tech's gallery - one combined page,
// shows a "sign in with Microsoft" link or, once GET /api/whoami
// succeeds, the annotated-clip gallery. Ported from the original
// GCP-hosted version at ai.timolehtonen.tech/shot-improvement (spec
// 087), updated for this service's own top-level /api/... paths (this
// whole domain IS shot-improvement, nothing else shares it) and its
// own identity provider (spec 096: Microsoft Entra ID, not Google -
// no GCP dependency anywhere in this service). Sign-in is a plain
// server-side redirect (GET /api/login/start -> Microsoft -> GET
// /api/login/callback -> back here with a session cookie set), not a
// client-side SDK - so, unlike the Google version, there's no init
// step and no credential callback here at all; this component only
// ever reads whoami/videos.

const { useEffect, useRef, useState } = React;

const POLL_MS = 3000;

function formatSize(bytes) {
  if (bytes == null) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} Mt` : `${Math.round(bytes / 1024)} kt`;
}

function formatRecordedAt(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("fi-FI", { dateStyle: "medium", timeStyle: "short" });
}

// Spec 094/095: every video has a same-stem .jpg preview (server-side:
// server/blob_videos.py's PREVIEW_NAME_RE), uploaded by
// core/cloud_sync.py as a side effect of the video's own upload -
// derived here rather than carried in the /api/videos response, since
// the naming convention is fixed and deterministic.
function previewUrl(videoName) {
  return `/api/previews/${videoName.replace(/\.mp4$/, ".jpg")}`;
}

// Spec 136: public install page for the Android app - shown above both the
// sign-in card and the gallery, so a phone can get it before signing in.
function AndroidLink() {
  return (
    <div className="bg-success-subtle text-center py-2 small">
      <a className="fw-semibold text-success-emphasis" href="/android">
        Lataa Android-sovellus (kiekon nopeus äänestä)
      </a>
    </div>
  );
}

function LoginCard({ message }) {
  return (
    <div className="d-flex flex-column min-vh-100">
      <AndroidLink />
      <div className="d-flex align-items-center justify-content-center flex-grow-1">
        <div className="card shadow-sm" style={{ maxWidth: 420, width: "100%" }}>
          <div className="card-body p-4 text-center">
            <p className="text-success text-uppercase small fw-semibold mb-1">Snapshot improvement</p>
            <h1 className="h4 mb-3">Kirjaudu sisään</h1>
            <p className="text-muted mb-4">Vain omistajalle. Kirjaudu omalla Microsoft-tililläsi.</p>
            {message && <div className="alert alert-warning py-2 small">{message}</div>}
            <a className="btn btn-primary" href="/api/login/start">
              Kirjaudu Microsoft-tilillä
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

// Spec 148: the front page, "/" - just two links out to the two kinds of
// recording, each its own full page load (plain <a> href, not a client-
// side router - matching this repo's own no-bundler, no-router style;
// see server/main.py's /liikeratatallenteet and /puhelimen-laukaukset
// routes, which serve this same index.html/app.js shell). Previously
// both sections lived on this one page at once (spec 147); split out
// once the phone's own shots needed a page of their own too.
function HomePage({ email }) {
  return (
    <div className="container py-4">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <p className="text-success text-uppercase small fw-semibold mb-0">Snapshot improvement</p>
        <span className="text-muted small">{email}</span>
      </div>
      <div className="row g-3">
        <div className="col-12 col-md-6">
          <a href="/liikeratatallenteet" className="card shadow-sm text-decoration-none h-100">
            <div className="card-body">
              <h2 className="h5 mb-1">Liikeratatallenteet</h2>
              <p className="text-muted small mb-0">Kannettavan tallentamat videot, käsien tunnistuksella merkittyinä.</p>
            </div>
          </a>
        </div>
        <div className="col-12 col-md-6">
          <a href="/puhelimen-laukaukset" className="card shadow-sm text-decoration-none h-100">
            <div className="card-body">
              <h2 className="h5 mb-1">Puhelimen laukaukset</h2>
              <p className="text-muted small mb-0">Android-sovelluksella tallennetut laukaukset.</p>
            </div>
          </a>
        </div>
      </div>
    </div>
  );
}

// Small "back to front page" link, shared by both subpages.
function HomeLink() {
  return (
    <a href="/" className="d-inline-block mb-3 small text-muted text-decoration-none">
      ← Etusivulle
    </a>
  );
}

// Spec 147/149: the Android app's own shots - one card per shot stem, not
// per file, since a shot can have audio with no video (camera unavailable
// that session). Matches MainActivity's own Historia row shape (spec
// 149): a small preview, the timestamp, the shooting place, and the
// speed (or the same "Osumaa ei kuulunut." mobile shows when a shot has
// no paired hit). Tapping a card opens AndroidShotDetail - the download
// links spec 147 originally put directly on the card moved into that
// dialog (see its own "Lataa" buttons).
// Spec 155: paged rather than rendering every shot at once - each row's
// <img> fires its own network request (a cache miss means the server
// runs ffmpeg to build it), so a page with hundreds of accumulated
// shots was issuing hundreds of concurrent thumbnail requests on every
// load, which is both what made the page feel slow and what made the
// server-side download race (see android_blobs.get_cached_path) easy
// to trigger in the first place. visibleCount only grows via the
// button below, so polling for new shots (AndroidPage's own effect)
// never collapses a list the user has already expanded.
const ANDROID_PAGE_SIZE = 20;

function AndroidShotList({ shots, onSelect }) {
  const [visibleCount, setVisibleCount] = useState(ANDROID_PAGE_SIZE);
  if (shots.length === 0) {
    return <p className="text-muted">Ei vielä puhelimen tallenteita.</p>;
  }
  const visible = shots.slice(0, visibleCount);
  return (
    <>
      <div className="list-group">
        {visible.map((s) => (
          <div
            className="list-group-item list-group-item-action d-flex align-items-center gap-2 py-1"
            role="button"
            key={s.stem}
            onClick={() => onSelect(s.stem)}
          >
            <img
              className="bg-dark rounded flex-shrink-0"
              src={`/api/android/thumbnail/${s.stem}`}
              alt=""
              loading="lazy"
              width={32}
              height={32}
              style={{ width: 32, height: 32, objectFit: "cover" }}
            />
            <div className="flex-grow-1 small text-truncate">
              <span className="text-muted me-2">{formatRecordedAt(s.recorded_at)}</span>
              <span className="fw-semibold me-2">
                {s.speed_kmh != null ? `${Math.round(s.speed_kmh)} km/h` : "Osumaa ei kuulunut."}
              </span>
              {s.place && <span className="text-muted">{s.place}</span>}
            </div>
          </div>
        ))}
      </div>
      {visibleCount < shots.length && (
        <div className="text-center mt-3">
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            onClick={() => setVisibleCount((c) => c + ANDROID_PAGE_SIZE)}
          >
            Näytä lisää ({shots.length - visibleCount} jäljellä)
          </button>
        </div>
      )}
    </>
  );
}

const ANDROID_STEP_S = 1.0;
// Matches MainActivity's own PLAYBACK_SPEEDS exactly (spec 146/150).
const ANDROID_PLAYBACK_SPEEDS = [0.1, 0.25, 0.5, 1.0, 2.0];

// "1,421" not "1.421" - matches MainActivity's own VideoFrameBox, whose
// "%.3f".format(timeS) runs under the app's default (Finnish) Locale.
function formatFiDecimal(value, digits) {
  return value.toFixed(digits).replace(".", ",");
}

// Spec 149: a tapped shot's speed, composed video (camera + spectrogram +
// hand-position boxes baked into one file server-side - see
// server/android_compose.py) and playback controls, matching
// MainActivity's own ShotDetailDialog: speed on top, then the video, the
// same "ruutu N/count   t s" frame readout VideoFrameBox shows, ±1s/one-
// frame step buttons, and a "continue automatically to the next shot"
// switch. Unlike the phone (which steps through individually decoded
// JPEG frames with their own real, unevenly-spaced capture timestamps),
// this steps a plain HTML5 <video>'s currentTime across the composed
// clip's own constant frame rate - GET /api/android/composed-meta/{stem}
// supplies fps and the real encoded frame count (an HTML5 video element
// exposes neither), so "one frame" lands on the exact same instant every
// time, not an approximation that drifts with repeated stepping.
function AndroidShotDetail({
  stem,
  shots,
  onSelectStem,
  onClose,
  autoAdvance,
  onAutoAdvanceChange,
  playbackSpeed,
  onPlaybackSpeedChange,
}) {
  const shot = shots.find((s) => s.stem === stem);
  const videoRef = useRef(null);
  const [meta, setMeta] = useState(null); // {duration_s, fps, frame_count}
  const [frameIndex, setFrameIndex] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setMeta(null);
    setFrameIndex(0);
    fetch(`/api/android/composed-meta/${stem}`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (!cancelled && body) setMeta(body);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [stem]);

  // Applied both right after this shot's <video> mounts (key={stem} makes
  // a fresh element every time) and whenever the speed selector changes
  // while already playing - matches MainActivity's own playbackSpeed
  // LaunchedEffect. HTML5 video has no separate pitch control the way
  // MediaPipe's PlaybackParams does, so slow motion here does pitch-shift
  // the audio down - a real difference, not a bug to hide, but not worth
  // a Web Audio API pitch-correction pipeline just to match the phone
  // exactly for a personal gallery.
  useEffect(() => {
    const v = videoRef.current;
    if (v) v.playbackRate = playbackSpeed;
  }, [playbackSpeed, stem]);

  // The one playback clock the frame readout is derived from - polled via
  // the video element's own timeupdate event (fires during playback AND
  // after a manual seek), not a separate local counter that could drift
  // from what's actually on screen.
  function handleTimeUpdate() {
    const v = videoRef.current;
    if (!v || !meta || !meta.fps) return;
    const idx = Math.round(v.currentTime * meta.fps);
    setFrameIndex(Math.min(Math.max(idx, 0), meta.frame_count - 1));
  }

  function seekToFrame(idx) {
    const v = videoRef.current;
    if (!v || !meta || !meta.fps) return;
    v.pause(); // a manual step always takes over from auto-play, same as the phone's own seekToS
    const clamped = Math.min(Math.max(idx, 0), meta.frame_count - 1);
    v.currentTime = clamped / meta.fps;
    setFrameIndex(clamped);
  }

  function stepFrames(delta) {
    seekToFrame(frameIndex + delta);
  }

  function stepSeconds(deltaS) {
    const v = videoRef.current;
    if (!v || !meta || !meta.fps) return;
    seekToFrame(Math.round((v.currentTime + deltaS) * meta.fps));
  }

  function handleEnded() {
    if (!autoAdvance) return;
    const idx = shots.findIndex((s) => s.stem === stem);
    const next = shots[idx + 1];
    if (next) onSelectStem(next.stem);
  }

  return (
    <div className="modal d-block" tabIndex={-1} style={{ backgroundColor: "rgba(0,0,0,0.6)" }} onClick={onClose}>
      <div className="modal-dialog modal-dialog-centered" onClick={(e) => e.stopPropagation()}>
        <div className="modal-content">
          <div className="modal-body p-3">
            <p className="text-muted small mb-1">{shot ? formatRecordedAt(shot.recorded_at) : ""}</p>
            {shot && shot.place && <p className="text-muted small mb-1">{shot.place}</p>}
            <p className="display-6 mb-3">
              {shot && shot.speed_kmh != null ? `${Math.round(shot.speed_kmh)} km/h` : "Osumaa ei kuulunut."}
            </p>
            <video
              ref={videoRef}
              key={stem}
              className="w-100 bg-dark"
              controls
              autoPlay
              playsInline
              onEnded={handleEnded}
              onTimeUpdate={handleTimeUpdate}
              src={`/api/android/composed/${stem}`}
            ></video>
            {meta && (
              <p className="text-muted small mt-1 mb-0">
                ruutu {frameIndex + 1}/{meta.frame_count}&nbsp;&nbsp;&nbsp;{formatFiDecimal(frameIndex / meta.fps, 3)} s
              </p>
            )}
            <div className="d-flex justify-content-between mt-2">
              <button className="btn btn-sm btn-outline-secondary" onClick={() => stepSeconds(-ANDROID_STEP_S)}>
                -1 s
              </button>
              <button className="btn btn-sm btn-outline-secondary" onClick={() => stepFrames(-1)}>
                ◀
              </button>
              <button className="btn btn-sm btn-outline-secondary" onClick={() => stepFrames(1)}>
                ▶|
              </button>
              <button className="btn btn-sm btn-outline-secondary" onClick={() => stepSeconds(ANDROID_STEP_S)}>
                +1 s
              </button>
            </div>
            <div className="d-flex justify-content-between mt-2">
              {ANDROID_PLAYBACK_SPEEDS.map((speed) => (
                <button
                  key={speed}
                  className="btn btn-sm btn-link p-0 text-decoration-none"
                  style={{ fontWeight: playbackSpeed === speed ? 700 : 400 }}
                  onClick={() => onPlaybackSpeedChange(speed)}
                >
                  {speed}x
                </button>
              ))}
            </div>
            <div className="d-flex align-items-center justify-content-between mt-3">
              <span className="small">Jatka automaattisesti seuraavaan</span>
              <div className="form-check form-switch mb-0">
                <input
                  className="form-check-input"
                  type="checkbox"
                  checked={autoAdvance}
                  onChange={(e) => onAutoAdvanceChange(e.target.checked)}
                />
              </div>
            </div>
            <div className="d-flex gap-2 flex-wrap mt-3">
              {shot && shot.has_video && (
                // Spec 149: the composed file, not the phone's raw upload -
                // "one combined portrait .mp4 ... spectrogram at the bottom
                // ... and the sound" is what a download should give, same
                // as what's already playing above (and the raw upload has
                // no rotation metadata at all - see android_compose's own
                // module docstring - so it would play sideways on its own).
                <a className="btn btn-sm btn-outline-secondary" href={`/api/android/composed/${stem}`} download={`${stem}-composed.mp4`}>
                  Lataa video
                </a>
              )}
              {shot && shot.has_audio && (
                <a className="btn btn-sm btn-outline-secondary" href={`/api/android/audio/${stem}`} download={`${stem}.wav`}>
                  Lataa ääni
                </a>
              )}
            </div>
            <button className="btn btn-secondary w-100 mt-3" onClick={onClose}>
              Sulje
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function VideoList({ videos }) {
  if (videos.length === 0) {
    return <p className="text-muted">Ei vielä tallenteita.</p>;
  }
  return (
    <div className="row g-3">
      {videos.map((v) => (
        <div className="col-12 col-md-6 col-lg-4" key={v.name}>
          <div className="card shadow-sm h-100">
            <video
              className="card-img-top bg-dark"
              controls
              preload="none"
              poster={previewUrl(v.name)}
              src={`/api/videos/${v.name}`}
            ></video>
            <div className="card-body py-2">
              <p className="card-text small text-muted mb-0">{formatRecordedAt(v.recorded_at)}</p>
              <p className="card-text small text-muted mb-2">{formatSize(v.size)}</p>
              <a
                className="btn btn-sm btn-outline-secondary"
                href={`/api/videos/${v.name}`}
                download={v.name}
              >
                Lataa
              </a>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}


// Spec 120: the newest recording, live. The laptop publishes its stage
// (processing -> raw_ready -> done) plus the fastest shot's km/h; this
// counts down to when each clip should be viewable, measured against
// the SERVER's clock (serverOffsetMs) so a wrong browser clock doesn't
// skew it. Once a clip is uploaded it plays here automatically.
function secondsLeft(status, etaKey, nowMs) {
  const endedMs = Date.parse(status.capture_ended_at);
  if (Number.isNaN(endedMs)) return null;
  return Math.max(0, Math.ceil((endedMs + status[etaKey] * 1000 - nowMs) / 1000));
}

function Countdown({ label, seconds }) {
  if (seconds === null) return null;
  return (
    <p className="mb-1">
      {label}{" "}
      {seconds > 0 ? (
        <strong style={{ fontVariantNumeric: "tabular-nums" }}>noin {seconds} s</strong>
      ) : (
        <strong>hetki vielä…</strong>
      )}
    </p>
  );
}

function LatestPanel({ status, serverOffsetMs, autoPlay }) {
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 250);
    return () => clearInterval(t);
  }, []);
  if (!status || !status.id) return null;

  const serverNow = nowMs + serverOffsetMs;
  const rawName = `shot-improvement-${status.id}.mp4`;
  const annotatedName = `shot-improvement-${status.id}-annotated.mp4`;
  const fastest =
    status.fastest_kmh != null ? (
      <p className="display-6 mb-2">
        Nopein laukaus: <strong>{status.fastest_kmh} km/h</strong>
      </p>
    ) : !status.shots_checked && (status.state === "processing" || status.state === "deferred") ? (
      <p className="text-muted mb-2">Laukauksen nopeutta lasketaan…</p>
    ) : (
      <p className="text-muted mb-2">Laukauksia ei tunnistettu.</p>
    );

  let heading = "Uusin tallenne";
  let video = null;
  let countdowns = null;
  if (status.state === "deferred") {
    heading = "Uusi tallenne tallennettu";
    countdowns = (
      <p className="text-muted mb-1">
        Videota ei ole vielä käsitelty – se ilmestyy tähän, kun tallenne käsitellään sovelluksessa.
      </p>
    );
  } else if (status.state === "processing") {
    heading = "Uusi tallenne käsittelyssä";
    countdowns = (
      <>
        <Countdown label="Video katsottavissa" seconds={secondsLeft(status, "raw_eta_s", serverNow)} />
        <Countdown label="Merkitty video valmis" seconds={secondsLeft(status, "annotated_eta_s", serverNow)} />
      </>
    );
  } else if (status.state === "raw_ready") {
    heading = "Video katsottavissa – merkittyä videota käsitellään";
    video = rawName;
    countdowns = (
      <Countdown label="Merkitty video valmis" seconds={secondsLeft(status, "annotated_eta_s", serverNow)} />
    );
  } else {
    video = annotatedName;
  }

  return (
    <div className="card shadow-sm mb-4">
      <div className="card-body">
        <div className="d-flex align-items-center mb-2">
          {status.state !== "done" && status.state !== "deferred" && (
            <div className="spinner-border spinner-border-sm text-success me-2" role="status"></div>
          )}
          <h2 className="h5 mb-0">{heading}</h2>
        </div>
        {fastest}
        {countdowns}
        {video && (
          <video
            key={video}
            className="w-100 bg-dark mt-2"
            style={{ maxWidth: 720 }}
            controls
            muted
            playsInline
            autoPlay={autoPlay}
            src={`/api/videos/${video}`}
          ></video>
        )}
      </div>
    </div>
  );
}

// Spec 148: was App()'s own render output before the front page split -
// now its own page at /liikeratatallenteet, with its own polling (videos +
// status) instead of sharing App()'s single combined poll.
function GalleryPage({ email }) {
  const [message, setMessage] = useState("");
  const [videos, setVideos] = useState(null);
  const [status, setStatus] = useState(null);
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  // Autoplay only for a recording that appears (or changes stage) while
  // the page is open - not for whatever was already there on load.
  const initialKey = useRef(undefined);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const [vresp, sresp] = await Promise.all([
          fetch("/api/videos", { credentials: "same-origin" }),
          fetch("/api/status", { credentials: "same-origin" }),
        ]);
        if (cancelled) return;
        if (vresp.ok) {
          const body = await vresp.json();
          // Keep the same array when nothing changed so the cards (and
          // any video being watched) don't re-render every poll.
          setVideos((prev) => (prev && JSON.stringify(prev) === JSON.stringify(body.videos) ? prev : body.videos));
          setMessage("");
        } else {
          setMessage("Videoiden lataus epäonnistui.");
        }
        if (sresp.ok) {
          const body = await sresp.json();
          setServerOffsetMs(Date.parse(body.server_now) - Date.now());
          if (initialKey.current === undefined) {
            initialKey.current = body.status ? `${body.status.id}:${body.status.state}` : "";
          }
          setStatus((prev) => (prev && JSON.stringify(prev) === JSON.stringify(body.status) ? prev : body.status));
        }
      } catch (err) {
        if (!cancelled) setMessage("Videoiden lataus epäonnistui (verkkovirhe).");
      }
    }
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="container py-4">
      <HomeLink />
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <p className="text-success text-uppercase small fw-semibold mb-1">Snapshot improvement</p>
          <h1 className="h4 mb-0">Liikeratatallenteet</h1>
        </div>
        <span className="text-muted small">{email}</span>
      </div>
      {message && <div className="alert alert-warning py-2 small">{message}</div>}
      <LatestPanel
        status={status}
        serverOffsetMs={serverOffsetMs}
        autoPlay={!!status && `${status.id}:${status.state}` !== initialKey.current}
      />
      {videos === null ? (
        <div className="spinner-border text-success" role="status">
          <span className="visually-hidden">Ladataan&hellip;</span>
        </div>
      ) : (
        <VideoList videos={videos} />
      )}
    </div>
  );
}

// Spec 148: the Android shots' own page, at /puhelimen-laukaukset - was a
// second section on the combined page (spec 147), split out alongside
// GalleryPage above once the front page became just two links.
function AndroidPage({ email }) {
  const [message, setMessage] = useState("");
  const [androidShots, setAndroidShots] = useState(null);
  const [selectedStem, setSelectedStem] = useState(null);
  // Session-wide, not per-shot (matches MainActivity's own autoAdvance/
  // playbackSpeed state) - toggling either while reviewing one shot
  // carries over to the next.
  const [autoAdvance, setAutoAdvance] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1.0);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const resp = await fetch("/api/android/videos", { credentials: "same-origin" });
        if (cancelled) return;
        if (resp.ok) {
          const body = await resp.json();
          setAndroidShots((prev) => (prev && JSON.stringify(prev) === JSON.stringify(body.shots) ? prev : body.shots));
          setMessage("");
        } else {
          setMessage("Laukausten lataus epäonnistui.");
        }
      } catch (err) {
        if (!cancelled) setMessage("Laukausten lataus epäonnistui (verkkovirhe).");
      }
    }
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="container py-4">
      <HomeLink />
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <p className="text-success text-uppercase small fw-semibold mb-1">Snapshot improvement</p>
          <h1 className="h4 mb-0">Puhelimen laukaukset</h1>
        </div>
        <span className="text-muted small">{email}</span>
      </div>
      {message && <div className="alert alert-warning py-2 small">{message}</div>}
      {androidShots === null ? (
        <div className="spinner-border text-success" role="status">
          <span className="visually-hidden">Ladataan&hellip;</span>
        </div>
      ) : (
        <AndroidShotList shots={androidShots} onSelect={setSelectedStem} />
      )}
      {selectedStem && androidShots && (
        <AndroidShotDetail
          stem={selectedStem}
          shots={androidShots}
          onSelectStem={setSelectedStem}
          onClose={() => setSelectedStem(null)}
          autoAdvance={autoAdvance}
          onAutoAdvanceChange={setAutoAdvance}
          playbackSpeed={playbackSpeed}
          onPlaybackSpeedChange={setPlaybackSpeed}
        />
      )}
    </div>
  );
}

function App() {
  const [checking, setChecking] = useState(true);
  const [email, setEmail] = useState(null);
  const [message, setMessage] = useState(() =>
    new URLSearchParams(window.location.search).get("login_error") ? "Kirjautuminen epäonnistui - yritä uudelleen." : ""
  );

  useEffect(() => {
    (async () => {
      try {
        const who = await fetch("/api/whoami", { credentials: "same-origin" });
        if (who.ok) {
          const body = await who.json();
          setEmail(body.email);
        }
      } catch (err) {
        // Ignore - fall through to showing the sign-in link; a real
        // network problem will surface again on the login attempt.
      }
      setChecking(false);
    })();
  }, []);

  if (checking) {
    return (
      <div className="d-flex align-items-center justify-content-center min-vh-100">
        <div className="spinner-border text-success" role="status">
          <span className="visually-hidden">Ladataan&hellip;</span>
        </div>
      </div>
    );
  }

  if (!email) {
    return <LoginCard message={message} />;
  }

  // Plain full-page navigation between routes (see HomePage/HomeLink's own
  // <a href> tags) - no client-side router, so which page to render is
  // just whatever path this particular page load came in on.
  const path = window.location.pathname;
  let page;
  if (path === "/liikeratatallenteet") {
    page = <GalleryPage email={email} />;
  } else if (path === "/puhelimen-laukaukset") {
    page = <AndroidPage email={email} />;
  } else {
    page = <HomePage email={email} />;
  }

  return (
    <>
      <AndroidLink />
      {page}
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
