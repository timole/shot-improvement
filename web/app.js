// Spec 087: one combined page - shows a Google Sign-In button (mirrors
// experiments/valkoapila/web/login.js) or, once GET /api/shot-improvement/
// whoami succeeds, the annotated-clip gallery. Unlike Valkoapila this is a
// single screen's worth of content, so there's no separate dashboard page
// to redirect to.

const { useEffect, useRef, useState } = React;

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

function LoginCard({ message, buttonRef }) {
  return (
    <div className="d-flex align-items-center justify-content-center min-vh-100">
      <div className="card shadow-sm" style={{ maxWidth: 420, width: "100%" }}>
        <div className="card-body p-4 text-center">
          <p className="text-success text-uppercase small fw-semibold mb-1">Shot improvement</p>
          <h1 className="h4 mb-3">Liikeratatallenteet</h1>
          <p className="text-muted mb-4">Vain omistajalle. Kirjaudu omalla Google-tililläsi.</p>
          {message && <div className="alert alert-warning py-2 small">{message}</div>}
          <div ref={buttonRef} className="d-flex justify-content-center"></div>
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
              src={`/api/shot-improvement/videos/${v.name}`}
            ></video>
            <div className="card-body py-2">
              <p className="card-text small text-muted mb-0">{formatRecordedAt(v.recorded_at)}</p>
              <p className="card-text small text-muted mb-0">{formatSize(v.size)}</p>
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
  const [message, setMessage] = useState("");
  const [videos, setVideos] = useState(null);
  const buttonRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const who = await fetch("/api/shot-improvement/whoami", { credentials: "same-origin" });
        if (who.ok) {
          const body = await who.json();
          setEmail(body.email);
          setChecking(false);
          return;
        }
      } catch (err) {
        // Ignore - fall through to showing the sign-in button; a real
        // network problem will surface again on the login attempt.
      }
      setChecking(false);
      initGoogleSignIn();
    })();
  }, []);

  useEffect(() => {
    if (!email) return;
    (async () => {
      try {
        const resp = await fetch("/api/shot-improvement/videos", { credentials: "same-origin" });
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

  async function initGoogleSignIn() {
    const configResp = await fetch("/api/shot-improvement/config");
    const { google_client_id } = await configResp.json();
    if (!google_client_id) {
      setMessage("Kirjautuminen ei ole vielä käytössä.");
      return;
    }
    window.google.accounts.id.initialize({ client_id: google_client_id, callback: onCredential });
    window.google.accounts.id.renderButton(buttonRef.current, { theme: "outline", size: "large" });
  }

  async function onCredential(response) {
    let loginResp;
    try {
      loginResp = await fetch("/api/shot-improvement/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ id_token: response.credential }),
      });
    } catch (err) {
      console.error("shot-improvement login request failed", err);
      setMessage("Kirjautuminen epäonnistui (verkkovirhe).");
      return;
    }
    if (!loginResp.ok) {
      const body = await loginResp.json().catch(() => ({}));
      setMessage(body.detail || "Kirjautuminen epäonnistui.");
      return;
    }
    const body = await loginResp.json();
    setEmail(body.email);
  }

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
    return <LoginCard message={message} buttonRef={buttonRef} />;
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
