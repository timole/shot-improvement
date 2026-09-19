// Spec 095/096: shot.timolehtonen.tech's gallery - one combined page,
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

function LoginCard({ message }) {
  return (
    <div className="d-flex align-items-center justify-content-center min-vh-100">
      <div className="card shadow-sm" style={{ maxWidth: 420, width: "100%" }}>
        <div className="card-body p-4 text-center">
          <p className="text-success text-uppercase small fw-semibold mb-1">Shot improvement</p>
          <h1 className="h4 mb-3">Liikeratatallenteet</h1>
          <p className="text-muted mb-4">Vain omistajalle. Kirjaudu omalla Microsoft-tililläsi.</p>
          {message && <div className="alert alert-warning py-2 small">{message}</div>}
          <a className="btn btn-primary" href="/api/login/start">
            Kirjaudu Microsoft-tilillä
          </a>
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

function App() {
  const [checking, setChecking] = useState(true);
  const [email, setEmail] = useState(null);
  const [message, setMessage] = useState(() =>
    new URLSearchParams(window.location.search).get("login_error") ? "Kirjautuminen epäonnistui - yritä uudelleen." : ""
  );
  const [videos, setVideos] = useState(null);
  const [status, setStatus] = useState(null);
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  // Autoplay only for a recording that appears (or changes stage) while
  // the page is open - not for whatever was already there on load.
  const initialKey = useRef(undefined);

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

  useEffect(() => {
    if (!email) return;
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
  }, [email]);

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

  return (
    <div className="container py-4">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <p className="text-success text-uppercase small fw-semibold mb-1">Shot improvement</p>
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

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
