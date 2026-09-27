package tech.timolehtonen.shot

/** Presentation timestamps, not frameIndex/fps: trimmed MP4s need not be uniform. */
object FrameTimeline {
    /** MediaPlayer takes milliseconds; MediaExtractor uses microseconds. */
    fun playerPositionMs(timeS: Double): Long = kotlin.math.round(timeS * 1000).toLong()
    fun indexAt(times: List<Double>, timeS: Double): Int {
        if (times.isEmpty()) return 0
        val found = times.binarySearch(timeS)
        if (found >= 0) return found
        val upper = -found - 1
        if (upper == 0) return 0
        if (upper == times.size) return times.lastIndex
        return if (timeS - times[upper - 1] <= times[upper] - timeS) upper - 1 else upper
    }

    fun step(times: List<Double>, timeS: Double, direction: Int): Double {
        if (times.isEmpty()) return timeS
        var index = indexAt(times, timeS)
        val currentMs = playerPositionMs(times[index])
        val delta = if (direction < 0) -1 else 1
        // Legacy remuxed clips pin decoder preroll frames to microseconds
        // near zero. They cannot be separately sought through MediaPlayer.
        do {
            val next = index + delta
            if (next !in times.indices) break
            index = next
        } while (playerPositionMs(times[index]) == currentMs)
        return times[index]
    }
}
