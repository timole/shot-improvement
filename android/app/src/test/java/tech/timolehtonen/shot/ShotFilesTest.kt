package tech.timolehtonen.shot

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class ShotFilesTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test
    fun aWrittenWavReadsBackWithTheSameSamplesAndRate() {
        val file = File(tmp.root, "clip.wav")
        val samples = shortArrayOf(0, 1000, -1000, 32767, -32768, 5)

        ShotFiles.writeWav(file, samples, 44100)
        val wav = ShotFiles.readWav(file)

        assertEquals(44100, wav.sampleRate)
        assertArrayEquals(samples, wav.samples)
    }

    @Test
    fun anEmptyClipRoundTrips() {
        val file = File(tmp.root, "empty.wav")

        ShotFiles.writeWav(file, ShortArray(0), 44100)
        val wav = ShotFiles.readWav(file)

        assertEquals(0, wav.samples.size)
    }
}
