package tech.timolehtonen.shot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneOffset
import java.time.ZonedDateTime

class StatsTest {
    private val utc = ZoneOffset.UTC

    private fun ms(iso: String) = ZonedDateTime.parse(iso).toInstant().toEpochMilli()

    private fun shot(iso: String, speed: Double?) = ShotRecord(
        timestampMs = ms(iso), distanceM = 22.5, place = "x", shotT = 1.0, hitT = if (speed != null) 1.9 else null, speedKmh = speed,
    )

    @Test
    fun groupsByCalendarDay() {
        val records = listOf(
            shot("2026-09-20T10:00:00Z", 80.0),
            shot("2026-09-20T22:00:00Z", 90.0),
            shot("2026-09-21T09:00:00Z", 100.0),
        )

        val stats = Stats.aggregate(records, StatsPeriod.DAY, utc)

        assertEquals(listOf("2026-09-20", "2026-09-21"), stats.map { it.label })
        assertEquals(2, stats[0].count)
        assertEquals(1, stats[1].count)
    }

    @Test
    fun computesMinMedianMaxAndAverage() {
        val records = listOf(
            shot("2026-09-20T10:00:00Z", 70.0),
            shot("2026-09-20T11:00:00Z", 90.0),
            shot("2026-09-20T12:00:00Z", 100.0),
        )

        val day = Stats.aggregate(records, StatsPeriod.DAY, utc).single()

        assertEquals(3, day.count)
        assertEquals(70.0, day.minKmh, 1e-9)
        assertEquals(90.0, day.medianKmh, 1e-9) // odd count: the middle value
        assertEquals(100.0, day.maxKmh, 1e-9)
        assertEquals(260.0 / 3, day.avgKmh, 1e-9)
    }

    @Test
    fun medianAveragesTheTwoMiddleValuesForAnEvenCount() {
        val records = listOf(shot("2026-09-20T10:00:00Z", 70.0), shot("2026-09-20T11:00:00Z", 90.0))

        val day = Stats.aggregate(records, StatsPeriod.DAY, utc).single()

        assertEquals(80.0, day.medianKmh, 1e-9)
    }

    @Test
    fun unpairedShotsDoNotContributeARowOrCountTowardOne() {
        val records = listOf(shot("2026-09-20T10:00:00Z", null))

        assertTrue(Stats.aggregate(records, StatsPeriod.DAY, utc).isEmpty())
    }

    @Test
    fun anEmptyHistoryGivesNoRows() {
        assertTrue(Stats.aggregate(emptyList(), StatsPeriod.DAY, utc).isEmpty())
        assertTrue(Stats.aggregate(emptyList(), StatsPeriod.WEEK, utc).isEmpty())
        assertTrue(Stats.aggregate(emptyList(), StatsPeriod.MONTH, utc).isEmpty())
    }

    @Test
    fun groupsByIsoWeek() {
        // 2026-09-20 is a Sunday (end of ISO week 38); 2026-09-21 is a Monday (start of week 39).
        val records = listOf(shot("2026-09-20T10:00:00Z", 80.0), shot("2026-09-21T10:00:00Z", 90.0))

        val stats = Stats.aggregate(records, StatsPeriod.WEEK, utc)

        assertEquals(listOf("2026-W38", "2026-W39"), stats.map { it.label })
    }

    @Test
    fun groupsByMonth() {
        val records = listOf(shot("2026-09-30T23:00:00Z", 80.0), shot("2026-10-01T01:00:00Z", 90.0))

        val stats = Stats.aggregate(records, StatsPeriod.MONTH, utc)

        assertEquals(listOf("2026-09", "2026-10"), stats.map { it.label })
    }

    @Test
    fun periodsAreOrderedOldestFirst() {
        val records = listOf(
            shot("2026-09-22T10:00:00Z", 80.0),
            shot("2026-09-20T10:00:00Z", 90.0),
            shot("2026-09-21T10:00:00Z", 100.0),
        )

        val stats = Stats.aggregate(records, StatsPeriod.DAY, utc)

        assertEquals(listOf("2026-09-20", "2026-09-21", "2026-09-22"), stats.map { it.label })
    }
}
