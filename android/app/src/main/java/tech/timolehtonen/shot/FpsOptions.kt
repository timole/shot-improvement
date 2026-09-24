package tech.timolehtonen.shot

/** A capture frame-rate range, decoupled from android.util.Range so the
 * "which of the candidate targets can this camera reach" logic (spec 141)
 * is plain, unit-testable Kotlin - CameraSession converts to/from Camera2's
 * own Range<Int> at the point it actually talks to the camera. */
data class FpsRange(val lower: Int, val upper: Int)

/**
 * The frame-rate choices the Settings screen offers (spec 141). 30fps
 * (and 60fps, on hardware whose *normal* session range reaches it) works
 * through Camera2's ordinary repeating-request API; higher rates
 * normally need the constrained-high-speed capture session
 * (CameraConstrainedHighSpeedCaptureSession) instead. Spec 144 found
 * that session type refuses to configure with a YUV_420_888 ImageReader
 * (confirmed on real hardware) - only an opaque PRIVATE-format surface
 * works, which is exactly what a MediaRecorder's input surface is. Spec
 * 145 records through MediaRecorder for that reason, so
 * [needsHighSpeedSession] and the high-speed half of
 * [bestRange]/[supportedFps] are back in real use (spec 144 had
 * temporarily zeroed out the high-speed lists callers passed in, since
 * nothing could reach them yet).
 */
object FpsOptions {
    val CANDIDATES = listOf(30, 60, 120, 240)
    // 120, not 60: on real hardware tested so far, a "60fps" request has never
    // resolved to an actual 60fps range - only fixed 120/240/480 high-speed
    // ranges exist, so bestRange already rounds 60 up to 120 in practice. 120
    // as the literal default is the honest version of the same outcome.
    const val DEFAULT_FPS = 120

    /** Which of [CANDIDATES] this camera can actually reach: an ordinary
     * range covers it, or (for the higher ones, typically) a high-speed
     * range does. */
    fun supportedFps(normalRanges: List<FpsRange>, highSpeedRanges: List<FpsRange>): List<Int> =
        CANDIDATES.filter { target -> normalRanges.any { it.upper >= target } || highSpeedRanges.any { it.upper >= target } }

    /** The best available range for a requested target: the tightest
     * ordinary range that reaches it, else the tightest high-speed range
     * that reaches it, else whichever range (of either kind) gets
     * highest - never returns null unless both lists are empty. */
    fun bestRange(target: Int, normalRanges: List<FpsRange>, highSpeedRanges: List<FpsRange>): FpsRange? {
        normalRanges.filter { it.upper >= target }.minByOrNull { it.upper }?.let { return it }
        highSpeedRanges.filter { it.upper >= target }.minByOrNull { it.upper }?.let { return it }
        return (normalRanges + highSpeedRanges).maxByOrNull { it.upper }
    }

    /** Whether reaching [target] needs the constrained-high-speed session
     * rather than an ordinary repeating request - true only when no
     * ordinary range reaches it but a high-speed one does. */
    fun needsHighSpeedSession(target: Int, normalRanges: List<FpsRange>, highSpeedRanges: List<FpsRange>): Boolean {
        if (normalRanges.any { it.upper >= target }) return false
        return highSpeedRanges.any { it.upper >= target }
    }

    /** Smaller frames at higher fps keep both the encoded recording's bitrate
     * (spec 145 - a whole session's worth of 480fps video adds up fast) and
     * a shot's decoded JPEGs reasonably sized, without the extra resolution
     * adding anything a phone screen's own stepping UI can actually show. */
    fun targetSizePxFor(fps: Int): Int = when {
        fps <= 60 -> 480
        fps <= 120 -> 320
        else -> 240
    }
}
