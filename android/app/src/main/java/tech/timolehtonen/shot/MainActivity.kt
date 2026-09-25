package tech.timolehtonen.shot

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Matrix
import android.graphics.RectF
import android.graphics.SurfaceTexture
import android.media.MediaPlayer
import android.media.PlaybackParams
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Surface
import android.view.TextureView
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlin.math.roundToInt

/** What the status line under the rink map shows while a listening session runs. */
private sealed interface ListenState {
    data object Idle : ListenState
    data object Listening : ListenState
    data class AwaitingHit(val shotTimeS: Double) : ListenState
    // Spec 153: shown after "Lopeta kuuntelu" while extractPendingVideoSnippets
    // still has shots left to trim - previously the screen went straight back to
    // Idle the instant the button was tapped, while trimming quietly continued in
    // the background, so a shot's video would just silently appear in Historia a
    // few seconds (formerly, before this spec, sometimes tens of seconds) later
    // with no indication anything was still happening.
    data class Processing(val done: Int, val total: Int) : ListenState
}

/** Which top-level screen is shown (spec 139/141) - swapped in place of a navigation library. */
private sealed interface Screen {
    data object Main : Screen
    data object Statistics : Screen
    data object Settings : Screen
}

// Spec 139: fixed shot-centred window, not shot-to-hit - an unpaired shot (no
// audible hit) gets the same window as a paired one, and the hit sound itself
// is deliberately not guaranteed to be inside it. Spec 158: POSTROLL_S bumped
// 1.0 -> 3.0 (a 4s clip instead of 2s) - more room after the shot for the
// puck's flight and the hit itself to fit on screen.
private const val PREROLL_S = 1.0
private const val POSTROLL_S = 3.0
private const val SPECTRO_VISIBLE_S = 6.0 // long enough to show a shot and its hit together at most distances
private const val SPECTRO_TICK_MS = 100L // ~10 fps - the scrolling comes from real time advancing, not a fast redraw
private val SHOT_MARKER_COLOR = Color(0xFFFFEB3B)
private val HIT_MARKER_COLOR = Color(0xFF00E5FF)

/**
 * Bridges the background thread that opens [CameraSession] with a
 * Compose-owned `TextureView`'s `SurfaceTexture` (spec 146) - the surface
 * only exists once the `CameraPreview` composable using it has actually
 * been laid out on screen, which happens on the UI thread shortly after
 * `startListening` sets the state that makes it appear, not before.
 */
private class PreviewSurfaceHolder {
    @Volatile var textureView: TextureView? = null
        private set
    @Volatile private var surfaceTexture: SurfaceTexture? = null
    private val latch = CountDownLatch(1)

    fun provide(view: TextureView, texture: SurfaceTexture) {
        textureView = view
        surfaceTexture = texture
        latch.countDown()
    }

    /** Blocks the calling (background, camera-open) thread until the preview
     * surface is ready, or [timeoutMs] passes - null means "no preview this
     * session," the same graceful fallback every other optional-video path
     * in this app already has. */
    fun await(timeoutMs: Long): SurfaceTexture? {
        latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        return surfaceTexture
    }
}

/**
 * Rotates and fill-scales a `TextureView`'s content so a landscape-native
 * camera sensor's output looks upright and fills the (square) preview box
 * (spec 146) - the live-preview equivalent of `CameraSession`'s
 * `setOrientationHint` on the recorded file. Simplified from the classic
 * Camera2 sample's version: that one also accounts for the *device's* own
 * rotation, which this app doesn't need to handle since its activity is
 * locked to portrait (AndroidManifest) - [sensorOrientation] alone is the
 * whole correction.
 */
private fun applyPreviewTransform(textureView: TextureView, bufferWidth: Int, bufferHeight: Int, sensorOrientation: Int) {
    val viewWidth = textureView.width.toFloat()
    val viewHeight = textureView.height.toFloat()
    if (viewWidth <= 0f || viewHeight <= 0f) return
    val swapped = sensorOrientation == 90 || sensorOrientation == 270
    val bufW = if (swapped) bufferHeight else bufferWidth
    val bufH = if (swapped) bufferWidth else bufferHeight

    val matrix = Matrix()
    val viewRect = RectF(0f, 0f, viewWidth, viewHeight)
    val centerX = viewRect.centerX()
    val centerY = viewRect.centerY()
    val bufferRect = RectF(0f, 0f, bufW.toFloat(), bufH.toFloat())
    bufferRect.offset(centerX - bufferRect.centerX(), centerY - bufferRect.centerY())
    matrix.setRectToRect(viewRect, bufferRect, Matrix.ScaleToFit.FILL)
    val scale = maxOf(viewHeight / bufH, viewWidth / bufW)
    matrix.postScale(scale, scale, centerX, centerY)
    matrix.postRotate(sensorOrientation.toFloat(), centerX, centerY)
    textureView.setTransform(matrix)
}

/** The live camera feed while listening (spec 146 - "Pushing Aloita starts
 * the video, too... Show the video preview, too"), square to match the
 * saved-shot playback box's own treatment. Purely a `TextureView` host -
 * [PreviewSurfaceHolder] is how the surface it creates reaches the camera. */
@Composable
private fun CameraPreview(holder: PreviewSurfaceHolder, modifier: Modifier = Modifier) {
    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            val view = TextureView(ctx)
            view.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                override fun onSurfaceTextureAvailable(surface: SurfaceTexture, width: Int, height: Int) {
                    holder.provide(view, surface)
                }
                override fun onSurfaceTextureSizeChanged(surface: SurfaceTexture, width: Int, height: Int) {}
                override fun onSurfaceTextureDestroyed(surface: SurfaceTexture): Boolean = true
                override fun onSurfaceTextureUpdated(surface: SurfaceTexture) {}
            }
            view
        },
    )
}

/** A shot's own trimmed clip, played back in [ShotDetailDialog] (spec 153,
 * replacing that dialog's old JPEG-per-frame VideoFrameBox) - a TextureView
 * transformed the same way [CameraPreview]'s live feed is, since a trimmed
 * clip carries no rotation metadata of its own for a raw MediaPlayer+
 * TextureView to pick up even if it wanted to (see VideoDecoder.trimToMp4's
 * own doc comment). Purely a view host, like CameraPreview - the caller
 * owns the actual MediaPlayer, handed [onSurfaceReady]'s `Surface` once the
 * TextureView has one to give. */
@Composable
private fun VideoPlaybackSurface(
    videoWidth: Int,
    videoHeight: Int,
    sensorOrientation: Int,
    modifier: Modifier = Modifier,
    onSurfaceReady: (Surface) -> Unit,
) {
    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            val view = TextureView(ctx)
            view.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                override fun onSurfaceTextureAvailable(surface: SurfaceTexture, width: Int, height: Int) {
                    applyPreviewTransform(view, videoWidth, videoHeight, sensorOrientation)
                    onSurfaceReady(Surface(surface))
                }
                override fun onSurfaceTextureSizeChanged(surface: SurfaceTexture, width: Int, height: Int) {
                    applyPreviewTransform(view, videoWidth, videoHeight, sensorOrientation)
                }
                override fun onSurfaceTextureDestroyed(surface: SurfaceTexture): Boolean = true
                override fun onSurfaceTextureUpdated(surface: SurfaceTexture) {}
            }
            view
        },
    )
}

class MainActivity : ComponentActivity() {
    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ShotFiles.cleanupDevFiles(this) // dev-mode snippets from a previous process; see saveSnippet
        setContent { MaterialTheme { ShotScreen() } }
    }

    /**
     * Saves the WAV+JSON for one shot (named [stem]): a fixed window,
     * [PREROLL_S] before the shot to [POSTROLL_S] after it - not
     * shot-to-hit. It plays back on tap in the history list regardless of
     * whether a hit was ever heard, and deliberately does not try to
     * stretch to include the hit sound.
     */
    private fun saveSnippet(detector: LiveDetector, record: ShotRecord, stem: String) {
        val startS = (record.shotT - PREROLL_S).coerceAtLeast(0.0)
        val endS = record.shotT + POSTROLL_S
        val samples = detector.snapshotSeconds(startS, endS)
        val dir = ShotFiles.recordingsDir(this)
        ShotFiles.writeWav(File(dir, "$stem.wav"), samples, detector.sampleRate)
        // Read live rather than a hardcoded literal (spec 141) - a hardcoded string here
        // had already drifted from build.gradle.kts's real versionName by two releases.
        val appVersion = try {
            packageManager.getPackageInfo(packageName, 0).versionName
        } catch (e: Exception) {
            "unknown"
        }
        val json = JSONObject()
            .put("appVersion", appVersion)
            .put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
            .put("sampleRate", detector.sampleRate)
            .put("distanceM", record.distanceM)
            .put("place", record.place)
            .put("timestampMs", record.timestampMs)
            .put("shotT", record.shotT - startS)
        json.put("hitT", record.hitT?.let { it - startS } ?: JSONObject.NULL)
        json.put("speedKmh", record.speedKmh ?: JSONObject.NULL)
        File(dir, "$stem.json").writeText(json.toString(2))
    }

    /**
     * Remuxes a shot's own [PREROLL_S]..[POSTROLL_S] window - same shotT as
     * [saveSnippet]'s audio, so the two stay in sync - out of the whole
     * session's [videoFile] (spec 145) into a small standalone MP4.
     * [offsetS] (from [CameraSession.offsetS]) converts the window from
     * `shotT`'s own clock (elapsed time since the mic opened) to the video
     * file's internal one (elapsed time since the recording actually
     * started, which - opening the mic first - always starts a little
     * later). Called once per shot, but only after the whole session's
     * recording has been [CameraSession.close]d - a `MediaRecorder`-written
     * file isn't safely seekable until then, so a shot's video is not ready
     * the instant the shot happens; see [extractPendingVideoSnippets].
     *
     * Spec 153: no longer also decodes a JPEG-per-frame sequence
     * (VideoDecoder.extractFrames) the way it did through spec 152 -
     * measured at ~7.7s for a single 2-second/480-frame shot on this
     * hardware, entirely swamping [trimToMp4]'s own ~0.5s remux and the
     * real cause of shots visibly "trickling in" to Historia over many
     * seconds after a session with several shots ended. [ShotDetailDialog]
     * now plays this MP4 directly (a real MediaPlayer + TextureView,
     * frame-accurate seeking via [VideoDecoder.probeVideoMeta]) instead of
     * stepping through those JPEGs, so decoding them ahead of time bought
     * nothing a viewer could tell apart from decoding on demand - except
     * the multi-second wait every shot paid whether or not it was ever
     * reviewed.
     */
    private fun extractVideoSnippet(camera: CameraSession, record: ShotRecord, stem: String) {
        val startS = (record.shotT - PREROLL_S).coerceAtLeast(0.0) - camera.offsetS
        val endS = record.shotT + POSTROLL_S - camera.offsetS
        val mp4File = File(ShotFiles.recordingsDir(this), "$stem.mp4")
        val trimmed = VideoDecoder.trimToMp4(camera.outputFile, mp4File, startS, endS)
        Log.i(TAG, "extractVideoSnippet: trimmed mp4 ($trimmed) for $stem")
    }

    /** Runs [extractVideoSnippet] for every shot recorded this session, then
     * deletes the now-fully-consumed session recording - called once, after
     * [CameraSession.close] has finalised it, from the listening loop's own
     * background thread (which has nothing else left to do at that point).
     * One shot's decode failing doesn't stop the rest from being tried.
     * [onProgress] (spec 153), if given, is called after each shot with
     * (done, total) - the UI's own 0-100% indicator while this runs. */
    private fun extractPendingVideoSnippets(
        camera: CameraSession,
        pending: List<Pair<ShotRecord, String>>,
        onProgress: (Int, Int) -> Unit = { _, _ -> },
    ) {
        for ((index, pair) in pending.withIndex()) {
            val (record, stem) = pair
            try {
                extractVideoSnippet(camera, record, stem)
            } catch (e: Exception) {
                Log.w(TAG, "extractPendingVideoSnippets: failed for $stem", e)
            }
            onProgress(index + 1, pending.size)
        }
        camera.outputFile.delete()
    }

    /** Uploads every non-dev shot from this session to
     * snapshot.timolehtonen.tech (spec 147), once its local files are ready -
     * on its own thread so a slow or absent network can't hold up
     * anything else. Whatever [CloudUploader.uploadShot] finds on disk for
     * a stem is whatever gets sent - a camera-less session simply has no
     * "$stem.mp4" to attach, and its audio uploads on its own. */
    private fun uploadPendingShots(pending: List<Pair<ShotRecord, String>>) {
        if (pending.isEmpty()) return
        thread(name = "shot-upload") {
            val dir = ShotFiles.recordingsDir(this)
            for ((_, stem) in pending) {
                val videoFile = File(dir, "$stem.mp4").takeIf { it.isFile }
                val audioFile = File(dir, "$stem.wav").takeIf { it.isFile }
                val metadataFile = File(dir, "$stem.json").takeIf { it.isFile }
                val ok = CloudUploader.uploadShot(stem, videoFile, audioFile, metadataFile)
                Log.i(TAG, "uploadPendingShots: $stem -> ${if (ok) "ok" else "failed"}")
            }
        }
    }

    @Composable
    private fun ShotScreen() {
        var screen by remember { mutableStateOf<Screen>(Screen.Main) }
        var showVersionDialog by remember { mutableStateOf(false) }
        var menuExpanded by remember { mutableStateOf(false) }
        // Spec 141: defaults on - a shot recorded before the user deliberately turns
        // this off stays out of the persisted history and Stats, not the other way
        // round, since a "real" session is the more consequential thing to opt into.
        var devMode by remember { mutableStateOf(true) }
        var targetFps by remember { mutableStateOf(Prefs.targetFps(this@MainActivity)) }
        var distanceM by remember { mutableStateOf(Rink.DEFAULT_PLACE.distanceM) }
        var listenState by remember { mutableStateOf<ListenState>(ListenState.Idle) }
        var listeningFlag by remember { mutableStateOf(false) } // read by the background loop too
        var lastResult by remember { mutableStateOf<ShotRecord?>(null) }
        var history by remember { mutableStateOf(ShotHistory.loadAll(this@MainActivity)) }
        var errorText by remember { mutableStateOf("") }
        var liveSpectrogram by remember { mutableStateOf<LiveSpectrogram?>(null) }
        var liveSnapshot by remember { mutableStateOf<LiveSpectrogram.Snapshot?>(null) }
        var markers by remember { mutableStateOf<List<SpectroMarker>>(emptyList()) }
        var selectedShot by remember { mutableStateOf<ShotRecord?>(null) }
        // Spec 143: whether the detail dialog auto-advances to the next history item
        // when a shot finishes playing - a session-wide setting like devMode/targetFps,
        // not per-shot, so it survives the dialog moving from one record to the next.
        var autoAdvance by remember { mutableStateOf(false) }
        // Spec 146: likewise session-wide, not per-shot - reviewing several shots in a
        // row at the same slow-motion setting is the natural workflow, not resetting
        // to 1x every time the dialog moves to a different record.
        var playbackSpeed by remember { mutableStateOf(1.0f) }
        // Spec 146: non-null only while a listening session both has camera permission
        // and hasn't finished (its CameraPreview composable disappears the moment this
        // goes back to null) - see startListening/PreviewSurfaceHolder.
        var previewHolder by remember { mutableStateOf<PreviewSurfaceHolder?>(null) }
        // Spec 149: true once this session's camera has opened without a live
        // preview - only above 30fps, where CameraSession.open drops the
        // preview surface to avoid this phone's high-speed-capture green-
        // frame bug (see that function's own comment). Lets the preview box
        // show a short caption instead of silently staying blank.
        var previewUnavailable by remember { mutableStateOf(false) }

        // Spec 143: with Dev mode off, dev-tagged shots (including the ones already
        // marked as such, spec 141) drop out of the visible list entirely, not just
        // out of Stats - "kun dev-mode kytketään pois päältä, näkyy vain oikeat
        // tallenteet". With it on, everything shows, same as before this spec.
        val visibleHistory = remember(history, devMode) { if (devMode) history else history.filter { !it.isDev } }

        // The item below [current] in the same newest-first order History displays -
        // what "seuraava" (next) means for auto-advance; null once at the oldest shown.
        fun nextInHistory(current: ShotRecord): ShotRecord? {
            val ordered = visibleHistory.asReversed()
            val idx = ordered.indexOf(current)
            return if (idx in 0 until ordered.size - 1) ordered[idx + 1] else null
        }

        // Spec 141: the stored/default fps preference (60) may not be one this
        // device's camera can actually reach (e.g. only 30 fps normal, no
        // constrained-high-speed capability) - correct it once at startup so
        // Settings doesn't show a disabled option as "selected", and so the
        // very first session already requests something achievable.
        LaunchedEffect(Unit) {
            val supported = withContext(Dispatchers.IO) { CameraSession.supportedFps(this@MainActivity) }
            if (supported.isNotEmpty() && targetFps !in supported) {
                val fallback = supported.filter { it <= targetFps }.maxOrNull() ?: supported.min()
                targetFps = fallback
                Prefs.setTargetFps(this@MainActivity, fallback)
            }
        }

        fun postState(update: () -> Unit) = mainHandler.post(update)

        fun startListening() {
            errorText = ""
            markers = emptyList()
            listeningFlag = true
            listenState = ListenState.Listening
            val recordingDev = devMode // locked in for the session, same as distanceM
            val fps = targetFps // spec 141: likewise locked in for the session
            thread(name = "shot-listen") {
                val mic = MicSession.open(this@MainActivity)
                if (mic == null) {
                    postState {
                        listeningFlag = false
                        listenState = ListenState.Idle
                        errorText = "Mikrofonia ei voitu avata."
                    }
                    return@thread
                }
                // Spec 145: the whole session records to one file, [sessionStartNanos]
                // (captured right after the mic opened) is the audio clock's zero point -
                // CameraSession measures its own offsetS from it, so a shot's shotT (on
                // the audio clock) can later be converted to this recording's own timeline.
                val sessionStartNanos = System.nanoTime()
                // Spec 140: video is an enhancement, not required to measure speed - if the
                // camera permission was declined or no camera opens, listening still proceeds
                // audio-only (camera stays null for this whole session in that case).
                val hasCameraPermission = ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                val videoFile = File(ShotFiles.recordingsDir(this@MainActivity), "tmp-video-" + ShotFiles.timestamp() + ".mp4")
                // Spec 146: only created (and thus only shown - see the CameraPreview
                // composable below) when there's actually a camera permission for it to
                // matter; the background thread waits briefly for its TextureView to be
                // laid out and ready before handing its surface to CameraSession.open.
                val previewHolderForSession = if (hasCameraPermission) PreviewSurfaceHolder() else null
                if (previewHolderForSession != null) postState { previewHolder = previewHolderForSession }
                val previewTexture = previewHolderForSession?.await(2000)
                val camera = if (hasCameraPermission) {
                    CameraSession.open(this@MainActivity, fps, videoFile, sessionStartNanos, previewTexture)
                } else {
                    null
                }
                if (hasCameraPermission && camera == null) Log.w(TAG, "startListening: camera unavailable, continuing audio-only")
                if (camera != null) {
                    postState {
                        previewHolderForSession?.textureView?.let { applyPreviewTransform(it, camera.width, camera.height, camera.sensorOrientation) }
                        previewUnavailable = !camera.hasLivePreview
                    }
                }
                // Spec 145: which shots need a video snippet, decided as they happen but
                // only actually extracted once the session's recording is finalised (see
                // extractPendingVideoSnippets) - a MediaRecorder-written file isn't safely
                // seekable while still being recorded to.
                val pendingVideoShots = mutableListOf<Pair<ShotRecord, String>>()
                // Spec 147: which shots to upload to snapshot.timolehtonen.tech once this
                // session ends - tracked separately from pendingVideoShots (populated
                // whenever the shot isn't a dev one, not just when there's a camera),
                // so an audio-only session still uploads its audio.
                val pendingUploads = mutableListOf<Pair<ShotRecord, String>>()

                val detector = LiveDetector(mic.sampleRate, distanceM)
                val spectro = LiveSpectrogram(mic.sampleRate, SPECTRO_VISIBLE_S)
                postState { liveSpectrogram = spectro }
                val chunk = ShortArray(4096)
                var shotIndex = 0
                val historyFile = ShotHistory.file(this@MainActivity)

                fun stemFor(shotIndex: Int) =
                    (if (recordingDev) "dev-shot-" else "shot-") + ShotFiles.timestamp() + "-$shotIndex"

                fun persist(record: ShotRecord) {
                    if (!recordingDev) ShotHistory.append(historyFile, record) // spec 139: dev shots never touch disk history
                    val stem = record.snippetFile!!.removeSuffix(".wav")
                    saveSnippet(detector, record, stem)
                    if (camera != null) pendingVideoShots.add(record to stem)
                    if (!recordingDev) pendingUploads.add(record to stem) // spec 147: dev shots never leave the phone
                }

                while (listeningFlag) {
                    val n = mic.read(chunk)
                    if (n <= 0) {
                        Thread.sleep(5)
                        continue
                    }
                    spectro.feed(chunk, n)
                    for (event in detector.feed(chunk, n)) {
                        when (event) {
                            is LiveDetector.Event.Shot -> postState {
                                listenState = ListenState.AwaitingHit(event.timeS)
                                markers = markers + SpectroMarker(event.timeS, "Laukaus", SHOT_MARKER_COLOR)
                            }
                            is LiveDetector.Event.Hit -> {
                                shotIndex++
                                val stem = stemFor(shotIndex)
                                val record = ShotRecord(
                                    timestampMs = System.currentTimeMillis(), distanceM = distanceM,
                                    place = Rink.describe(distanceM), shotT = event.shotTimeS, hitT = event.hitTimeS,
                                    speedKmh = event.speedKmh, snippetFile = "$stem.wav", isDev = recordingDev,
                                )
                                persist(record)
                                val label = event.speedKmh?.let { "${Math.round(it)} km/h" } ?: "Osuma"
                                postState {
                                    lastResult = record; history = history + record; listenState = ListenState.Listening
                                    markers = markers + SpectroMarker(event.hitTimeS, label, HIT_MARKER_COLOR)
                                }
                            }
                            is LiveDetector.Event.Unpaired -> {
                                shotIndex++
                                val stem = stemFor(shotIndex)
                                val record = ShotRecord(
                                    timestampMs = System.currentTimeMillis(), distanceM = distanceM,
                                    place = Rink.describe(distanceM), shotT = event.shotTimeS, hitT = null,
                                    speedKmh = null, snippetFile = "$stem.wav", isDev = recordingDev,
                                )
                                persist(record)
                                postState { lastResult = record; history = history + record; listenState = ListenState.Listening }
                            }
                            is LiveDetector.Event.NoisyEnvironment -> Log.w(TAG, "LiveDetector: too many events/s, ignoring one")
                            is LiveDetector.Event.SessionCapReached -> {
                                postState {
                                    listeningFlag = false
                                    listenState = ListenState.Idle
                                    errorText = "Kuuntelu pysäytetty (20 min raja) - aloita uudelleen."
                                }
                            }
                        }
                    }
                }
                postState { liveSpectrogram = null; previewHolder = null; previewUnavailable = false }
                mic.close()
                camera?.close() // finalises videoFile so it's safely seekable
                // Spec 153: was silent (straight back to ListenState.Idle, set by
                // stopListening() the instant the button was tapped) while trimming
                // ran invisibly in the background - now the UI shows 0-100% for
                // however many shots have video, since that's what determines when
                // Historia's own video playback actually becomes available.
                if (camera != null && pendingVideoShots.isNotEmpty()) {
                    postState { listenState = ListenState.Processing(0, pendingVideoShots.size) }
                    extractPendingVideoSnippets(camera, pendingVideoShots) { done, total ->
                        postState { listenState = ListenState.Processing(done, total) }
                    }
                } else {
                    camera?.outputFile?.delete()
                }
                postState { listenState = ListenState.Idle }
                uploadPendingShots(pendingUploads)
            }
        }

        fun stopListening() {
            // Spec 153: listenState is NOT set to Idle here any more - the
            // background thread now owns that transition itself, through
            // Processing, once mic/camera cleanup and trimming actually
            // finish (see startListening's own postState calls). Setting it
            // here too used to flash straight back to Idle the instant the
            // button was tapped, even though real work (and, before this
            // spec, several more seconds of it) was still running unseen.
            listeningFlag = false
        }

        // Camera is requested alongside the microphone but its result is never blocking -
        // spec 140: a declined camera permission just means this session records no video.
        val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
            if (granted[Manifest.permission.RECORD_AUDIO] == true) startListening()
            else errorText = "Mikrofonin lupa puuttuu. Salli mikrofoni asetuksista."
        }

        fun onToggleClick() {
            if (listenState != ListenState.Idle) {
                stopListening()
                return
            }
            val hasMic = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
            val hasCamera = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
            if (hasMic && hasCamera) {
                startListening()
            } else {
                permission.launch(arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA))
            }
        }

        val isListening = listenState != ListenState.Idle
        // Drives the scrolling: real time keeps advancing via spectro.snapshot()'s own
        // nowS even between shots, so this timer, not a per-sample redraw, is what scrolls it.
        LaunchedEffect(isListening) {
            if (!isListening) {
                liveSnapshot = null
                return@LaunchedEffect
            }
            while (true) {
                liveSpectrogram?.let { liveSnapshot = it.snapshot() }
                delay(SPECTRO_TICK_MS)
            }
        }

        Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
            // Spec 139: hamburger menu (Tilastot / Versio) and the Dev mode switch,
            // top-left and top-right - locked while listening, same as the rink map.
            Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { menuExpanded = true }) { Text("☰", fontSize = 22.sp) }
                DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
                    DropdownMenuItem(text = { Text("Tilastot") }, onClick = { menuExpanded = false; screen = Screen.Statistics })
                    DropdownMenuItem(text = { Text("Asetukset") }, onClick = { menuExpanded = false; screen = Screen.Settings })
                    DropdownMenuItem(text = { Text("Versio") }, onClick = { menuExpanded = false; showVersionDialog = true })
                }
                Spacer(Modifier.weight(1f))
                Text("Dev mode", fontSize = 13.sp)
                Switch(checked = devMode, onCheckedChange = { devMode = it }, enabled = listenState == ListenState.Idle)
            }

            if (screen is Screen.Statistics) {
                StatisticsScreen(history = history, onBack = { screen = Screen.Main })
            } else if (screen is Screen.Settings) {
                SettingsScreen(
                    context = this@MainActivity, targetFps = targetFps,
                    onTargetFpsChange = { targetFps = it; Prefs.setTargetFps(this@MainActivity, it) },
                    onBack = { screen = Screen.Main },
                )
            } else {
                Text("Ammuntapaikka: ${Rink.describe(distanceM)}", fontSize = 16.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(8.dp))
                RinkMap(
                    selectedDistanceM = distanceM,
                    enabled = listenState == ListenState.Idle,
                    onPick = { distanceM = it },
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(12.dp))

                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    when (val s = listenState) {
                        is ListenState.Idle -> Text("Ei kuunnella.", textAlign = TextAlign.Center)
                        is ListenState.Listening -> Text("Kuunnellaan laukausta…", fontSize = 18.sp, textAlign = TextAlign.Center)
                        is ListenState.AwaitingHit -> Text("Kuunnellaan osumaa…", fontSize = 18.sp, textAlign = TextAlign.Center)
                        is ListenState.Processing -> {
                            val percent = if (s.total > 0) (100 * s.done / s.total) else 100
                            Text("Käsitellään videoita… $percent %", fontSize = 18.sp, textAlign = TextAlign.Center)
                            Spacer(Modifier.height(4.dp))
                            LinearProgressIndicator(
                                progress = { if (s.total > 0) s.done.toFloat() / s.total else 1f },
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                    if (isListening) {
                        // Spec 149: video before the spectrogram (was the other way around) -
                        // matches the cloud gallery's own single-shot view, which bakes the
                        // spectrogram band underneath the video into one composed file (see
                        // server/android_compose.py) and can't easily do it the other way
                        // around; ShotDetailDialog below was reordered to match too.
                        //
                        // Spec 146: "pushing Aloita starts the video, too... show the video
                        // preview, too" - visual confirmation the camera is actually being
                        // recorded, not just the audio side. Absent (no box at all) when the
                        // session has no camera - same as every other optional-video path.
                        previewHolder?.let { holder ->
                            if (previewUnavailable) {
                                // Spec 149: above 30fps this phone's camera HAL corrupts most
                                // of the recorded video if a live preview shares the same
                                // high-speed capture burst - CameraSession.open drops the
                                // preview surface rather than lose the recording, so there's
                                // nothing to show here. A short caption instead of a silent
                                // blank box, so it doesn't look like something broke.
                                Text(
                                    "Esikatselu ei ole käytössä tällä kuvataajuudella - tallennus jatkuu normaalisti.",
                                    fontSize = 13.sp,
                                    color = Color.Gray,
                                )
                            } else {
                                // Fixed height, not a full square (spec 146 v1 tried that - it
                                // pushed "Lopeta kuuntelu" and Historia off the bottom of the
                                // screen, since this Column doesn't scroll while listening).
                                // 200dp is a compromise: recognisable as a live feed without
                                // dominating a layout that's already sharing space with the rink
                                // map and the spectrogram.
                                CameraPreview(holder, modifier = Modifier.fillMaxWidth().height(200.dp))
                            }
                            Spacer(Modifier.height(8.dp))
                        }
                        val snap = liveSnapshot
                        val rate = liveSpectrogram?.sampleRate
                        if (snap != null && rate != null) {
                            SpectrogramCanvas(
                                columns = snap.columns, columnTimesS = snap.columnTimesS, nowS = snap.nowS,
                                sampleRate = rate, visibleSeconds = SPECTRO_VISIBLE_S, markers = markers,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Spacer(Modifier.height(8.dp))
                        }
                    }
                    lastResult?.let { r ->
                        Spacer(Modifier.height(4.dp))
                        Text(
                            r.speedKmh?.let { "Nopeus: ${Math.round(it)} km/h" } ?: "Osumaa ei kuulunut.",
                            fontSize = 32.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center,
                        )
                    }
                    if (errorText.isNotEmpty()) {
                        Spacer(Modifier.height(4.dp))
                        Text(errorText, textAlign = TextAlign.Center)
                    }
                    Button(
                        onClick = ::onToggleClick,
                        enabled = listenState !is ListenState.Processing,
                        modifier = Modifier.fillMaxWidth().padding(top = 12.dp).height(64.dp),
                    ) {
                        val label = when (listenState) {
                            is ListenState.Idle -> "Aloita"
                            is ListenState.Processing -> "Käsitellään…"
                            else -> "Lopeta kuuntelu"
                        }
                        Text(label, fontSize = 20.sp)
                    }
                }

                Spacer(Modifier.height(16.dp))
                Text("Historia", fontSize = 16.sp, fontWeight = FontWeight.Bold)
                HorizontalDivider(Modifier.padding(vertical = 4.dp))
                if (visibleHistory.isEmpty()) {
                    Text("Ei vielä laukauksia.")
                } else {
                    LazyColumn(modifier = Modifier.weight(1f)) {
                        items(visibleHistory.asReversed()) { record -> HistoryRow(record, onClick = { selectedShot = record }) }
                    }
                }
            }
        }

        selectedShot?.let { record ->
            ShotDetailDialog(
                record = record, context = this@MainActivity,
                nextRecord = nextInHistory(record),
                autoAdvance = autoAdvance, onAutoAdvanceChange = { autoAdvance = it },
                playbackSpeed = playbackSpeed, onPlaybackSpeedChange = { playbackSpeed = it },
                onAdvance = { selectedShot = it },
                onDismiss = { selectedShot = null },
            )
        }
        if (showVersionDialog) VersionDialog(this@MainActivity, onDismiss = { showVersionDialog = false })
    }
}

private val HISTORY_DATE_FORMAT = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)

@Composable
private fun HistoryRow(record: ShotRecord, onClick: () -> Unit) {
    Row(modifier = Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 6.dp)) {
        Column(modifier = Modifier.weight(1f)) {
            Row {
                Text(HISTORY_DATE_FORMAT.format(Date(record.timestampMs)), fontSize = 13.sp)
                if (record.isDev) {
                    Text(
                        " DEV", fontSize = 11.sp, fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
            Text(record.place, fontSize = 12.sp)
        }
        Text(
            record.speedKmh?.let { "${Math.round(it)} km/h" } ?: "—",
            fontSize = 16.sp, fontWeight = FontWeight.Bold,
        )
    }
}

@Composable
private fun StatisticsScreen(history: List<ShotRecord>, onBack: () -> Unit) {
    var period by remember { mutableStateOf(StatsPeriod.DAY) }
    // Dev-mode shots never leave the current session's in-memory list, but keep them
    // out of the numbers even so - Stats is meant to show real progress over time.
    val stats = remember(history, period) { Stats.aggregate(history.filter { !it.isDev }, period) }

    Column(modifier = Modifier.fillMaxSize()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("← Takaisin") }
            Text("Tilastot", fontSize = 18.sp, fontWeight = FontWeight.Bold)
        }
        Row(modifier = Modifier.padding(vertical = 4.dp)) {
            for (p in StatsPeriod.entries) {
                TextButton(onClick = { period = p }, enabled = period != p) { Text(p.label) }
            }
        }
        HorizontalDivider(Modifier.padding(vertical = 4.dp))
        if (stats.isEmpty()) {
            Text("Ei vielä tilastoja.")
        } else {
            LazyColumn(modifier = Modifier.weight(1f)) {
                items(stats.asReversed()) { s -> StatsRow(s) }
            }
        }
    }
}

@Composable
private fun StatsRow(s: PeriodStats) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Text(s.label, fontWeight = FontWeight.Bold, fontSize = 15.sp)
        Text(
            "Laukauksia: ${s.count}   Min: ${Math.round(s.minKmh)}   Med: ${Math.round(s.medianKmh)}   " +
                "Ka: ${Math.round(s.avgKmh)}   Max: ${Math.round(s.maxKmh)} km/h",
            fontSize = 13.sp,
        )
    }
    HorizontalDivider()
}

/**
 * Video capture fps (spec 141): [FpsOptions.CANDIDATES], each shown
 * enabled only if [CameraSession.supportedFps] says this device's back
 * camera reaches it through a normal capture session - the setting
 * takes effect from the next "Aloita kuuntelu" (it's read into
 * [MainActivity.ShotScreen]'s `startListening` the same way distanceM
 * and devMode already are).
 *
 * A disabled option here can genuinely mean two different things (spec
 * 144): the camera hardware itself has no faster mode at all, or - as
 * turned out to be the actual case on this app's own OnePlus 10T test
 * phone - the hardware *can* go faster but only through Camera2's
 * constrained-high-speed session, which needs an opaque output surface
 * this app's YUV-based JPEG capture can't provide (see [CameraSession]'s
 * own doc comment). Either way the practical answer for this screen is
 * the same - this app can't currently deliver it - so the label doesn't
 * try to distinguish the two, but says "sovelluksessa" (in the app) so
 * it doesn't over-claim it's the phone's own limit.
 */
@Composable
private fun SettingsScreen(context: Context, targetFps: Int, onTargetFpsChange: (Int) -> Unit, onBack: () -> Unit) {
    val supported = remember { CameraSession.supportedFps(context) }

    Column(modifier = Modifier.fillMaxSize()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("← Takaisin") }
            Text("Asetukset", fontSize = 18.sp, fontWeight = FontWeight.Bold)
        }
        HorizontalDivider(Modifier.padding(vertical = 4.dp))
        Text("Videon kuvataajuus", fontSize = 15.sp, fontWeight = FontWeight.Bold)
        Text(
            "Suurempi kuvataajuus näyttää mailan taipumisen tarkemmin. Puhelin saattaa " +
                "tukea vain osaa vaihtoehdoista.",
            fontSize = 12.sp,
        )
        Spacer(Modifier.height(4.dp))
        for (fps in FpsOptions.CANDIDATES) {
            val isSupported = supported.contains(fps)
            Row(
                modifier = Modifier.fillMaxWidth()
                    .clickable(enabled = isSupported) { onTargetFpsChange(fps) }
                    .padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = targetFps == fps, onClick = { onTargetFpsChange(fps) }, enabled = isSupported)
                Text(
                    "$fps kuvaa/s" + if (!isSupported) " (ei vielä tuettu sovelluksessa)" else "",
                    fontSize = 14.sp,
                )
            }
        }
        if (supported.isEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text("Kameraa ei löytynyt - video jää pois, ääni toimii silti.", fontSize = 12.sp)
        }
    }
}

@Composable
private fun VersionDialog(context: Context, onDismiss: () -> Unit) {
    val info = remember {
        try {
            val pkg = context.packageManager.getPackageInfo(context.packageName, 0)
            val code = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                pkg.longVersionCode
            } else {
                @Suppress("DEPRECATION") pkg.versionCode.toLong()
            }
            "Versio ${pkg.versionName} ($code)"
        } catch (e: Exception) {
            "Versiotietoja ei saatavilla."
        }
    }
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = MaterialTheme.shapes.medium) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text("Snapshot", fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Text(info)
                Spacer(Modifier.height(12.dp))
                Button(onClick = onDismiss, modifier = Modifier.fillMaxWidth()) { Text("Sulje") }
            }
        }
    }
}

// Spec 142: the moving playback-position line on a shot's spectrogram - distinct
// from the fixed "Laukaus"/hit markers (no label, just a line) and driven by the
// same currentTimeS that also picks the shown video frame and the audio position.
private val PLAYHEAD_MARKER_COLOR = Color(0xFFFFFFFF)

// Spec 146: 1.0x is what specs 142/143 always played at - the rest are new.
private val PLAYBACK_SPEEDS = listOf(0.1f, 0.25f, 0.5f, 1.0f, 2.0f)

/**
 * A tapped history row's spectrogram, audio and (if the shot has one)
 * video - all on one playback position (spec 142), starting as soon as
 * the shot is opened and optionally carrying on into [nextRecord] when
 * it finishes (spec 143 - "kun käyttäjä valitsee historia-listasta
 * elementin, video alkaa toistua automaattisesti"; the auto-advance
 * switch is "toista automaattisesti historiaa myös aina seuraavan
 * itemin"). Pressing play advances the audio, the shown video frame,
 * and a moving line on the spectrogram together; a frame-step or ±1s
 * press seeks the audio and moves the spectrogram line to match, rather
 * than each of those living in its own little bubble the way spec
 * 139/140 first built them.
 */
@Composable
private fun ShotDetailDialog(
    record: ShotRecord,
    context: Context,
    nextRecord: ShotRecord?,
    autoAdvance: Boolean,
    onAutoAdvanceChange: (Boolean) -> Unit,
    playbackSpeed: Float,
    onPlaybackSpeedChange: (Float) -> Unit,
    onAdvance: (ShotRecord) -> Unit,
    onDismiss: () -> Unit,
) {
    val snippetFile = remember(record) { record.snippetFile?.let { File(ShotFiles.recordingsDir(context), it) } }
    val stem = remember(record) { record.snippetFile?.removeSuffix(".wav") }
    var wav by remember(record) { mutableStateOf<ShotFiles.Wav?>(null) }
    var loadFailed by remember(record) { mutableStateOf(false) }
    // Spec 153: was frameFiles/frameTimesS, a whole JPEG-per-frame sequence
    // extractVideoSnippet decoded ahead of time (~7.7s for one 480-frame
    // shot on this hardware - see that function's own doc comment on why
    // that's gone). videoFile is the same trimmed MP4 that's uploaded to
    // the cloud gallery, played here directly instead.
    val videoFile = remember(record) { stem?.let { File(ShotFiles.recordingsDir(context), "$it.mp4") }?.takeIf { it.isFile } }
    // A fixed hardware property of this phone's own back camera (see
    // CameraSession.sensorOrientation's own doc comment) - queried once,
    // not per record, since it can't change between shots.
    val sensorOrientation = remember { CameraSession.sensorOrientation(context) }
    var videoMeta by remember(record) { mutableStateOf<VideoDecoder.VideoMeta?>(null) }
    var videoSurface by remember(record) { mutableStateOf<Surface?>(null) }
    var videoPlayer by remember(record) { mutableStateOf<MediaPlayer?>(null) }
    var mediaPlayer by remember(record) { mutableStateOf<MediaPlayer?>(null) }
    var playing by remember(record) { mutableStateOf(false) }
    // The one playback clock: what the spectrogram line, and (if any) the shown
    // video frame, are both derived from - not each control's own local state.
    var currentTimeS by remember(record) { mutableStateOf(0.0) }
    // Spec 152: the speed row (five TextButtons) pushed "Sulje" almost off
    // the bottom of a phone screen - a dropdown behind one small button
    // takes the same vertical space as any other single control row.
    var speedMenuExpanded by remember { mutableStateOf(false) }

    // The completion listener below is set once per record but must act on
    // whichever nextRecord/autoAdvance are current when playback actually
    // finishes (autoAdvance in particular can be toggled mid-playback) - a plain
    // captured parameter would freeze at whatever it was when the listener was
    // created, so these read through rememberUpdatedState instead.
    val latestAutoAdvance = rememberUpdatedState(autoAdvance)
    val latestNextRecord = rememberUpdatedState(nextRecord)
    val latestOnAdvance = rememberUpdatedState(onAdvance)

    LaunchedEffect(record) {
        if (snippetFile == null) {
            loadFailed = true
            return@LaunchedEffect
        }
        val loaded = try {
            withContext(Dispatchers.IO) { ShotFiles.readWav(snippetFile) }
        } catch (e: Exception) {
            Log.w(TAG, "ShotDetailDialog: could not read ${snippetFile.name}", e)
            null
        }
        if (loaded == null) loadFailed = true else wav = loaded
    }

    LaunchedEffect(record) {
        val file = videoFile ?: return@LaunchedEffect
        videoMeta = withContext(Dispatchers.IO) { VideoDecoder.probeVideoMeta(file) }
    }

    // Spec 153: the video-only MediaPlayer, created once both the trimmed
    // MP4 and the TextureView's own Surface (below, in the Dialog content)
    // are ready - whichever of those two things happens second. Silent
    // (the MP4 has no audio track - trimToMp4 only ever muxes video), kept
    // in step with mediaPlayer (the WAV, which IS audible) by every
    // togglePlayback/seekToS call below rather than by its own completion
    // listener or position polling.
    LaunchedEffect(record, videoSurface) {
        val file = videoFile ?: return@LaunchedEffect
        val surface = videoSurface ?: return@LaunchedEffect
        val player = try {
            MediaPlayer().apply {
                setDataSource(file.absolutePath)
                setSurface(surface)
                prepare()
                seekTo((currentTimeS * 1000).toInt())
            }
        } catch (e: Exception) {
            Log.w(TAG, "ShotDetailDialog: could not prepare video player for ${file.name}", e)
            null
        }
        videoPlayer = player
    }

    // Spec 153: keyed on record ALONE, not (record, videoSurface) - videoSurface
    // itself changes once per record (null -> the real Surface, right after the
    // TextureView reports it's ready), and keying a DisposableEffect on a value
    // means its onDispose fires on every change of that value, reading the
    // CURRENT (already-updated) state at dispose time, not a snapshot of what
    // it was when the effect started. Keyed on videoSurface too, this fired the
    // instant the surface transitioned from null to real, immediately released
    // that brand-new surface (confirmed on-device: MediaPlayer.setSurface threw
    // "IllegalArgumentException: The surface has been released" right after),
    // before the LaunchedEffect above ever got to use it. Only release once,
    // when the dialog actually moves on from this record - same shape as the
    // WAV player's own DisposableEffect(record) just above.
    DisposableEffect(record) {
        onDispose {
            videoPlayer?.release()
            videoSurface?.release()
        }
    }

    // Prepared (and, spec 143, started) as soon as the shot opens - not lazily
    // on first play tap (spec 139's original approach). Deliberately NOT on
    // Dispatchers.IO: MediaPlayer's completion callback is delivered on the
    // thread that constructed it, which needs a Looper - the IO dispatcher's
    // threads don't have one, so building it there would silently break
    // setOnCompletionListener (and with it, auto-advance) even though prepare()
    // itself would succeed. The file is a small local WAV, so preparing it
    // inline is a few ms at most, same as spec 139's original main-thread call.
    LaunchedEffect(record) {
        if (snippetFile == null) return@LaunchedEffect
        val player = try {
            MediaPlayer().apply {
                setDataSource(snippetFile.absolutePath)
                prepare()
            }
        } catch (e: Exception) {
            Log.w(TAG, "ShotDetailDialog: could not prepare player for ${snippetFile.name}", e)
            null
        }
        player?.setOnCompletionListener {
            playing = false
            currentTimeS = 0.0
            player.seekTo(0)
            val next = latestNextRecord.value
            if (latestAutoAdvance.value && next != null) latestOnAdvance.value(next)
        }
        mediaPlayer = player
        if (player != null) {
            player.start()
            playing = true
        }
    }

    // One MediaPlayer per dialog instance - released whenever a different shot is
    // shown or the dialog closes, never left playing in the background.
    DisposableEffect(record) {
        onDispose { mediaPlayer?.release() }
    }

    // While playing, the audio clock is the single source of truth for both the
    // shown video frame and the spectrogram's moving line - polled rather than
    // driven by a fixed frame interval, since MediaPlayer is what's actually audible.
    LaunchedEffect(playing, mediaPlayer) {
        val player = mediaPlayer ?: return@LaunchedEffect
        if (!playing) return@LaunchedEffect
        while (true) {
            currentTimeS = player.currentPosition / 1000.0
            delay(33L)
        }
    }

    // Spec 146: applied both when the player first becomes available and whenever
    // the speed selector changes while already playing - pitch pinned to 1.0 so
    // slow motion doesn't also drop the audio an octave. Very low speeds (0.1x) or
    // very high ones aren't guaranteed by every device/decoder, so this is a
    // best-effort - a rejection just leaves the previous speed in effect. Spec
    // 153: applied to videoPlayer too (no pitch to pin - it's silent).
    LaunchedEffect(playbackSpeed, mediaPlayer) {
        val player = mediaPlayer ?: return@LaunchedEffect
        try {
            player.playbackParams = PlaybackParams().setSpeed(playbackSpeed).setPitch(1f)
        } catch (e: Exception) {
            Log.w(TAG, "ShotDetailDialog: playback speed $playbackSpeed rejected", e)
        }
    }
    LaunchedEffect(playbackSpeed, videoPlayer) {
        val player = videoPlayer ?: return@LaunchedEffect
        try {
            player.playbackParams = PlaybackParams().setSpeed(playbackSpeed)
        } catch (e: Exception) {
            Log.w(TAG, "ShotDetailDialog: video playback speed $playbackSpeed rejected", e)
        }
    }

    // Spec 153: drives videoPlayer alongside mediaPlayer (the WAV) - two
    // independently clocked MediaPlayers can't be guaranteed frame-locked
    // during continuous playback, but these clips are only a couple of
    // seconds long, both start from the exact same position every time,
    // and neither is under real-time pressure from anything else, so drift
    // isn't perceptible in practice. Every manual seek re-anchors both to
    // the same target anyway.
    fun togglePlayback() {
        val player = mediaPlayer ?: return
        if (playing) {
            player.pause()
            videoPlayer?.pause()
            playing = false
        } else {
            player.seekTo((currentTimeS * 1000).toInt())
            videoPlayer?.seekTo((currentTimeS * 1000).toInt())
            player.start()
            videoPlayer?.start()
            playing = true
        }
    }

    // Pauses (a manual step always takes over from auto-play) and seeks the
    // audio, video and playback clock together, so nothing ever drifts from
    // what the next "play" would actually be audible/visible from.
    fun seekToS(targetS: Double, durationS: Double) {
        playing = false
        mediaPlayer?.pause()
        videoPlayer?.pause()
        val clamped = targetS.coerceIn(0.0, durationS)
        currentTimeS = clamped
        mediaPlayer?.seekTo((clamped * 1000).toInt())
        videoPlayer?.seekTo((clamped * 1_000_000).toLong(), MediaPlayer.SEEK_CLOSEST)
    }

    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = MaterialTheme.shapes.medium) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text(HISTORY_DATE_FORMAT.format(Date(record.timestampMs)), fontWeight = FontWeight.Bold)
                Text(record.place, fontSize = 13.sp)
                Spacer(Modifier.height(4.dp))
                Text(
                    record.speedKmh?.let { "${Math.round(it)} km/h" } ?: "Osumaa ei kuulunut.",
                    fontSize = 24.sp, fontWeight = FontWeight.Bold,
                )
                Spacer(Modifier.height(8.dp))
                val loadedWav = wav
                when {
                    loadedWav != null -> {
                        val columns = remember(loadedWav) { Spectrogram.columns(loadedWav.samples) }
                        val times = remember(columns) { List(columns.size) { it * Spectrogram.FFT_HOP.toDouble() / loadedWav.sampleRate } }
                        val durationS = loadedWav.samples.size.toDouble() / loadedWav.sampleRate
                        // Snippet-relative offsets: saveSnippet trimmed the WAV starting at this same startS.
                        val startS = (record.shotT - PREROLL_S).coerceAtLeast(0.0)
                        val fixedMarkers = remember(record) {
                            listOfNotNull(
                                SpectroMarker(record.shotT - startS, "Laukaus", SHOT_MARKER_COLOR),
                                record.hitT?.let {
                                    val label = record.speedKmh?.let { s -> "${Math.round(s)} km/h" } ?: "Osuma"
                                    SpectroMarker(it - startS, label, HIT_MARKER_COLOR)
                                },
                            )
                        }
                        val markers = fixedMarkers + SpectroMarker(currentTimeS, "", PLAYHEAD_MARKER_COLOR)

                        // Spec 149: video before the spectrogram (was the other way around) -
                        // matches the cloud gallery's own single-shot view, which bakes the
                        // spectrogram band underneath the video into one composed file (see
                        // server/android_compose.py); the live listening screen above was
                        // reordered to match too.
                        //
                        // Spec 152: no Spacer between video and spectrogram any more (they
                        // now sit flush against each other, like the cloud's own composed
                        // video does) and the transport buttons moved below the spectrogram
                        // instead of between it and the video - both purely to reclaim
                        // vertical space, since "Sulje" was ending up almost off the bottom
                        // of the screen otherwise.
                        val meta = videoMeta
                        if (videoFile != null && meta != null) {
                            VideoPlaybackSurface(
                                videoWidth = meta.width, videoHeight = meta.height, sensorOrientation = sensorOrientation,
                                modifier = Modifier.fillMaxWidth().aspectRatio(1f).background(Color.Black),
                                onSurfaceReady = { videoSurface = it },
                            )
                            val frameIndex = (currentTimeS * meta.fps).roundToInt().coerceIn(0, meta.frameCount - 1)
                            Text("ruutu ${frameIndex + 1}/${meta.frameCount}   ${"%.3f".format(frameIndex / meta.fps.toDouble())} s", fontSize = 12.sp)
                        } else if (videoFile != null) {
                            // Trimmed but not probed yet (or probing failed) - a blank box the
                            // right shape rather than nothing, so the layout doesn't jump once
                            // videoMeta does arrive a moment later.
                            Box(modifier = Modifier.fillMaxWidth().aspectRatio(1f).background(Color.Black))
                        }
                        SpectrogramCanvas(columns, times, durationS, loadedWav.sampleRate, durationS, markers, Modifier.fillMaxWidth())
                        Spacer(Modifier.height(4.dp))
                        if (videoFile != null && meta != null) {
                            val frameS = 1.0 / meta.fps
                            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                TextButton(onClick = { seekToS(currentTimeS - 1.0, durationS) }) { Text("-1 s") }
                                TextButton(onClick = { seekToS(currentTimeS - frameS, durationS) }) { Text("◀") }
                                TextButton(onClick = ::togglePlayback) { Text(if (playing) "⏸" else "▶") }
                                TextButton(onClick = { seekToS(currentTimeS + frameS, durationS) }) { Text("▶|") }
                                TextButton(onClick = { seekToS(currentTimeS + 1.0, durationS) }) { Text("+1 s") }
                            }
                        } else {
                            // No video for this shot (camera unavailable that session, or it
                            // predates spec 140) - audio alone, but still drives the spectrogram line.
                            Button(onClick = ::togglePlayback, modifier = Modifier.fillMaxWidth()) {
                                Text(if (playing) "⏸ Pysäytä" else "▶ Toista ääni")
                            }
                        }
                        // Spec 146: "make it possible to playback videos with speed 0.1,
                        // 0.25, 0.5, 1 and 2" - applies to audio-only shots too, since the
                        // spectrogram line is worth slowing down even without video. Spec
                        // 152: a dropdown behind one button, not five TextButtons in a row -
                        // same reasoning as the reordering above.
                        Box {
                            TextButton(onClick = { speedMenuExpanded = true }) {
                                Text("Nopeus: ${playbackSpeed}x")
                            }
                            DropdownMenu(expanded = speedMenuExpanded, onDismissRequest = { speedMenuExpanded = false }) {
                                for (speed in PLAYBACK_SPEEDS) {
                                    DropdownMenuItem(
                                        text = {
                                            Text(
                                                "${speed}x",
                                                fontWeight = if (playbackSpeed == speed) FontWeight.Bold else FontWeight.Normal,
                                            )
                                        },
                                        onClick = { onPlaybackSpeedChange(speed); speedMenuExpanded = false },
                                    )
                                }
                            }
                        }
                    }
                    loadFailed -> Text("Äänitiedostoa ei löytynyt.")
                    else -> Text("Ladataan…")
                }
                Spacer(Modifier.height(8.dp))
                // Spec 143: "toistaa automaattisesti historiaa myös aina seuraavan
                // itemin" - when a shot finishes playing, jump straight to the next
                // one in History (same order the list shows) and it starts itself.
                Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text("Jatka automaattisesti seuraavaan", fontSize = 13.sp, modifier = Modifier.weight(1f))
                    Switch(checked = autoAdvance, onCheckedChange = onAutoAdvanceChange)
                }
                Spacer(Modifier.height(4.dp))
                Button(onClick = onDismiss, modifier = Modifier.fillMaxWidth()) { Text("Sulje") }
            }
        }
    }
}

