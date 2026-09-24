package tech.timolehtonen.shot

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraConstrainedHighSpeedCaptureSession
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureRequest
import android.media.MediaRecorder
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.util.Range
import android.view.Surface
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * One continuous camera recording (spec 140/141/144/145), the video
 * equivalent of MicSession: opened once when listening starts, records
 * the whole session to [outputFile] until [close]d, at which point the
 * file is finalised and safely readable/seekable. Optionally also feeds
 * a live preview (spec 146) to [previewSurfaceTexture] - the same
 * capture session's second output, not a separate camera stream.
 *
 * Spec 144 found that reaching 60/120/240/480fps needs Camera2's
 * constrained-high-speed capture session, and that this session type
 * only accepts an opaque (`PRIVATE`-format) output surface - never the
 * `YUV_420_888` `ImageReader` specs 140/141 grabbed raw frames with
 * directly (confirmed on real hardware: that combination fails
 * `SurfaceUtils.checkHighSpeedSurfaceFormat` every time). A
 * `MediaRecorder`'s input surface *is* that opaque format, so this spec
 * records through `MediaRecorder` instead of grabbing frames itself -
 * [VideoDecoder] later decodes whichever window a shot needs back out
 * of the finished file. This is also why there's no more per-frame
 * `onFrame` callback or rolling in-memory buffer (spec 140's `LiveVideo`,
 * removed) - a shot's own window is extracted from the finished
 * recording after listening stops, not accumulated live.
 */
class CameraSession private constructor(
    private val cameraDevice: CameraDevice,
    private val captureSession: CameraCaptureSession,
    private val mediaRecorder: MediaRecorder,
    private val backgroundThread: HandlerThread,
    private val previewSurface: Surface?,
    val outputFile: File,
    val width: Int,
    val height: Int,
    val fps: Int,
    // How many degrees the sensor's own (always-landscape) output must be
    // rotated clockwise to appear upright with the phone held in its normal
    // portrait orientation (the only orientation this app supports - see
    // AndroidManifest's screenOrientation="portrait"). Recorded into the
    // file itself (recorder.setOrientationHint) and reused by MainActivity
    // to orient the live preview the same way (spec 146).
    val sensorOrientation: Int,
    // Seconds of MicSession's own clock (the same origin ShotRecord.shotT is
    // measured against) that had already elapsed by the time this recording's
    // own internal timeline started at 0 - what VideoDecoder subtracts from a
    // shot's audio-clock window to get the recording-relative window to decode.
    val offsetS: Double,
) {
    /** False when this session's fps needed the high-speed capture session -
     * see [open]'s own comment on why the live preview is dropped there.
     * MainActivity reads this to show a short caption instead of an
     * unexplained blank preview box. */
    val hasLivePreview: Boolean get() = previewSurface != null

    companion object {
        private fun normalRangesOf(chars: CameraCharacteristics): List<FpsRange> =
            chars.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
                ?.map { FpsRange(it.lower, it.upper) }.orEmpty()

        private fun highSpeedRangesOf(chars: CameraCharacteristics, map: android.hardware.camera2.params.StreamConfigurationMap): List<FpsRange> {
            val capabilities = chars.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES)?.toList().orEmpty()
            if (!capabilities.contains(CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_CONSTRAINED_HIGH_SPEED_VIDEO)) return emptyList()
            return try {
                map.highSpeedVideoFpsRanges?.map { FpsRange(it.lower, it.upper) }.orEmpty()
            } catch (e: Exception) {
                emptyList()
            }
        }

        /** The highest fps any range (normal or high-speed) reports for [id], or 0
         * if characteristics can't be read - used only to rank candidate cameras. */
        private fun maxFpsOf(manager: CameraManager, id: String): Int = try {
            val chars = manager.getCameraCharacteristics(id)
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            val normal = normalRangesOf(chars)
            val highSpeed = map?.let { highSpeedRangesOf(chars, it) }.orEmpty()
            (normal + highSpeed).maxOfOrNull { it.upper } ?: 0
        } catch (e: Exception) {
            0
        }

        /** A phone with more than one back lens doesn't guarantee `cameraIdList`
         * puts the most capable one first - every back-facing ID is scored by
         * the highest fps its own characteristics report (normal or
         * high-speed - spec 145 can use either, unlike spec 144's normal-only
         * scoring, since MediaRecorder's surface works with both session
         * types), and the best one wins. */
        private fun pickCameraId(manager: CameraManager): String? {
            val ids = try {
                manager.cameraIdList.toList()
            } catch (e: Exception) {
                Log.w(TAG, "CameraSession: could not list cameras", e)
                return null
            }
            if (ids.isEmpty()) return null
            val backIds = ids.filter {
                try {
                    manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) == CameraCharacteristics.LENS_FACING_BACK
                } catch (e: Exception) {
                    false
                }
            }
            val candidates = backIds.ifEmpty { ids }
            val best = candidates.maxByOrNull { maxFpsOf(manager, it) }
            Log.i(TAG, "CameraSession: back camera candidates " +
                candidates.joinToString { "$it(${maxFpsOf(manager, it)}fps)" } + " -> picked $best")
            return best
        }

        /** Which of [FpsOptions.CANDIDATES] the device's back (or first) camera can
         * reach, through either a normal or (spec 145) a high-speed session -
         * what the Settings screen offers, disabling the rest. Empty if there's
         * no camera at all. Cheap: only reads characteristics, never opens the
         * camera device itself. */
        fun supportedFps(context: Context): List<Int> = try {
            val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val cameraId = pickCameraId(manager) ?: return emptyList()
            val chars = manager.getCameraCharacteristics(cameraId)
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: return emptyList()
            FpsOptions.supportedFps(normalRangesOf(chars), highSpeedRangesOf(chars, map))
        } catch (e: Exception) {
            Log.w(TAG, "CameraSession.supportedFps failed", e)
            emptyList()
        }

        /** Same value this class's own [open] bakes into a session recording via
         * `setOrientationHint` - spec 153: [ShotDetailDialog][tech.timolehtonen.shot.ShotDetailDialog]
         * needs this too, to orient its own TextureView the same way
         * [applyPreviewTransform] already does for the live preview, since a
         * trimmed per-shot clip carries no rotation metadata of its own
         * (MediaMuxer.addTrack doesn't copy the track header's rotation
         * matrix - see VideoDecoder.trimToMp4's own doc comment) for a raw
         * MediaPlayer+TextureView to pick up even if it did. A fixed
         * hardware property of this phone's own back camera, cheap and safe
         * to query any time, no active session needed - defaults to 90 (the
         * overwhelmingly common case) if characteristics can't be read. */
        fun sensorOrientation(context: Context): Int = try {
            val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val cameraId = pickCameraId(manager) ?: return 90
            manager.getCameraCharacteristics(cameraId).get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90
        } catch (e: Exception) {
            Log.w(TAG, "CameraSession.sensorOrientation failed", e)
            90
        }

        /** A conservative bits-per-second guess scaled to the actual pixel
         * throughput (width x height x fps) - generous enough that this app's
         * small frame sizes (see [FpsOptions.targetSizePxFor]) stay comfortably
         * legible after H.264 compression, without the file ballooning at the
         * higher fps settings. */
        private fun bitRateFor(width: Int, height: Int, fps: Int): Int =
            (width * height * fps * 0.1).toInt().coerceIn(500_000, 8_000_000)

        /**
         * Opens the camera and starts recording continuously to [outputFile]
         * until [close] - [sessionStartNanos] should be `System.nanoTime()`
         * from right after `MicSession.open()` returned, so [offsetS] on the
         * result reflects how much of a head start the audio clock already had.
         * [previewSurfaceTexture] (spec 146), if given, is added as a second
         * output alongside the recording - if the capture session rejects
         * both surfaces together (some devices' high-speed modes are pickier
         * about multiple outputs), this transparently retries recording-only
         * rather than losing the recording over a live-preview nicety.
         */
        @SuppressLint("MissingPermission")
        fun open(
            context: Context,
            targetFps: Int,
            outputFile: File,
            sessionStartNanos: Long,
            previewSurfaceTexture: SurfaceTexture? = null,
        ): CameraSession? {
            val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
            val cameraId = pickCameraId(manager) ?: return null

            val chars = manager.getCameraCharacteristics(cameraId)
            val map = chars.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: return null
            val sensorOrientation = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90
            val normalRanges = normalRangesOf(chars)
            val highSpeedRanges = highSpeedRangesOf(chars, map)
            val useHighSpeed = FpsOptions.needsHighSpeedSession(targetFps, normalRanges, highSpeedRanges)
            val chosen = FpsOptions.bestRange(targetFps, normalRanges, highSpeedRanges) ?: FpsRange(30, 30)
            val bestRange = Range(chosen.lower, chosen.upper)

            val targetSizePx = FpsOptions.targetSizePxFor(targetFps)
            val sizes = if (useHighSpeed) {
                try {
                    map.getHighSpeedVideoSizesFor(bestRange)?.toList()
                } catch (e: Exception) {
                    null
                } ?: emptyList()
            } else {
                map.getOutputSizes(MediaRecorder::class.java)?.toList().orEmpty()
            }
            val size = sizes.minByOrNull { Math.abs(it.width - targetSizePx) } ?: return null

            val recorder = MediaRecorder().apply {
                setVideoSource(MediaRecorder.VideoSource.SURFACE)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setVideoEncoder(MediaRecorder.VideoEncoder.H264)
                setVideoSize(size.width, size.height)
                // The recorded fps metadata matches the capture rate, not a lower
                // one - this app wants many distinct steppable frames, not the
                // "slow motion" effect a lower declared rate would bake in.
                setVideoFrameRate(bestRange.upper)
                setVideoEncodingBitRate(bitRateFor(size.width, size.height, bestRange.upper))
                // Spec 146: without this, playback (and this app's own frame
                // extraction) showed the sensor's native landscape output
                // un-rotated - wrong by 90 degrees for a phone held upright.
                setOrientationHint(sensorOrientation)
                setOutputFile(outputFile.absolutePath)
            }
            val recorderSurface = try {
                recorder.prepare()
                recorder.surface
            } catch (e: Exception) {
                Log.w(TAG, "CameraSession: MediaRecorder.prepare failed", e)
                recorder.release()
                return null
            }

            // Spec 149: real-device recordings at 60/120/240fps (this app's
            // high-speed session range - see FpsOptions) came back with ~75%
            // of frames replaced by a solid green frame, but ONLY in the
            // recorded file, never in the live preview shown at the same
            // time. Adding both surfaces to the same high-speed burst below
            // is the documented way to combine a <=30fps preview with a
            // high-speed recording surface, but this phone's camera HAL
            // (OnePlus 10T / Snapdragon) mishandles it - confirmed on-device
            // (Sep 2026) by comparing recordings with and without the
            // preview surface attached: 0 green frames in ~2500 frames
            // across two 120/240fps clips once it's dropped, vs. hundreds
            // per clip with it attached. So: no live preview above 30fps -
            // MainActivity's CameraPreview stays blank for those sessions
            // (see its own comment), which is a real loss but a far smaller
            // one than a mostly-unusable recording.
            val previewSurface = if (useHighSpeed) null else previewSurfaceTexture?.let {
                it.setDefaultBufferSize(size.width, size.height)
                Surface(it)
            }

            val thread = HandlerThread("shot-camera").apply { start() }
            val handler = Handler(thread.looper)

            var resultDevice: CameraDevice? = null
            var resultSession: CameraCaptureSession? = null
            var started = false
            val latch = CountDownLatch(1)
            try {
                manager.openCamera(cameraId, object : CameraDevice.StateCallback() {
                    override fun onOpened(device: CameraDevice) {
                        resultDevice = device

                        fun configureAndStart(surfaces: List<Surface>, canRetryWithoutPreview: Boolean) {
                            val sessionCallback = object : CameraCaptureSession.StateCallback() {
                                override fun onConfigured(session: CameraCaptureSession) {
                                    try {
                                        val request = device.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                                            surfaces.forEach { addTarget(it) }
                                            set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, bestRange)
                                            set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
                                        }.build()
                                        if (useHighSpeed && session is CameraConstrainedHighSpeedCaptureSession) {
                                            val burst = session.createHighSpeedRequestList(request)
                                            session.setRepeatingBurst(burst, null, handler)
                                        } else {
                                            session.setRepeatingRequest(request, null, handler)
                                        }
                                        recorder.start()
                                        started = true
                                        resultSession = session
                                    } catch (e: Exception) {
                                        Log.w(TAG, "CameraSession: could not start recording", e)
                                    }
                                    latch.countDown()
                                }

                                override fun onConfigureFailed(session: CameraCaptureSession) {
                                    if (canRetryWithoutPreview) {
                                        Log.w(TAG, "CameraSession: session config failed with preview surface, retrying recording-only")
                                        configureAndStart(listOf(recorderSurface), canRetryWithoutPreview = false)
                                    } else {
                                        Log.w(TAG, "CameraSession: capture session configuration failed")
                                        latch.countDown()
                                    }
                                }
                            }
                            try {
                                if (useHighSpeed) {
                                    device.createConstrainedHighSpeedCaptureSession(surfaces, sessionCallback, handler)
                                } else {
                                    device.createCaptureSession(surfaces, sessionCallback, handler)
                                }
                            } catch (e: Exception) {
                                Log.w(TAG, "CameraSession: could not create capture session", e)
                                if (canRetryWithoutPreview) {
                                    configureAndStart(listOf(recorderSurface), canRetryWithoutPreview = false)
                                } else {
                                    latch.countDown()
                                }
                            }
                        }

                        val allSurfaces = if (previewSurface != null) listOf(recorderSurface, previewSurface) else listOf(recorderSurface)
                        configureAndStart(allSurfaces, canRetryWithoutPreview = previewSurface != null)
                    }

                    override fun onDisconnected(device: CameraDevice) {
                        device.close()
                        latch.countDown()
                    }

                    override fun onError(device: CameraDevice, error: Int) {
                        Log.w(TAG, "CameraSession: camera error $error")
                        device.close()
                        latch.countDown()
                    }
                }, handler)
            } catch (e: Exception) {
                Log.w(TAG, "CameraSession: openCamera failed", e)
                recorder.release()
                thread.quitSafely()
                return null
            }

            latch.await(5, TimeUnit.SECONDS)
            val offsetS = (System.nanoTime() - sessionStartNanos) / 1e9
            val device = resultDevice
            val session = resultSession
            if (device == null || session == null || !started) {
                try { recorder.release() } catch (e: Exception) {}
                try { device?.close() } catch (e: Exception) {}
                thread.quitSafely()
                return null
            }
            Log.i(TAG, "camera open: id=$cameraId ${size.width}x${size.height} requestedFps=$targetFps " +
                "fps=${bestRange.lower}-${bestRange.upper} highSpeed=$useHighSpeed sensorOrientation=$sensorOrientation " +
                "preview=${previewSurface != null} offsetS=$offsetS -> ${outputFile.name}")
            return CameraSession(device, session, recorder, thread, previewSurface, outputFile, size.width, size.height, bestRange.upper, sensorOrientation, offsetS)
        }
    }

    /** Stops recording and finalises [outputFile] so it's safely readable/
     * seekable afterwards - must complete before [VideoDecoder] touches it. */
    fun close() {
        try {
            captureSession.stopRepeating()
        } catch (e: Exception) {
        }
        try {
            mediaRecorder.stop()
        } catch (e: Exception) {
            Log.w(TAG, "CameraSession: MediaRecorder.stop failed (very short recording?)", e)
        }
        try {
            mediaRecorder.release()
        } catch (e: Exception) {
        }
        try {
            captureSession.close()
        } catch (e: Exception) {
        }
        try {
            cameraDevice.close()
        } catch (e: Exception) {
        }
        try {
            previewSurface?.release()
        } catch (e: Exception) {
        }
        backgroundThread.quitSafely()
        Log.i(TAG, "camera closed")
    }
}
