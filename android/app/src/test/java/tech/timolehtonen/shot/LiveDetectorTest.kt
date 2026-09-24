package tech.timolehtonen.shot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random
import kotlin.math.exp

/**
 * Feeds a synthetic clip into LiveDetector in small chunks - the way a
 * real AudioRecord.read() loop would - and checks its events against
 * what the offline Claps.detectClaps/pairClaps would find on the whole
 * clip at once, plus the streaming-only behaviour (timeouts, the
 * distance-dependent refractory shrink) that has no offline equivalent.
 */
class LiveDetectorTest {
    private val rate = 44100
    private val chunkSize = 4096 // a realistic AudioRecord read() size

    private fun synth(events: List<Double>, durationS: Double, burstRms: Double = 8000.0, burstMs: Double = 15.0): ShortArray {
        val rng = Random(0)
        val n = (durationS * rate).toInt()
        val audio = DoubleArray(n) { rng.nextGaussian() * 100.0 }
        val burstLen = (burstMs / 1000 * rate).toInt()
        for (t in events) {
            val start = (t * rate).toInt()
            for (i in 0 until burstLen) {
                if (start + i >= n) break
                audio[start + i] += burstRms * exp(-6.0 * i / (burstLen - 1)) * (if (rng.nextBoolean()) 1 else -1)
            }
        }
        return ShortArray(n) { audio[it].coerceIn(-32768.0, 32767.0).toInt().toShort() }
    }

    private fun feedAll(detector: LiveDetector, audio: ShortArray): List<LiveDetector.Event> {
        val events = ArrayList<LiveDetector.Event>()
        var offset = 0
        val chunk = ShortArray(chunkSize)
        while (offset < audio.size) {
            val n = minOf(chunkSize, audio.size - offset)
            System.arraycopy(audio, offset, chunk, 0, n)
            events += detector.feed(chunk, n)
            offset += n
        }
        return events
    }

    private fun measuredDt(distanceM: Double, kmh: Double) = distanceM / (kmh / 3.6) + distanceM / Geometry.SPEED_OF_SOUND_MS

    @Test
    fun detectsAShotThenItsHitLiveInOrder() {
        val distance = 22.5
        val dt = measuredDt(distance, 100.0)
        val audio = synth(listOf(1.0, 1.0 + dt), 4.0)

        val events = feedAll(LiveDetector(rate, distance), audio)

        assertEquals(2, events.size)
        val shot = events[0] as LiveDetector.Event.Shot
        val hit = events[1] as LiveDetector.Event.Hit
        assertEquals(1.0, shot.timeS, 0.03)
        assertEquals(100.0, hit.speedKmh!!, 1.5)
        assertEquals(shot.timeS, hit.shotTimeS, 1e-9)
    }

    @Test
    fun aShotWithNoHitEventuallyTimesOutAsUnpaired() {
        val distance = 22.5
        val audio = synth(listOf(1.0), 6.0) // nothing else in the clip

        val events = feedAll(LiveDetector(rate, distance), audio)

        assertEquals(1.0, (events.first() as LiveDetector.Event.Shot).timeS, 0.03)
        val unpaired = events.last() as LiveDetector.Event.Unpaired
        assertEquals(1.0, unpaired.shotTimeS, 0.03)
    }

    @Test
    fun twoSeparateShotHitPairsAreBothReportedInASingleSession() {
        val distance = 22.5
        val dt1 = measuredDt(distance, 90.0)
        val dt2 = measuredDt(distance, 120.0)
        val audio = synth(listOf(1.0, 1.0 + dt1, 6.0, 6.0 + dt2), 10.0)

        val events = feedAll(LiveDetector(rate, distance), audio)
        val hits = events.filterIsInstance<LiveDetector.Event.Hit>()

        assertEquals(2, hits.size)
        assertEquals(90.0, hits[0].speedKmh!!, 1.5)
        assertEquals(120.0, hits[1].speedKmh!!, 1.5)
    }

    @Test
    fun aWindupTapIsMergedIntoItsShotJustLikeOffline() {
        // 0.40s apart, both loud - the offline detector merges these into one event (ClapsTest.mergesAWindupTapIntoItsShot).
        val distance = 22.5
        val audio = synth(listOf(1.00, 1.40, 1.40 + measuredDt(distance, 100.0)), 4.0)

        val events = feedAll(LiveDetector(rate, distance), audio)

        assertEquals(1, events.filterIsInstance<LiveDetector.Event.Shot>().size)
        assertEquals(1, events.filterIsInstance<LiveDetector.Event.Hit>().size)
    }

    @Test
    fun aShortDistanceShrinksTheRefractoryPeriodSoAFastHitStillPairs() {
        // core/claps.py spec 128: a short shot's hit can follow within the
        // fixed 0.45 s refractory - min_separation_s must shrink with distance.
        val distance = 10.0
        val dt = measuredDt(distance, 90.0)
        assertTrue("fixture assumes a gap under the fixed refractory", dt < Claps.MIN_SEPARATION_S)
        val audio = synth(listOf(1.0, 1.0 + dt), 4.0)

        val hits = feedAll(LiveDetector(rate, distance), audio).filterIsInstance<LiveDetector.Event.Hit>()

        assertEquals(1, hits.size)
        assertEquals(90.0, hits[0].speedKmh!!, 2.0)
    }

    @Test
    fun silenceProducesNoEvents() {
        assertTrue(feedAll(LiveDetector(rate, 22.5), ShortArray(rate * 3)).isEmpty())
    }

    @Test
    fun aClapDuringStartupSkipIsIgnored() {
        val audio = synth(listOf(0.05, 1.0), 3.0, burstRms = 15000.0)

        val events = feedAll(LiveDetector(rate, 22.5), audio)

        assertTrue(events.all { it !is LiveDetector.Event.Shot || it.timeS >= Claps.STARTUP_SKIP_S })
    }

    @Test
    fun matchesTheOfflineDetectorsEventTimesOnTheSameClip() {
        val distance = 22.5
        val dt = measuredDt(distance, 105.0)
        val audio = synth(listOf(1.5, 1.5 + dt), 5.0)

        val offline = Claps.pairClaps(Claps.detectClaps(audio, rate).map { it.timeS }, Geometry.hitDelayWindow(distance)).single()
        val live = feedAll(LiveDetector(rate, distance), audio)
        val liveHit = live.filterIsInstance<LiveDetector.Event.Hit>().single()

        assertEquals(offline.first, liveHit.shotTimeS, 0.03)
        assertEquals(offline.second!!, liveHit.hitTimeS, 0.03)
    }
}
