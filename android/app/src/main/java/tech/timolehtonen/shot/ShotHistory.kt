package tech.timolehtonen.shot

import android.content.Context
import android.util.Log
import java.io.File

/** One shot: what the app showed, plus enough to re-derive it (spec 138). */
data class ShotRecord(
    val timestampMs: Long,
    val distanceM: Double,
    val place: String,
    val shotT: Double,
    val hitT: Double?,
    val speedKmh: Double?,
    // The saved WAV snippet's filename (recordingsDir()/snippetFile), for the per-shot
    // spectrogram view. null for a shot recorded before this field existed.
    val snippetFile: String? = null,
    // Dev-mode shot (spec 139/141): keeps it out of Stats and tags it "DEV" in the
    // history list. A shot recorded while Dev mode is on is still never appended to
    // shots.jsonl (spec 139: dev recordings don't outlive the app process) - this
    // field only round-trips for shots that *are* persisted, so an already-persisted
    // shot can be retroactively marked dev (spec 141: the test recordings made while
    // building this app were migrated this way) without deleting anything. Missing
    // from an older line on disk reads back as false, same as any other shot.
    val isDev: Boolean = false,
)

/**
 * The shot history: an append-only JSON-Lines file, one shot per line, so
 * a shot is durable the instant it is written (no read-modify-write of a
 * whole array) and a torn write can only ever corrupt its own last line.
 * Every shot is kept - nothing here is ever deleted by the app.
 *
 * Hand-written JSON, not org.json: org.json ships as a platform stub in
 * the Android unit-test jar (every method throws at runtime unless run
 * under Robolectric or on a device), so using it here would make this
 * whole module untestable in a plain JVM test. The schema is small,
 * flat and entirely ours to both write and read, so a couple of regexes
 * cover it without pulling in a JSON library.
 */
object ShotHistory {
    const val FILE_NAME = "shots.jsonl"

    fun file(context: Context): File = File(ShotFiles.recordingsDir(context), FILE_NAME)

    private fun escape(s: String) = buildString {
        for (c in s) when (c) {
            '"' -> append("\\\"")
            '\\' -> append("\\\\")
            '\n' -> append(' ')
            else -> append(c)
        }
    }

    private fun unescape(s: String) = s.replace("\\\"", "\"").replace("\\\\", "\\")

    private fun toLine(record: ShotRecord): String {
        val hit = record.hitT?.toString() ?: "null"
        val speed = record.speedKmh?.toString() ?: "null"
        val snippet = record.snippetFile?.let { "\"${escape(it)}\"" } ?: "null"
        return "{\"timestampMs\":${record.timestampMs},\"distanceM\":${record.distanceM}," +
            "\"place\":\"${escape(record.place)}\",\"shotT\":${record.shotT}," +
            "\"hitT\":$hit,\"speedKmh\":$speed,\"snippetFile\":$snippet,\"isDev\":${record.isDev}}"
    }

    private val FIELD_RE = Regex("\"(\\w+)\":(null|true|false|-?[0-9.]+(?:[eE]-?[0-9]+)?|\"(?:[^\"\\\\]|\\\\.)*\")")

    private fun parseLine(line: String): ShotRecord? {
        val fields = FIELD_RE.findAll(line).associate { it.groupValues[1] to it.groupValues[2] }
        fun num(key: String): Double? = fields[key]?.let { if (it == "null") null else it.toDoubleOrNull() }
        fun str(key: String): String? = fields[key]?.takeIf { it.startsWith("\"") }?.let { unescape(it.substring(1, it.length - 1)) }

        val timestampMs = num("timestampMs")?.toLong() ?: return null
        val distanceM = num("distanceM") ?: return null
        val shotT = num("shotT") ?: return null
        return ShotRecord(
            timestampMs = timestampMs, distanceM = distanceM, place = str("place") ?: "",
            shotT = shotT, hitT = num("hitT"), speedKmh = num("speedKmh"), snippetFile = str("snippetFile"),
            isDev = fields["isDev"] == "true",
        )
    }

    fun append(file: File, record: ShotRecord) {
        file.appendText(toLine(record) + "\n")
    }

    fun append(context: Context, record: ShotRecord) = append(file(context), record)

    /** Oldest first, as written; malformed lines (a previous version's format, a torn write) are skipped. */
    fun loadAll(file: File): List<ShotRecord> {
        if (!file.exists()) return emptyList()
        val result = ArrayList<ShotRecord>()
        file.forEachLine { line ->
            if (line.isBlank()) return@forEachLine
            val record = try {
                parseLine(line)
            } catch (e: Exception) {
                null
            }
            if (record == null) {
                Log.w(TAG, "ShotHistory: skipping malformed line: $line")
            } else {
                result.add(record)
            }
        }
        return result
    }

    fun loadAll(context: Context): List<ShotRecord> = loadAll(file(context))
}
