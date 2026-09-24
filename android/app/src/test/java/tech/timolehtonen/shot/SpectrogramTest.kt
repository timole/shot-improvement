package tech.timolehtonen.shot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.PI
import kotlin.math.sin

class SpectrogramTest {
    private val rate = 44100

    private fun tone(hz: Double, durationS: Double, amplitude: Double = 20000.0): ShortArray {
        val n = (durationS * rate).toInt()
        return ShortArray(n) { (amplitude * sin(2 * PI * hz * it / rate)).toInt().toShort() }
    }

    @Test
    fun columnPutsATonesEnergyInTheExpectedBin() {
        val hz = 3000.0
        val audio = tone(hz, 0.1)
        val col = Spectrogram.column(audio, 0)

        val expectedBin = (hz / (rate / 2.0) * (Spectrogram.FFT_WINDOW / 2)).toInt()
        val peakBin = col.indices.maxByOrNull { col[it] }!!
        assertTrue("expected bin near $expectedBin, got $peakBin", kotlin.math.abs(expectedBin - peakBin) <= 2)
    }

    @Test
    fun columnsCoversAWholeClipAtTheExpectedHop() {
        val audio = tone(1000.0, 0.5)
        val columns = Spectrogram.columns(audio)

        val expected = (audio.size - Spectrogram.FFT_WINDOW) / Spectrogram.FFT_HOP + 1
        assertEquals(expected, columns.size)
        assertTrue(columns.all { it.size == Spectrogram.FFT_WINDOW / 2 + 1 })
    }

    @Test
    fun columnsIsEmptyForAudioShorterThanOneWindow() {
        assertTrue(Spectrogram.columns(ShortArray(10)).isEmpty())
    }

    @Test
    fun normalizeClampsToZeroOneAndIsMonotonic() {
        assertEquals(0.0, Spectrogram.normalize(-1000.0), 1e-9) // far below floor
        assertEquals(1.0, Spectrogram.normalize(1000.0), 1e-9) // far above reference
        assertTrue(Spectrogram.normalize(50.0) < Spectrogram.normalize(80.0))
    }

    @Test
    fun colorIsOpaqueAndBrightensWithLouderInput() {
        val quiet = Spectrogram.color(0.0)
        val loud = Spectrogram.color(1.0)
        assertEquals(0xFF, (quiet ushr 24) and 0xFF) // fully opaque
        fun luma(c: Int) = ((c shr 16 and 0xFF) + (c shr 8 and 0xFF) + (c and 0xFF))
        assertTrue(luma(loud) > luma(quiet))
    }

    // --- LiveSpectrogram ---------------------------------------------------------

    @Test
    fun liveSpectrogramProducesOneColumnPerHopAsAudioStreams() {
        val spectro = LiveSpectrogram(rate, visibleSeconds = 6.0)
        val audio = tone(2000.0, 0.2) // 8820 samples

        var offset = 0
        val chunk = ShortArray(2048)
        while (offset < audio.size) {
            val n = minOf(chunk.size, audio.size - offset)
            System.arraycopy(audio, offset, chunk, 0, n)
            spectro.feed(chunk, n)
            offset += n
        }

        val snap = spectro.snapshot()
        val expectedColumns = (audio.size - Spectrogram.FFT_WINDOW) / Spectrogram.FFT_HOP + 1
        assertEquals(expectedColumns, snap.columns.size)
        assertEquals(audio.size / rate.toDouble(), snap.nowS, 1e-9)
    }

    @Test
    fun liveSpectrogramNeverKeepsMoreThanItsVisibleWindowOfColumns() {
        val spectro = LiveSpectrogram(rate, visibleSeconds = 1.0) // maxColumns ~ 344
        val audio = tone(1500.0, 3.0) // far more than 1 s of columns

        val chunk = ShortArray(4096)
        var offset = 0
        while (offset < audio.size) {
            val n = minOf(chunk.size, audio.size - offset)
            System.arraycopy(audio, offset, chunk, 0, n)
            spectro.feed(chunk, n)
            offset += n
        }

        val snap = spectro.snapshot()
        assertEquals(spectro.maxColumns, snap.columns.size)
        assertEquals(spectro.maxColumns, snap.columnTimesS.size)
        // evenly spaced by exactly one hop, oldest first
        val hopS = Spectrogram.FFT_HOP.toDouble() / rate
        for (i in 1 until snap.columnTimesS.size) {
            assertEquals(hopS, snap.columnTimesS[i] - snap.columnTimesS[i - 1], 1e-9)
        }
        // the newest column's own window doesn't reach past what has actually been fed
        assertTrue(snap.columnTimesS.last() + Spectrogram.FFT_WINDOW.toDouble() / rate <= snap.nowS + 1e-9)
    }

    @Test
    fun liveSpectrogramHandlesFeedsSmallerThanOneHop() {
        // A real AudioRecord.read() can return fewer samples than expected - one sample at a time
        // must still eventually accumulate into columns, not crash or drop data.
        val spectro = LiveSpectrogram(rate)
        val audio = tone(1000.0, 0.05)
        for (s in audio) spectro.feed(shortArrayOf(s), 1)

        val snap = spectro.snapshot()
        assertEquals((audio.size - Spectrogram.FFT_WINDOW) / Spectrogram.FFT_HOP + 1, snap.columns.size)
    }

    @Test
    fun liveSpectrogramWithNoAudioYetHasNoColumns() {
        assertTrue(LiveSpectrogram(rate).snapshot().columns.isEmpty())
    }
}
