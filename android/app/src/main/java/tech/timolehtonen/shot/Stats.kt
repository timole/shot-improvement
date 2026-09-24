package tech.timolehtonen.shot

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.IsoFields
import java.util.Locale

/** A grouping granularity for the statistics screen (spec 139). */
enum class StatsPeriod(val label: String) {
    DAY("Päivä"), WEEK("Viikko"), MONTH("Kuukausi")
}

/** One period's speeds, oldest-period-first when a list of these is built by [Stats.aggregate]. */
data class PeriodStats(
    val label: String,
    val count: Int,
    val minKmh: Double,
    val medianKmh: Double,
    val maxKmh: Double,
    val avgKmh: Double,
)

/**
 * Groups shots into calendar days/ISO weeks/months and summarises each
 * group's speeds (spec 139) - "does the speed rise over time" is the
 * whole point, so unpaired shots (no speedKmh) don't contribute a row of
 * their own but are otherwise ignored here, same as they always were
 * absent from any speed number in the app.
 */
object Stats {
    private val zone: ZoneId = ZoneId.systemDefault()

    internal fun periodKey(timestampMs: Long, period: StatsPeriod, zone: ZoneId = Stats.zone): String {
        val date = Instant.ofEpochMilli(timestampMs).atZone(zone).toLocalDate()
        return when (period) {
            StatsPeriod.DAY -> date.format(DateTimeFormatter.ISO_LOCAL_DATE)
            StatsPeriod.WEEK -> {
                val week = date.get(IsoFields.WEEK_OF_WEEK_BASED_YEAR)
                val year = date.get(IsoFields.WEEK_BASED_YEAR)
                String.format(Locale.ROOT, "%04d-W%02d", year, week)
            }
            StatsPeriod.MONTH -> date.format(DateTimeFormatter.ofPattern("yyyy-MM", Locale.ROOT))
        }
    }

    /** Oldest period first. */
    fun aggregate(records: List<ShotRecord>, period: StatsPeriod, zone: ZoneId = Stats.zone): List<PeriodStats> {
        val speedsByKey = LinkedHashMap<String, MutableList<Double>>()
        for (r in records) {
            val speed = r.speedKmh ?: continue
            speedsByKey.getOrPut(periodKey(r.timestampMs, period, zone)) { ArrayList() }.add(speed)
        }
        return speedsByKey.entries.sortedBy { it.key }.map { (key, speeds) ->
            val sorted = speeds.sorted()
            PeriodStats(
                label = key,
                count = sorted.size,
                minKmh = sorted.first(),
                maxKmh = sorted.last(),
                medianKmh = median(sorted),
                avgKmh = sorted.average(),
            )
        }
    }

    private fun median(sorted: List<Double>): Double {
        val n = sorted.size
        val mid = n / 2
        return if (n % 2 == 1) sorted[mid] else (sorted[mid - 1] + sorted[mid]) / 2.0
    }
}
