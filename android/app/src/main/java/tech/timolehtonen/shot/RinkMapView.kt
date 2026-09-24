package tech.timolehtonen.shot

import android.graphics.Paint
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntSize

/**
 * The rink map for picking the shooting place (spec 138): an IIHF rink,
 * the shooter's end on the left, the end boards - what every distance is
 * measured to, since the phone always stands at the shooter - highlighted
 * on the right. Tapping a named place's line (or close to it) picks that
 * place; tapping anywhere else on the ice sets the distance to the end
 * boards at that spot. Only ever reports a distance in metres - the
 * selected-distance state lives in the caller (Rink.kt has the geometry).
 */
private val ICE = Color(0xFFEEF6FB)
private val BOARDS = Color(0xFF5B6770)
private val RED = Color(0xFFC0392B)
private val BLUE = Color(0xFF2F6FB3)
private val PLACE_LINE = Color(0xFF1E8449)
private val SELECTED = Color(0xFFE67E22)
private val TARGET = Color(0xFFF5B041)

@Composable
fun RinkMap(
    selectedDistanceM: Double?,
    enabled: Boolean,
    onPick: (Double) -> Unit,
    modifier: Modifier = Modifier,
) {
    var canvasSize by remember { mutableStateOf(IntSize.Zero) }
    val aspect = (Rink.RINK_LENGTH_M / Rink.RINK_WIDTH_M).toFloat()
    Canvas(
        modifier = modifier
            .fillMaxWidth()
            .aspectRatio(aspect)
            .onSizeChanged { canvasSize = it }
            .pointerInput(enabled) {
                if (!enabled) return@pointerInput
                detectTapGestures { offset ->
                    val scale = canvasSize.width / Rink.RINK_LENGTH_M.toFloat()
                    if (scale <= 0f) return@detectTapGestures
                    Rink.distanceForClick((offset.x / scale).toDouble())?.let(onPick)
                }
            },
    ) {
        drawRink(selectedDistanceM)
    }
}

private fun DrawScope.drawRink(selectedDistanceM: Double?) {
    val scale = size.width / Rink.RINK_LENGTH_M.toFloat()
    fun px(xM: Double) = (xM * scale).toFloat()
    fun py(yM: Double) = (yM * scale).toFloat()
    val cornerPx = px(Rink.CORNER_RADIUS_M)
    val boardsWidth = px(0.12)

    // boards
    drawRoundRect(ICE, cornerRadius = CornerRadius(cornerPx, cornerPx))
    drawRoundRect(BOARDS, cornerRadius = CornerRadius(cornerPx, cornerPx), style = Stroke(width = boardsWidth))

    val midY = Rink.RINK_WIDTH_M / 2
    val top = py(0.0)
    val bottom = py(Rink.RINK_WIDTH_M)

    // centre and blue lines
    drawLine(RED, Offset(px(Rink.CENTER_LINE_X_M), top), Offset(px(Rink.CENTER_LINE_X_M), bottom), strokeWidth = px(0.3))
    for (x in listOf(Rink.BLUE_LINE_NEAR_X_M, Rink.BLUE_LINE_FAR_X_M)) {
        drawLine(BLUE, Offset(px(x), top), Offset(px(x), bottom), strokeWidth = px(0.4))
    }
    // near goal line (decorative) and the far one (the goal itself, also decorative - the target is the boards behind it)
    for (x in listOf(Rink.GOAL_LINE_OFFSET_M, Rink.FAR_GOAL_LINE_X_M)) {
        drawLine(RED, Offset(px(x), py(1.0)), Offset(px(x), py(Rink.RINK_WIDTH_M - 1.0)), strokeWidth = px(0.1))
    }
    // goals
    val goalHalf = Rink.GOAL_WIDTH_M / 2
    fun goalBox(lineX: Double, towardFar: Boolean) {
        val x0 = if (towardFar) lineX else lineX - Rink.GOAL_DEPTH_M
        val x1 = if (towardFar) lineX + Rink.GOAL_DEPTH_M else lineX
        drawRect(
            Color(0xFFF8D7DA), Offset(px(x0), py(midY - goalHalf)),
            Size(px(x1) - px(x0), py(midY + goalHalf) - py(midY - goalHalf)),
        )
        drawRect(
            RED, Offset(px(x0), py(midY - goalHalf)),
            Size(px(x1) - px(x0), py(midY + goalHalf) - py(midY - goalHalf)), style = Stroke(width = px(0.08)),
        )
    }
    goalBox(Rink.GOAL_LINE_OFFSET_M, towardFar = false)
    goalBox(Rink.FAR_GOAL_LINE_X_M, towardFar = true)

    // the end boards: what every distance is measured to
    val edge = Rink.CORNER_RADIUS_M
    drawLine(
        TARGET, Offset(px(Rink.RINK_LENGTH_M), py(edge)), Offset(px(Rink.RINK_LENGTH_M), py(Rink.RINK_WIDTH_M - edge)),
        strokeWidth = px(0.5), cap = StrokeCap.Round,
    )

    // centre circle/dot, end-zone face-off circles/spots
    drawCircleAt(Rink.CENTER_LINE_X_M, midY, Rink.FACEOFF_CIRCLE_RADIUS_M, BLUE, px = ::px, py = ::py)
    drawDotAt(Rink.CENTER_LINE_X_M, midY, 1.0, BLUE, px = ::px, py = ::py)
    for (x in Rink.FACEOFF_SPOT_X_M) {
        for (y in Rink.FACEOFF_SPOT_Y_M) {
            drawCircleAt(x, y, Rink.FACEOFF_CIRCLE_RADIUS_M, RED, px = ::px, py = ::py)
            drawDotAt(x, y, 0.6, RED, px = ::px, py = ::py)
        }
    }

    // named places: a dashed line + a distance chip
    val dash = PathEffect.dashPathEffect(floatArrayOf(px(0.8), px(0.6)))
    val chipHeight = px(3.2)
    val textPaint = Paint().apply {
        color = android.graphics.Color.WHITE
        textAlign = Paint.Align.CENTER
        textSize = px(2.6)
        isAntiAlias = true
    }
    for (place in Rink.PLACES) {
        val x = px(place.xM)
        drawLine(PLACE_LINE, Offset(x, top), Offset(x, bottom), strokeWidth = px(0.25), pathEffect = dash)
        val label = Rink.formatDistance(place.distanceM)
        val chipHalf = textPaint.measureText(label) / 2f + px(0.5)
        drawRect(PLACE_LINE, Offset(x - chipHalf, 0f), Size(chipHalf * 2, chipHeight))
        drawContext.canvas.nativeCanvas.drawText(label, x, chipHeight * 0.72f, textPaint)
    }

    // the chosen distance: the shooter's spot and an arrow to the end boards
    if (selectedDistanceM != null) {
        val xM = Rink.xForDistance(selectedDistanceM)
        if (xM in 0.0..Rink.RINK_LENGTH_M) {
            val x = px(xM)
            val y = py(midY)
            drawLine(SELECTED, Offset(x, y), Offset(px(Rink.RINK_LENGTH_M), y), strokeWidth = px(0.35), cap = StrokeCap.Round)
            drawCircle(SELECTED, radius = px(1.1), center = Offset(x, y))
            drawCircle(Color.White, radius = px(1.1), center = Offset(x, y), style = Stroke(width = px(0.25)))
        }
    }
}

private fun DrawScope.drawCircleAt(xM: Double, yM: Double, rM: Double, color: Color, px: (Double) -> Float, py: (Double) -> Float) {
    drawCircle(color, radius = px(rM), center = Offset(px(xM), py(yM)), style = Stroke(width = px(0.12)))
}

private fun DrawScope.drawDotAt(xM: Double, yM: Double, rM: Double, color: Color, px: (Double) -> Float, py: (Double) -> Float) {
    drawCircle(color, radius = px(rM), center = Offset(px(xM), py(yM)))
}
