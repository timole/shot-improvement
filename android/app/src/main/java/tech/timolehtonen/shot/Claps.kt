package tech.timolehtonen.shot

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Port of core/claps.py: finds loud percussive events ("claps") - the puck
 * leaving the stick and the puck hitting the end boards - and pairs them.
 * Constant names mirror the Python module so re-tuning is a literal diff.
 *
 * Deliberate differences from the desktop detector:
 *  - the spectral gate uses a fixed 8192-sample window (185.8 ms @ 44.1 kHz)
 *    instead of 0.15 s = 6615 samples, which is not a power of two;
 *  - the noise floor is stated in dBFS, not in one webcam's raw counts, and
 *    a peak must also stand out from the local median (PROMINENCE_RATIO);
 *  - the pairing window and speed come from Geometry (mic at the shooter).
 */
data class Clap(val timeS: Double, val peakRms: Double)

object Claps {
    const val RMS_WINDOW = 256
    const val STARTUP_SKIP_S = 0.3
    const val THRESHOLD_MEDIAN_RATIO = 5.0

    /** Full-scale-relative floor (500/32768 on the desktop = -36 dBFS; phone mics are quieter). */
    const val FLOOR_DBFS = -45.0
    const val PROMINENCE_RATIO = 6.0
    const val PROMINENCE_HALF_SPAN_S = 1.0

    /** Merges a stick "windup" tap (0.36-0.43 s before release) into its shot, yet is below the fastest shot's 0.543 s gap. */
    const val MIN_SEPARATION_S = 0.45
    const val SHOT_BAND_LOW_HZ = 2000.0
    const val SHOT_BAND_HIGH_HZ = 8000.0
    const val SHOT_BAND_WINDOW = 8192
    const val SHOT_BAND_FRACTION_MIN = 0.15
    const val MAX_CLAPS_PER_SECOND = 2.0

    private const val FULL_SCALE = 32768.0

    fun floorRms(): Double = FULL_SCALE * 10.0.pow(FLOOR_DBFS / 20.0)

    /** Per-window RMS and each window's start time; non-overlapping, so the hop is [RMS_WINDOW]/sampleRate. */
    fun rmsEnvelope(audio: ShortArray, sampleRate: Int, window: Int = RMS_WINDOW): Pair<DoubleArray, DoubleArray> {
        val n = audio.size / window
        val rms = DoubleArray(n)
        val times = DoubleArray(n)
        for (i in 0 until n) {
            var sumSq = 0L
            val base = i * window
            for (j in 0 until window) {
                val s = audio[base + j].toLong()
                sumSq += s * s
            }
            rms[i] = sqrt(sumSq.toDouble() / window)
            times[i] = i.toDouble() * window / sampleRate
        }
        return Pair(rms, times)
    }

    /** In-place iterative radix-2 Cooley-Tukey FFT; size must be a power of two. */
    internal fun fft(re: DoubleArray, im: DoubleArray) {
        val n = re.size
        var j = 0
        for (i in 1 until n) {
            var bit = n shr 1
            while (j and bit != 0) {
                j = j xor bit
                bit = bit shr 1
            }
            j = j xor bit
            if (i < j) {
                val tr = re[i]; re[i] = re[j]; re[j] = tr
                val ti = im[i]; im[i] = im[j]; im[j] = ti
            }
        }
        var len = 2
        while (len <= n) {
            val ang = -2.0 * PI / len
            val wr = cos(ang)
            val wi = sin(ang)
            var i = 0
            while (i < n) {
                var cr = 1.0
                var ci = 0.0
                for (k in 0 until len / 2) {
                    val a = i + k
                    val b = a + len / 2
                    val xr = re[b] * cr - im[b] * ci
                    val xi = re[b] * ci + im[b] * cr
                    re[b] = re[a] - xr; im[b] = im[a] - xi
                    re[a] += xr; im[a] += xi
                    val ncr = cr * wr - ci * wi
                    ci = cr * wi + ci * wr
                    cr = ncr
                }
                i += len
            }
            len = len shl 1
        }
    }

    /**
     * Fraction of the FFT magnitude in [SHOT_BAND_LOW_HZ, SHOT_BAND_HIGH_HZ)
     * over a [SHOT_BAND_WINDOW]-sample Hann window centred on [centerT].
     * Near a clip's edge the window is shortened (and zero-padded); 0.0 when
     * it is too short to mean anything.
     */
    fun shotBandFraction(audio: ShortArray, sampleRate: Int, centerT: Double): Double {
        val n = SHOT_BAND_WINDOW
        val centerI = (centerT * sampleRate).toInt()
        val start = max(0, centerI - n / 2)
        val end = min(audio.size, centerI + n / 2)
        val len = end - start
        if (len < 8) return 0.0
        val re = DoubleArray(n)
        val im = DoubleArray(n)
        for (i in 0 until len) {
            val hann = 0.5 - 0.5 * cos(2.0 * PI * i / (len - 1))
            re[i] = audio[start + i] * hann
        }
        fft(re, im)
        var total = 0.0
        var band = 0.0
        for (k in 0..n / 2) {
            val mag = sqrt(re[k] * re[k] + im[k] * im[k])
            val f = k.toDouble() * sampleRate / n
            total += mag
            if (f >= SHOT_BAND_LOW_HZ && f < SHOT_BAND_HIGH_HZ) band += mag
        }
        return if (total <= 0.0) 0.0 else band / total
    }

    /** numpy.median semantics: mean of the two middle values for an even count. */
    internal fun median(values: DoubleArray): Double {
        if (values.isEmpty()) return 0.0
        val s = values.sortedArray()
        val m = s.size / 2
        return if (s.size % 2 == 1) s[m] else (s[m - 1] + s[m]) / 2.0
    }

    /** Detected events sorted by time. See core/claps.py for the reasoning behind each step. */
    fun detectClaps(audio: ShortArray, sampleRate: Int, minSeparationS: Double = MIN_SEPARATION_S): List<Clap> {
        val (rms, times) = rmsEnvelope(audio, sampleRate)
        if (rms.isEmpty()) return emptyList()

        val validIdx = times.indices.filter { times[it] >= STARTUP_SKIP_S }
        if (validIdx.isEmpty()) return emptyList()
        val validRms = DoubleArray(validIdx.size) { rms[validIdx[it]] }
        val median = median(validRms)
        val threshold = max(floorRms(), median * THRESHOLD_MEDIAN_RATIO)

        val candidates = validIdx.filter { rms[it] > threshold }
            .sortedWith(compareByDescending<Int> { rms[it] }.thenBy { it })
        if (candidates.isEmpty()) return emptyList()

        val hop = RMS_WINDOW.toDouble() / sampleRate
        val halfSpan = (PROMINENCE_HALF_SPAN_S / hop).toInt()

        // Non-maximum suppression. A peak the gates reject still suppresses its
        // neighbours, exactly like the Python loop.
        val centers = ArrayList<Double>()
        val claps = ArrayList<Clap>()
        for (idx in candidates) {
            val t = times[idx]
            if (centers.any { abs(it - t) <= minSeparationS }) continue
            centers.add(t)
            if (shotBandFraction(audio, sampleRate, t) < SHOT_BAND_FRACTION_MIN) continue
            val lo = max(0, idx - halfSpan)
            val hi = min(rms.size, idx + halfSpan + 1)
            val local = median(rms.copyOfRange(lo, hi))
            if (rms[idx] < PROMINENCE_RATIO * local) continue
            claps.add(Clap(t, rms[idx]))
        }
        claps.sortBy { it.timeS }

        val durationS = times.last()
        if (durationS > 0 && claps.size / durationS > MAX_CLAPS_PER_SECOND) return emptyList()
        return claps
    }

    /**
     * Every shot's (shotT, hitT), chronologically; hitT is null when no later
     * clap lands within the window. Greedy, like core/claps.py.
     */
    fun pairClaps(
        clapTimes: List<Double>,
        window: Pair<Double, Double> = Geometry.hitDelayWindow(),
    ): List<Pair<Double, Double?>> {
        val (minDelay, maxDelay) = window
        val shots = ArrayList<Pair<Double, Double?>>()
        var pending: Double? = null
        for (t in clapTimes) {
            val p = pending
            if (p != null && t - p in minDelay..maxDelay) {
                shots.add(Pair(p, t))
                pending = null
                continue
            }
            if (p != null) shots.add(Pair(p, null))
            pending = t
        }
        pending?.let { shots.add(Pair(it, null)) }
        return shots
    }
}
