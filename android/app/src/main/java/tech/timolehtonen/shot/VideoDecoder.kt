package tech.timolehtonen.shot

import android.content.Context
import android.graphics.Bitmap
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.media.MediaMuxer
import android.os.Build
import android.util.Log
import java.io.File
import java.nio.ByteBuffer

/**
 * Decodes a shot's own window back out of the full-session video
 * [CameraSession] recorded (spec 145), writing each frame as a JPEG the
 * same way [ShotFiles.readFrames] has always expected - the on-disk
 * shape a saved shot's video takes hasn't changed since spec 140, only
 * how it's produced: decoding a real encoded video, not raw frames
 * grabbed live. Runs after the whole session's recording has stopped (a
 * `MediaRecorder`-written MP4 isn't safely seekable until finalised), so
 * unlike specs 140-144, a shot's video is not ready the instant the shot
 * happens - it's ready once "Lopeta kuuntelu" finishes processing every
 * shot from the session.
 *
 * Uses [MediaMetadataRetriever] rather than driving `MediaCodec`/
 * `ImageReader` directly - an earlier version of this class did exactly
 * that (decode to a YUV_420_888 `ImageReader` Surface, convert to NV21
 * by hand), and it crashed outright on real hardware: `JNI DETECTED
 * ERROR IN APPLICATION: non-zero capacity for nullptr pointer` from
 * inside the plane-reading code, on this app's own OnePlus 10T. Camera-
 * captured `Image`s support that pattern fine (proven across specs
 * 140-144's own extensive testing) but this phone's hardware video
 * *decoder* doesn't reliably hand back CPU-readable YUV planes the same
 * way when rendering to an `ImageReader` Surface - a real device/driver
 * limitation, not a bug in the plane-reading arithmetic itself.
 * `MediaMetadataRetriever` hits the same underlying decoder but through
 * the platform's own high-level, widely-used frame-extraction path,
 * which handles that hardware's quirks internally.
 */
object VideoDecoder {
    /**
     * Decodes [videoFile] between [fromS]..[toS] (already converted to the
     * video file's own internal timeline - see [CameraSession.offsetS]) at
     * [fps] (the session's actual requested capture rate - [MediaMetadata-
     * Retriever] doesn't reliably report a decoded frame's own exact
     * timestamp back, so frame times in the sidecar are the assumed-uniform
     * `index / fps`, not measured) into [ShotFiles.framesDir]'s JPEGs plus
     * the frame-times sidecar. Returns the frame count written (0 if the
     * file is missing, the window doesn't overlap it, or decoding fails -
     * same "this shot just has no video" graceful fallback every other
     * failure mode here has always had).
     */
    fun extractFrames(context: Context, videoFile: File, stem: String, fromS: Double, toS: Double, fps: Int): Int {
        if (!videoFile.isFile || fps <= 0) return 0
        val clampedFromS = fromS.coerceAtLeast(0.0)
        if (clampedFromS >= toS) return 0
        val startIndex = (clampedFromS * fps).toInt()
        val numFrames = ((toS - clampedFromS) * fps).toInt().coerceAtLeast(1)

        val framesDir = ShotFiles.framesDir(context, stem)
        val retriever = MediaMetadataRetriever()
        var writtenCount = 0
        try {
            retriever.setDataSource(videoFile.absolutePath)
            val bitmaps = batchExtract(retriever, startIndex, numFrames)
                ?: perFrameExtract(retriever, clampedFromS, numFrames, fps)

            val frameTimesS = mutableListOf<Double>()
            for (bitmap in bitmaps) {
                File(framesDir, "frame_%04d.jpg".format(writtenCount)).outputStream().use { out ->
                    bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
                }
                frameTimesS.add(writtenCount / fps.toDouble())
                bitmap.recycle()
                writtenCount++
            }
            if (writtenCount > 0) {
                ShotFiles.writeFrameTimesSidecar(context, stem, frameTimesS)
            }
        } catch (e: Exception) {
            Log.w(TAG, "VideoDecoder: decode failed for ${videoFile.name}", e)
        } finally {
            try {
                retriever.release()
            } catch (e: Exception) {
            }
        }
        if (writtenCount == 0) framesDir.delete()
        return writtenCount
    }

    /** The fast path (API 28+): one call decodes [numFrames] consecutive
     * frames starting at [startIndex], reusing decoder state internally -
     * far quicker than [perFrameExtract]'s one-seek-per-frame loop for a
     * high-fps window's worth of frames. Null (not empty) signals "try the
     * fallback instead", both below API 28 and if the device's decoder
     * rejects this call for some reason. */
    private fun batchExtract(retriever: MediaMetadataRetriever, startIndex: Int, numFrames: Int): List<Bitmap>? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return null
        return try {
            retriever.getFramesAtIndex(startIndex, numFrames).takeIf { it.isNotEmpty() }
        } catch (e: Exception) {
            Log.w(TAG, "VideoDecoder: getFramesAtIndex failed, falling back to per-frame extraction", e)
            null
        }
    }

    /** minSdk 26/27 fallback (and a safety net if [batchExtract] fails
     * anywhere) - one [MediaMetadataRetriever.getFrameAtTime] call per
     * frame, each independently seeking - slower, but the same public API
     * every Android version since well before this app's minSdk supports. */
    private fun perFrameExtract(retriever: MediaMetadataRetriever, fromS: Double, numFrames: Int, fps: Int): List<Bitmap> {
        val stepUs = 1_000_000L / fps
        val startUs = (fromS * 1_000_000).toLong()
        return (0 until numFrames).mapNotNull { i ->
            try {
                retriever.getFrameAtTime(startUs + i * stepUs, MediaMetadataRetriever.OPTION_CLOSEST)
            } catch (e: Exception) {
                null
            }
        }
    }

    /**
     * Copies [videoFile]'s compressed video samples between [fromS]..[toS]
     * into a new, small, standalone MP4 at [destFile] - for spec 147's
     * cloud upload, which needs a real playable video file, not a JPEG
     * sequence. Uses `MediaExtractor`+`MediaMuxer` to *remux* the existing
     * H.264 bitstream directly (no decode/re-encode - the opposite of
     * [extractFrames], which must decode to get pixels for JPEGs): far
     * cheaper, and avoids this device's own decode-to-`ImageReader` crash
     * (see this class's doc comment) entirely, since no `Image`/pixel data
     * is ever touched here. Returns true if any samples were written (a
     * window with none - e.g. entirely before the recording started - is
     * reported as failure the same way [extractFrames] reports it as 0).
     */
    fun trimToMp4(videoFile: File, destFile: File, fromS: Double, toS: Double): Boolean {
        if (!videoFile.isFile) return false
        val clampedFromS = fromS.coerceAtLeast(0.0)
        if (clampedFromS >= toS) return false

        val extractor = MediaExtractor()
        var muxer: MediaMuxer? = null
        var wroteAny = false
        try {
            extractor.setDataSource(videoFile.absolutePath)
            val trackIndex = (0 until extractor.trackCount).firstOrNull {
                extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("video/") == true
            } ?: return false
            val format = extractor.getTrackFormat(trackIndex)
            extractor.selectTrack(trackIndex)

            muxer = MediaMuxer(destFile.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            val outTrack = muxer.addTrack(format)
            muxer.start()

            val maxSampleSize = try {
                format.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE)
            } catch (e: Exception) {
                0
            }.let { if (it > 0) it else 2_000_000 }
            val buffer = ByteBuffer.allocate(maxSampleSize)
            val bufferInfo = MediaCodec.BufferInfo()
            val fromUs = (clampedFromS * 1_000_000).toLong()
            val toUs = (toS * 1_000_000).toLong()
            extractor.seekTo(fromUs, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)

            // Spec 153: rebased to fromUs, NOT to wherever SEEK_TO_PREVIOUS_SYNC
            // actually landed. This encoder's keyframe interval is coarser than
            // a shot's own PREROLL_S/POSTROLL_S window (confirmed on a real
            // clip: a ~2.0s requested window came out 2.31s long), so the
            // previous version - which rebased to the *seek point's* own
            // timestamp - left an extra, un-requested wedge of video at the
            // start that the audio snippet (cut at an exact sample boundary,
            // no such keyframe constraint) never had. That silently pushed
            // every real frame later relative to t=0 than the matching audio
            // moment, which is exactly the "video is late" / "audio arrives
            // before the spectrogram peak" symptom this fixes - the video
            // and audio snippets are meant to share the same t=0 instant.
            //
            // Samples between the keyframe and fromUs still have to be
            // MUXED, not dropped - the P-frames right after fromUs decode
            // relative to them, and dropping them would corrupt exactly
            // those first few frames (the same kind of visible corruption
            // spec 149 hit for an unrelated reason). They're just pinned to
            // (approximately) t=0 instead of their real, pre-fromUs time,
            // since a player has nothing useful to show for "before the
            // clip starts" anyway. MediaMuxer requires strictly increasing
            // timestamps per track, hence lastWrittenUs rather than a flat
            // coerceAtLeast(0) for all of them.
            var lastWrittenUs = -1L
            while (true) {
                val sampleTimeUs = extractor.sampleTime
                if (sampleTimeUs < 0 || sampleTimeUs > toUs) break
                val size = extractor.readSampleData(buffer, 0)
                if (size < 0) break
                val rebasedUs = (sampleTimeUs - fromUs).coerceAtLeast(0L)
                val outputUs = maxOf(rebasedUs, lastWrittenUs + 1)
                lastWrittenUs = outputUs
                bufferInfo.offset = 0
                bufferInfo.size = size
                bufferInfo.presentationTimeUs = outputUs
                bufferInfo.flags = extractor.sampleFlags
                muxer.writeSampleData(outTrack, buffer, bufferInfo)
                wroteAny = true
                extractor.advance()
            }
        } catch (e: Exception) {
            Log.w(TAG, "VideoDecoder: trimToMp4 failed for ${videoFile.name}", e)
            wroteAny = false
        } finally {
            try {
                muxer?.stop()
            } catch (e: Exception) {
            }
            try {
                muxer?.release()
            } catch (e: Exception) {
            }
            extractor.release()
        }
        if (!wroteAny) destFile.delete()
        return wroteAny
    }

    /** A trimmed shot clip's own frame rate, real frame count and duration -
     * spec 153: what [ShotDetailDialog][tech.timolehtonen.shot.ShotDetailDialog]
     * needs to seek a real video-only MediaPlayer frame-accurately (the
     * ±1-frame buttons) and show the same "ruutu N/count   t s" readout the
     * old JPEG-per-frame approach did, without decoding a single pixel -
     * frameCount comes from walking the sample table (cheap: no decode,
     * same cost class as [trimToMp4]'s own remux loop), not from
     * duration*fps, which drifted by one on a real clip when tried
     * server-side (see server/android_compose.py's own probe_frame_count
     * docstring - the same class of bug, fixed the same way here). */
    // width/height (spec 153): the raw, un-rotated frame size MediaExtractor
    // reports - what ShotDetailDialog's own applyPreviewTransform call needs,
    // the same way CameraSession.width/height already feed that function for
    // the live preview.
    data class VideoMeta(val fps: Int, val frameCount: Int, val durationS: Double, val width: Int, val height: Int)

    fun probeVideoMeta(file: File): VideoMeta? {
        if (!file.isFile) return null
        val extractor = MediaExtractor()
        return try {
            extractor.setDataSource(file.absolutePath)
            val trackIndex = (0 until extractor.trackCount).firstOrNull {
                extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("video/") == true
            } ?: return null
            val format = extractor.getTrackFormat(trackIndex)
            val fps = try {
                format.getInteger(MediaFormat.KEY_FRAME_RATE)
            } catch (e: Exception) {
                30
            }.let { if (it > 0) it else 30 }
            val width = try { format.getInteger(MediaFormat.KEY_WIDTH) } catch (e: Exception) { 0 }
            val height = try { format.getInteger(MediaFormat.KEY_HEIGHT) } catch (e: Exception) { 0 }
            extractor.selectTrack(trackIndex)
            var frameCount = 0
            var lastSampleTimeUs = 0L
            while (true) {
                val t = extractor.sampleTime
                if (t < 0) break
                lastSampleTimeUs = t
                frameCount++
                if (!extractor.advance()) break
            }
            if (frameCount == 0) return null
            // The true clip length is one frame's worth past the last sample's
            // own timestamp, not just lastSampleTimeUs itself.
            val durationS = (lastSampleTimeUs / 1_000_000.0) + (1.0 / fps)
            VideoMeta(fps, frameCount, durationS, width, height)
        } catch (e: Exception) {
            Log.w(TAG, "VideoDecoder: probeVideoMeta failed for ${file.name}", e)
            null
        } finally {
            extractor.release()
        }
    }
}
