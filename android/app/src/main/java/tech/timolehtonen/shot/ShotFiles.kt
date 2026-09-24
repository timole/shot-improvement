package tech.timolehtonen.shot

import android.content.Context
import java.io.File
import java.io.RandomAccessFile
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Where captures live, and the WAV writer - shared by live snippet saving and (later) tooling. */
object ShotFiles {
    /** recordings dir under the app's external files - visible over USB/MTP, no permission needed. */
    fun recordingsDir(context: Context): File {
        val base = context.getExternalFilesDir(android.os.Environment.DIRECTORY_MUSIC) ?: context.filesDir
        return File(base, "recordings").also { it.mkdirs() }
    }

    fun timestamp(): String = SimpleDateFormat("yyyyMMddHHmmss", Locale.US).format(Date())

    /** Dev-mode snippet files ("dev-*", spec 139) and any leftover session video
     * ("tmp-video-*", spec 145 - deleted once its shots' frames are extracted,
     * so a survivor means the app was killed mid-session) are not meant to
     * outlive the app process that made them - called once at startup to sweep
     * up anything a previous run left behind. */
    fun cleanupDevFiles(context: Context) {
        recordingsDir(context).listFiles { f -> f.name.startsWith("dev-") || f.name.startsWith("tmp-video-") }
            ?.forEach { it.deleteRecursively() }
    }

    /** Where [VideoDecoder] writes a shot's frames (spec 140/145) -
     * "<stem>-frames/frame_NNNN.jpg", created if needed. */
    fun framesDir(context: Context, stem: String): File =
        File(recordingsDir(context), "$stem-frames").apply { mkdirs() }

    /** The "<stem>-frames.json" sidecar of each frame's time (seconds, relative
     * to the snippet's own first frame) - written once, after [VideoDecoder]
     * has written all of a shot's frame JPEGs into [framesDir]. */
    fun writeFrameTimesSidecar(context: Context, stem: String, frameTimesS: List<Double>) {
        val times = org.json.JSONArray()
        frameTimesS.forEach { times.put(it) }
        File(recordingsDir(context), "$stem-frames.json").writeText(org.json.JSONObject().put("frameTimesS", times).toString())
    }

    /** A saved shot's video frames and their times (seconds from the first
     * frame), or an empty pair if it has none - an old shot, one where the
     * camera was unavailable, or one with no frames in its window. */
    fun readFrames(context: Context, stem: String): Pair<List<File>, List<Double>> {
        val dir = recordingsDir(context)
        val framesDir = File(dir, "$stem-frames")
        val timesFile = File(dir, "$stem-frames.json")
        if (!framesDir.isDirectory || !timesFile.isFile) return Pair(emptyList(), emptyList())
        val files = framesDir.listFiles { f -> f.name.endsWith(".jpg") }?.sortedBy { it.name }.orEmpty()
        val times = try {
            val arr = org.json.JSONObject(timesFile.readText()).getJSONArray("frameTimesS")
            List(arr.length()) { arr.getDouble(it) }
        } catch (e: Exception) {
            emptyList()
        }
        return Pair(files, times)
    }

    /** Uncompressed PCM16 mono WAV - exactly the given samples, no normalisation. */
    fun writeWav(file: File, samples: ShortArray, sampleRate: Int) {
        val dataLen = samples.size * 2
        RandomAccessFile(file, "rw").use { f ->
            f.setLength(0)
            val header = java.nio.ByteBuffer.allocate(44).order(java.nio.ByteOrder.LITTLE_ENDIAN)
            header.put("RIFF".toByteArray()).putInt(36 + dataLen).put("WAVE".toByteArray())
            header.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
            header.putInt(sampleRate).putInt(sampleRate * 2).putShort(2).putShort(16)
            header.put("data".toByteArray()).putInt(dataLen)
            f.write(header.array())
            val body = java.nio.ByteBuffer.allocate(dataLen).order(java.nio.ByteOrder.LITTLE_ENDIAN)
            body.asShortBuffer().put(samples)
            f.write(body.array())
        }
    }

    /** One WAV's samples and sample rate. */
    class Wav(val samples: ShortArray, val sampleRate: Int)

    /**
     * Reads a WAV written by [writeWav] back: PCM16 mono only (what this
     * app ever writes), for showing a saved shot's spectrogram. Walks the
     * chunk list rather than assuming the fixed 44-byte header, so it
     * still works if a future writer adds a chunk before "data".
     */
    fun readWav(file: File): Wav {
        RandomAccessFile(file, "r").use { f ->
            val riff = ByteArray(12)
            f.readFully(riff)
            require(String(riff, 0, 4, Charsets.US_ASCII) == "RIFF" && String(riff, 8, 4, Charsets.US_ASCII) == "WAVE") {
                "not a RIFF/WAVE file: ${file.name}"
            }
            var sampleRate = 0
            var channels = 1
            var bitsPerSample = 16
            val header = ByteArray(8)
            while (f.filePointer < f.length()) {
                f.readFully(header)
                val id = String(header, 0, 4, Charsets.US_ASCII)
                val size = java.nio.ByteBuffer.wrap(header, 4, 4).order(java.nio.ByteOrder.LITTLE_ENDIAN).int
                when (id) {
                    "fmt " -> {
                        val fmt = ByteArray(size)
                        f.readFully(fmt)
                        val buf = java.nio.ByteBuffer.wrap(fmt).order(java.nio.ByteOrder.LITTLE_ENDIAN)
                        buf.getShort() // audio format (1 = PCM)
                        channels = buf.getShort().toInt()
                        sampleRate = buf.getInt()
                        buf.getInt() // byte rate
                        buf.getShort() // block align
                        bitsPerSample = buf.getShort().toInt()
                    }
                    "data" -> {
                        require(bitsPerSample == 16 && channels == 1) { "only PCM16 mono is supported: ${file.name}" }
                        val bytes = ByteArray(size)
                        f.readFully(bytes)
                        val samples = ShortArray(size / 2)
                        java.nio.ByteBuffer.wrap(bytes).order(java.nio.ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(samples)
                        return Wav(samples, sampleRate)
                    }
                    else -> f.seek(f.filePointer + size + (size and 1)) // chunks are word-aligned
                }
            }
            throw java.io.IOException("no data chunk in ${file.name}")
        }
    }
}
