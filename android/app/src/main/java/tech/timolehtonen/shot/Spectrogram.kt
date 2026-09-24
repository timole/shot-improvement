package tech.timolehtonen.shot

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.log10
import kotlin.math.pow

/**
 * Turns audio into a spectrogram (spec 138): a column of dB magnitudes per
 * frequency bin, one column per hop. Shared by the live, continuously
 * scrolling view (while "Aloita kuuntelu" runs) and the static one shown
 * for a saved shot (from its WAV snippet).
 */
object Spectrogram {
    const val FFT_WINDOW = 512 // same power-of-two family as Claps.SHOT_BAND_WINDOW, reuses Claps.fft
    const val FFT_HOP = 128
    const val DYNAMIC_RANGE_DB = 80.0

    // A fixed reference level (full-scale int16), not each column's own max: a live view
    // renormalising itself every frame would visibly flicker in brightness as the max jumps around.
    private val REFERENCE_DB = 20.0 * log10(32768.0)
    private val FLOOR_DB = REFERENCE_DB - DYNAMIC_RANGE_DB

    private val hann = DoubleArray(FFT_WINDOW) { 0.5 - 0.5 * cos(2.0 * PI * it / (FFT_WINDOW - 1)) }

    /** One column's dB magnitude at [start, start+FFT_WINDOW) of [audio], bin 0 = DC. */
    fun column(audio: ShortArray, start: Int): DoubleArray {
        val re = DoubleArray(FFT_WINDOW)
        val im = DoubleArray(FFT_WINDOW)
        for (i in 0 until FFT_WINDOW) re[i] = audio[start + i] * hann[i]
        Claps.fft(re, im)
        return DoubleArray(FFT_WINDOW / 2 + 1) { k -> 20.0 * log10(hypot(re[k], im[k]) + 1e-6) }
    }

    /** Every column of a whole clip, oldest first - for a saved shot's (short, static) spectrogram. */
    fun columns(audio: ShortArray): List<DoubleArray> {
        if (audio.size < FFT_WINDOW) return emptyList()
        val n = (audio.size - FFT_WINDOW) / FFT_HOP + 1
        return (0 until n).map { column(audio, it * FFT_HOP) }
    }

    /** dB -> [0, 1], clipped, against the fixed reference (not per-column) - see [REFERENCE_DB]. */
    fun normalize(db: Double): Double = ((db - FLOOR_DB) / DYNAMIC_RANGE_DB).coerceIn(0.0, 1.0)

    // A small inferno-like gradient (dark -> purple -> red -> orange -> pale yellow) - not aiming
    // to match core/spectrogram.py's viridis exactly, just to read as "a spectrogram" at a glance.
    private val STOPS = intArrayOf(
        0xFF0A0A1E.toInt(), 0xFF280A64.toInt(), 0xFFA01E5A.toInt(), 0xFFFA8C1E.toInt(), 0xFFFFFFBE.toInt(),
    )

    /** ARGB colour for a normalised [0,1] value. */
    fun color(normalized: Double): Int {
        val t = normalized.coerceIn(0.0, 1.0) * (STOPS.size - 1)
        val i = t.toInt().coerceAtMost(STOPS.size - 2)
        val frac = t - i
        val a = STOPS[i]
        val b = STOPS[i + 1]
        fun mix(shift: Int): Int {
            val av = (a shr shift) and 0xFF
            val bv = (b shr shift) and 0xFF
            return (av + (bv - av) * frac).toInt().coerceIn(0, 255)
        }
        return (0xFF shl 24) or (mix(16) shl 16) or (mix(8) shl 8) or mix(0)
    }

    /** dB magnitude straight to colour - the common case at render time. */
    fun colorForDb(db: Double): Int = color(normalize(db))
}

/**
 * The live, scrolling spectrogram: fed audio chunks as they arrive (same
 * calling convention as LiveDetector.feed - called from the same mic-read
 * loop, on the same background thread), keeps only the last
 * [visibleSeconds] worth of columns. [snapshot] is read from the UI/
 * Compose side on a timer (~10 fps), not on every feed - the display
 * scrolls because [Snapshot.nowS] keeps advancing with real time, not
 * because anything is redrawn per sample.
 */
class LiveSpectrogram(val sampleRate: Int, visibleSeconds: Double = 6.0) {
    val maxColumns: Int = (visibleSeconds * sampleRate / Spectrogram.FFT_HOP).toInt().coerceAtLeast(8)

    data class Snapshot(val columns: List<DoubleArray>, val columnTimesS: List<Double>, val nowS: Double)

    private var carry = ShortArray(0) // unconsumed tail: fewer than FFT_WINDOW samples, always
    private var totalSamples = 0L
    private var processedUpTo = 0L
    private val columns = ArrayDeque<DoubleArray>()
    private val columnTimesS = ArrayDeque<Double>()

    @Synchronized
    fun feed(chunk: ShortArray, length: Int) {
        val merged = ShortArray(carry.size + length)
        System.arraycopy(carry, 0, merged, 0, carry.size)
        System.arraycopy(chunk, 0, merged, carry.size, length)
        totalSamples += length

        var consumed = 0
        while (merged.size - consumed >= Spectrogram.FFT_WINDOW) {
            columns.addLast(Spectrogram.column(merged, consumed))
            columnTimesS.addLast(processedUpTo.toDouble() / sampleRate)
            processedUpTo += Spectrogram.FFT_HOP
            consumed += Spectrogram.FFT_HOP
            if (columns.size > maxColumns) {
                columns.removeFirst()
                columnTimesS.removeFirst()
            }
        }
        carry = if (consumed > 0) merged.copyOfRange(consumed, merged.size) else merged
    }

    @Synchronized
    fun snapshot(): Snapshot = Snapshot(columns.toList(), columnTimesS.toList(), totalSamples.toDouble() / sampleRate)
}
