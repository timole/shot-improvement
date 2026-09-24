package tech.timolehtonen.shot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class ShotHistoryTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun file(): File = File(tmp.root, "shots.jsonl")

    @Test
    fun aWrittenShotRoundTripsExactly() {
        val record = ShotRecord(
            timestampMs = 1_700_000_000_000, distanceM = 22.5, place = "Sinisestä viivasta päätyyn (22,5 m)",
            shotT = 3.104, hitT = 3.981, speedKmh = 104.2,
        )
        ShotHistory.append(file(), record)

        assertEquals(listOf(record), ShotHistory.loadAll(file()))
    }

    @Test
    fun anUnpairedShotStoresNullHitAndSpeed() {
        val record = ShotRecord(
            timestampMs = 1_700_000_000_000, distanceM = 22.5, place = "Sinisestä viivasta päätyyn (22,5 m)",
            shotT = 3.104, hitT = null, speedKmh = null,
        )
        ShotHistory.append(file(), record)

        val loaded = ShotHistory.loadAll(file()).single()
        assertEquals(null, loaded.hitT)
        assertEquals(null, loaded.speedKmh)
    }

    @Test
    fun multipleShotsAppendInOrderAndAllRoundTrip() {
        val a = ShotRecord(1L, 22.5, "a", 1.0, 1.9, 100.0)
        val b = ShotRecord(2L, 6.0, "b", 2.0, null, null)
        val f = file()
        ShotHistory.append(f, a)
        ShotHistory.append(f, b)

        assertEquals(listOf(a, b), ShotHistory.loadAll(f))
    }

    @Test
    fun missingFileLoadsAsEmpty() = assertTrue(ShotHistory.loadAll(File(tmp.root, "does-not-exist.jsonl")).isEmpty())

    @Test
    fun aMalformedLineIsSkippedNotFatal() {
        val f = file()
        f.writeText("not json\n")
        ShotHistory.append(f, ShotRecord(1L, 22.5, "a", 1.0, 1.9, 100.0))

        assertEquals(1, ShotHistory.loadAll(f).size)
    }

    @Test
    fun theSnippetFilenameRoundTrips() {
        val record = ShotRecord(1L, 22.5, "a", 1.0, 1.9, 100.0, snippetFile = "shot-20260922114523-1.wav")
        ShotHistory.append(file(), record)

        assertEquals("shot-20260922114523-1.wav", ShotHistory.loadAll(file()).single().snippetFile)
    }

    @Test
    fun aLineWrittenBeforeSnippetFilesExistedLoadsWithNullSnippet() {
        val f = file()
        f.writeText("""{"timestampMs":1,"distanceM":22.5,"place":"a","shotT":1.0,"hitT":1.9,"speedKmh":100.0}""" + "\n")

        assertEquals(null, ShotHistory.loadAll(f).single().snippetFile)
    }

    @Test
    fun aDevShotPersistsAndRoundTripsIsDevTrue() {
        val record = ShotRecord(1L, 22.5, "a", 1.0, 1.9, 100.0, isDev = true)
        ShotHistory.append(file(), record)

        assertEquals(true, ShotHistory.loadAll(file()).single().isDev)
    }

    @Test
    fun aLineWrittenBeforeIsDevExistedLoadsAsNotDev() {
        val f = file()
        f.writeText("""{"timestampMs":1,"distanceM":22.5,"place":"a","shotT":1.0,"hitT":1.9,"speedKmh":100.0}""" + "\n")

        assertEquals(false, ShotHistory.loadAll(f).single().isDev)
    }
}
