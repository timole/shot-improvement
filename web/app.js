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

const { useEffect, useState } = React;

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

function App() {
  const [checking, setChecking] = useState(true);
  const [email, setEmail] = useState(null);
  const [message, setMessage] = useState(() =>
    new URLSearchParams(window.location.search).get("login_error") ? "Kirjautuminen epäonnistui - yritä uudelleen." : ""
  );
  const [videos, setVideos] = useState(null);

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
    (async () => {
      try {
        const resp = await fetch("/api/videos", { credentials: "same-origin" });
        if (!resp.ok) {
          setMessage("Videoiden lataus epäonnistui.");
          return;
        }
        const body = await resp.json();
        setVideos(body.videos);
      } catch (err) {
        setMessage("Videoiden lataus epäonnistui (verkkovirhe).");
      }
    })();
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
