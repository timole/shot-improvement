package tech.timolehtonen.shot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random
import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.sin

/** Mirrors tests/test_claps.py case for case, plus the mic-at-the-shooter speed formula. */
class ClapsTest {
    private val rate = 44100

    /** Gaussian noise floor + short decaying random-sign bursts - same idea as _synth in test_claps.py. */
    private fun synth(
        events: List<Double>,
        durationS: Double,
        noiseRms: Double = 100.0,
        burstRms: Double = 8000.0,
        burstMs: Double = 15.0,
    ): ShortArray {
        val rng = Random(0)
        val n = (durationS * rate).toInt()
        val audio = DoubleArray(n) { rng.nextGaussian() * noiseRms }
        val burstLen = (burstMs / 1000 * rate).toInt()
        for (t in events) {
            val start = (t * rate).toInt()
            for (i in 0 until burstLen) {
                if (start + i >= n) break
                val decay = exp(-6.0 * i / (burstLen - 1))
                audio[start + i] += burstRms * decay * (if (rng.nextBoolean()) 1 else -1)
            }
        }
        return ShortArray(n) { audio[it].coerceIn(-32768.0, 32767.0).toInt().toShort() }
    }

    private fun times(audio: ShortArray) = Claps.detectClaps(audio, rate).map { it.timeS }

    @Test
    fun findsInjectedEventsWithinTolerance() {
        val t = times(synth(listOf(1.0, 2.0, 3.5), 5.0))
        assertEquals(3, t.size)
        listOf(1.0, 2.0, 3.5).zip(t).forEach { (e, a) -> assertEquals(e, a, 0.03) }
    }

    @Test
    fun ignoresStartupPop() {
        val t = times(synth(listOf(0.05, 1.0, 2.0), 4.0, burstRms = 15000.0))
        assertTrue(t.all { it >= Claps.STARTUP_SKIP_S })
        assertEquals(2, t.size)
        assertEquals(1.0, t[0], 0.03)
        assertEquals(2.0, t[1], 0.03)
    }

    @Test
    fun nothingForDigitalSilence() = assertTrue(times(ShortArray(rate * 3)).isEmpty())

    @Test
    fun nothingForNearSilence() {
        val rng = Random(1)
        val audio = ShortArray(rate * 3) { (rng.nextGaussian() * 5.0).toInt().toShort() }
        assertTrue(times(audio).isEmpty())
    }

    @Test
    fun mergesADecayTailIntoOneEvent() = assertEquals(1, times(synth(listOf(1.00, 1.10), 3.0)).size)

    @Test
    fun mergesAWindupTapIntoItsShot() = assertEquals(1, times(synth(listOf(1.00, 1.40), 3.0)).size)

    @Test
    fun keepsTwoEventsARealGapApart() {
        val t = times(synth(listOf(1.00, 1.70), 3.0))
        assertEquals(2, t.size)
        assertEquals(0.70, t[1] - t[0], 0.03)
    }

    @Test
    fun rejectsWallOfNoise() {
        val rng = Random(2)
        val audio = ShortArray(rate * 3) { (rng.nextGaussian() * 6000.0).toInt().toShort() }
        assertTrue(times(audio).isEmpty())
    }

    @Test
    fun rejectsLowFrequencyRumble() {
        // A loud 80 Hz thump has almost no 2-8 kHz energy: the spectral gate must drop it.
        val audio = synth(emptyList(), 3.0)
        val start = 1 * rate
        for (i in 0 until 4000) {
            val v = 12000.0 * exp(-6.0 * i / 4000) * sin(2 * PI * 80.0 * i / rate)
            audio[start + i] = (audio[start + i] + v).toInt().toShort()
        }
        assertTrue(times(audio).isEmpty())
    }

    @Test
    fun handlesEmptyAndShortInput() {
        assertTrue(times(ShortArray(0)).isEmpty())
        assertTrue(times(ShortArray(10)).isEmpty())
    }

    @Test
    fun fftPlacesASineInItsBin() {
        val n = 1024
        val re = DoubleArray(n) { sin(2 * PI * 50.0 * it / n) }
        val im = DoubleArray(n)
        Claps.fft(re, im)
        val mags = DoubleArray(n / 2) { Math.hypot(re[it], im[it]) }
        assertEquals(50, mags.indices.maxByOrNull { mags[it] })
        assertEquals(n / 2.0, mags[50], 1e-6)
    }

    @Test
    fun medianAveragesTheTwoMiddleValuesForEvenCounts() {
        assertEquals(2.5, Claps.median(doubleArrayOf(4.0, 1.0, 3.0, 2.0)), 0.0)
        assertEquals(3.0, Claps.median(doubleArrayOf(5.0, 1.0, 3.0)), 0.0)
    }

    // --- pairing --------------------------------------------------------

    @Test
    fun pairEmpty() = assertTrue(Claps.pairClaps(emptyList()).isEmpty())

    @Test
    fun pairSingleShotHasNoHit() = assertEquals(listOf(Pair(1.0, null)), Claps.pairClaps(listOf(1.0)))

    @Test
    fun pairTwoWithinWindowFormsOnePair() = assertEquals(listOf(Pair(1.0, 1.9)), Claps.pairClaps(listOf(1.0, 1.9)))

    @Test
    fun pairTooCloseOrTooFarLeavesShotsUnpaired() {
        assertEquals(listOf(Pair(1.0, null), Pair(1.3, null)), Claps.pairClaps(listOf(1.0, 1.3)))
        assertEquals(listOf(Pair(1.0, null), Pair(4.0, null)), Claps.pairClaps(listOf(1.0, 4.0)))
    }

    @Test
    fun hitDelayWindowMatchesTheDerivedBand() {
        val (lo, hi) = Geometry.hitDelayWindow()
        assertEquals(0.543, lo, 0.001)
        assertEquals(2.092, hi, 0.001)
    }

    // --- speed ----------------------------------------------------------

    private fun measuredDt(kmh: Double) = Geometry.DISTANCE_M / (kmh / 3.6) + Geometry.DISTANCE_M / Geometry.SPEED_OF_SOUND_MS

    @Test
    fun speedInvertsTheFlightPlusSoundReturnModel() {
        for (kmh in listOf(70.0, 100.0, 130.0)) {
            assertEquals(kmh, Geometry.puckSpeedKmh(1.0, 1.0 + measuredDt(kmh))!!, 1e-6)
        }
    }

    @Test
    fun omittingTheSoundTermWouldUnderestimateByAboutEightPercentAt100() {
        val naive = Geometry.DISTANCE_M / measuredDt(100.0) * 3.6
        assertEquals(92.4, naive, 0.1)
    }

    @Test
    fun speedIsNullWhenNoPlausibleFlightTimeRemains() {
        assertNull(Geometry.puckSpeedKmh(1.0, 1.05))
        assertNotNull(Geometry.puckSpeedKmh(1.0, 1.9))
    }

    @Test
    fun endToEndFromSyntheticAudio() {
        val dt = measuredDt(100.0)
        val audio = synth(listOf(1.0, 1.0 + dt), 4.0)
        val pair = Claps.pairClaps(times(audio)).single()
        val speed = Geometry.puckSpeedKmh(pair.first, pair.second!!)!!
        // 5.8 ms RMS hop on a 0.877 s gap: ~+-0.7 km/h at 100 km/h.
        assertEquals(100.0, speed, 1.5)
    }
}
