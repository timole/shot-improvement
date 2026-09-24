package tech.timolehtonen.shot

import android.graphics.Bitmap
import android.graphics.Paint
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp

/** A vertical line + label drawn on top of a spectrogram at one instant. */
data class SpectroMarker(val timeS: Double, val label: String, val color: Color)

private const val DISPLAY_MAX_HZ = 12000.0 // above Claps.SHOT_BAND_HIGH_HZ - keeps the interesting range readable

/**
 * Renders a spectrogram's columns with [markers] on top. The same
 * composable serves both the live view (columns/columnTimesS/nowS keep
 * changing, so the window - and every marker in it - visibly scrolls
 * left as [nowS] advances) and a saved shot's static one (called once
 * with the whole clip, nowS = its duration, nothing further changes).
 */
@Composable
fun SpectrogramCanvas(
    columns: List<DoubleArray>,
    columnTimesS: List<Double>,
    nowS: Double,
    sampleRate: Int,
    visibleSeconds: Double,
    markers: List<SpectroMarker>,
    modifier: Modifier = Modifier,
) {
    val bitmap = remember(columns) { buildBitmap(columns, sampleRate) }
    Canvas(modifier = modifier.fillMaxWidth().height(140.dp).background(Color.Black)) {
        val windowStartS = columnTimesS.firstOrNull() ?: (nowS - visibleSeconds)
        val windowEndS = nowS.coerceAtLeast(windowStartS + 0.001)
        bitmap?.let { drawImage(it.asImageBitmap(), dstSize = IntSize(size.width.toInt(), size.height.toInt())) }

        val textPaint = Paint().apply { textSize = 28f; isAntiAlias = true; color = android.graphics.Color.WHITE; setShadowLayer(4f, 0f, 0f, android.graphics.Color.BLACK) }
        for (m in markers) {
            if (m.timeS < windowStartS || m.timeS > windowEndS) continue
            val x = ((m.timeS - windowStartS) / (windowEndS - windowStartS) * size.width).toFloat()
            // A shadow line under the marker's own colour: on this colourmap's own warm
            // (red/orange/yellow) peaks, a thin single-colour line can disappear entirely.
            drawLine(Color.Black, Offset(x, 0f), Offset(x, size.height), strokeWidth = 7f)
            drawLine(m.color, Offset(x, 0f), Offset(x, size.height), strokeWidth = 4f)
            val textX = x.coerceIn(4f, size.width - 4f)
            drawContext.canvas.nativeCanvas.drawText(m.label, textX, 30f, textPaint)
        }
    }
}

/** columns.size wide, capped to [DISPLAY_MAX_HZ] tall - high frequencies at the top. */
private fun buildBitmap(columns: List<DoubleArray>, sampleRate: Int): Bitmap? {
    if (columns.isEmpty()) return null
    val totalBins = columns[0].size
    val height = ((DISPLAY_MAX_HZ / (sampleRate / 2.0)) * (totalBins - 1)).toInt().coerceIn(1, totalBins - 1)
    val width = columns.size
    val pixels = IntArray(width * height)
    for (x in columns.indices) {
        val col = columns[x]
        for (yFromTop in 0 until height) {
            val bin = (height - yFromTop).coerceIn(0, totalBins - 1)
            pixels[yFromTop * width + x] = Spectrogram.colorForDb(col[bin])
        }
    }
    return Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
}
