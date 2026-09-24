package tech.timolehtonen.shot

/**
 * An append-only sample buffer for one continuous listening session:
 * fixed-size chunks (no realloc-and-copy like a growing array would need),
 * random access by absolute sample index, and a contiguous snapshot for
 * the spectral gate / snippet saving. Never trims - a session is bounded
 * by LiveDetector's own session-length cap, not by this buffer.
 */
class GrowableShortBuffer(private val chunkSamples: Int) {
    private val chunks = ArrayList<ShortArray>()
    private var tail = ShortArray(chunkSamples)
    private var tailFill = 0

    val totalSamples: Long get() = chunks.size.toLong() * chunkSamples + tailFill

    fun append(src: ShortArray, length: Int) {
        var offset = 0
        while (offset < length) {
            val room = chunkSamples - tailFill
            val n = minOf(room, length - offset)
            System.arraycopy(src, offset, tail, tailFill, n)
            tailFill += n
            offset += n
            if (tailFill == chunkSamples) {
                chunks.add(tail)
                tail = ShortArray(chunkSamples)
                tailFill = 0
            }
        }
    }

    fun get(index: Long): Short {
        val chunkIdx = (index / chunkSamples).toInt()
        val within = (index % chunkSamples).toInt()
        return if (chunkIdx < chunks.size) chunks[chunkIdx][within] else tail[within]
    }

    /** A copy of [start, endExclusive), clamped to what has been written. */
    fun snapshot(start: Long, endExclusive: Long): ShortArray {
        val from = start.coerceIn(0, totalSamples)
        val to = endExclusive.coerceIn(from, totalSamples)
        val out = ShortArray((to - from).toInt())
        for (i in out.indices) out[i] = get(from + i)
        return out
    }

    /** RMS over [start, start+window) without materialising an array - called once per ~5.8 ms window. */
    fun rmsAt(start: Long, window: Int): Double {
        var sumSq = 0L
        for (i in 0 until window) {
            val s = get(start + i).toLong()
            sumSq += s * s
        }
        return kotlin.math.sqrt(sumSq.toDouble() / window)
    }
}
