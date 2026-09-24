package tech.timolehtonen.shot

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class GrowableShortBufferTest {
    @Test
    fun readsBackWhatWasAppendedAcrossChunkBoundaries() {
        val buf = GrowableShortBuffer(chunkSamples = 4)
        buf.append(shortArrayOf(1, 2, 3, 4, 5, 6, 7), 7) // spans two 4-sample chunks, one partial

        assertEquals(7L, buf.totalSamples)
        for (i in 0 until 7) assertEquals((i + 1).toShort(), buf.get(i.toLong()))
    }

    @Test
    fun appendCanBeCalledManyTimesWithChunksThatDoNotDivideEvenly() {
        val buf = GrowableShortBuffer(chunkSamples = 3)
        buf.append(shortArrayOf(10, 20), 2)
        buf.append(shortArrayOf(30, 40, 50, 60), 4)
        buf.append(shortArrayOf(70), 1)

        assertEquals(7L, buf.totalSamples)
        assertArrayEquals(shortArrayOf(10, 20, 30, 40, 50, 60, 70), buf.snapshot(0, 7))
    }

    @Test
    fun snapshotClampsToWhatHasBeenWritten() {
        val buf = GrowableShortBuffer(chunkSamples = 4)
        buf.append(shortArrayOf(1, 2, 3), 3)

        assertArrayEquals(shortArrayOf(1, 2, 3), buf.snapshot(-5, 100))
        assertArrayEquals(ShortArray(0), buf.snapshot(10, 20))
    }

    @Test
    fun rmsAtMatchesAManualComputation() {
        val buf = GrowableShortBuffer(chunkSamples = 8)
        val samples = shortArrayOf(3, 4, 0, 0) // 3-4-5 triangle: rms of (3,4) padded... use a clean case
        buf.append(samples, samples.size)

        val expected = kotlin.math.sqrt((9.0 + 16.0 + 0.0 + 0.0) / 4)
        assertEquals(expected, buf.rmsAt(0, 4), 1e-9)
    }
}
