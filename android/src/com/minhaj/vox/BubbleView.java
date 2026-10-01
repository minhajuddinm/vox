package com.minhaj.vox;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

/**
 * Round mic bubble. Grey when idle, red with a level ring while recording, amber spinner while processing.
 * The voice note bubble (note = true) is the same bubble with a blue idle colour and a note page instead of the mic.
 */
public class BubbleView extends View {
    private final boolean note;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint ring = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint glyph = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF r = new RectF();
    private int state = DictationService.IDLE;
    private float level;
    private float spin;
    private final ValueAnimator spinner;
    private final float d;

    public BubbleView(Context c) {
        this(c, false);
    }

    /** @param note true for the voice note bubble (its own colour and icon) */
    public BubbleView(Context c, boolean note) {
        super(c);
        this.note = note;
        d = getResources().getDisplayMetrics().density;
        ring.setStyle(Paint.Style.STROKE);
        ring.setStrokeWidth(3 * d);
        ring.setStrokeCap(Paint.Cap.ROUND);
        glyph.setColor(0xFFFFFFFF);
        glyph.setStrokeWidth(2.2f * d);
        glyph.setStrokeCap(Paint.Cap.ROUND);
        spinner = ValueAnimator.ofFloat(0, 360);
        spinner.setDuration(900);
        spinner.setRepeatCount(ValueAnimator.INFINITE);
        spinner.addUpdateListener(a -> { spin = (float) a.getAnimatedValue(); invalidate(); });
        setContentDescription(note ? "Vox voice note" : "Vox dictation");
    }

    public void setState(int s) {
        state = s;
        level = 0;
        if (s == DictationService.PROCESSING) { if (!spinner.isStarted()) spinner.start(); }
        else spinner.cancel();
        invalidate();
    }

    public void setLevel(float l) {
        level = l > level ? level * 0.35f + l * 0.65f : level * 0.8f + l * 0.2f;   // quick to rise, slow to fall
        invalidate();
    }

    @Override
    protected void onDraw(Canvas c) {
        float w = getWidth(), h = getHeight();
        float cx = w / 2f, cy = h / 2f;
        float base = Math.min(w, h) / 2f - 5 * d;

        int color;
        switch (state) {
            case DictationService.RECORDING: color = 0xFFE53935; break;
            case DictationService.PROCESSING: color = 0xFFF59E0B; break;
            default: color = note ? 0xE01F4E79 : 0xE0303338;
        }
        fill.setColor(color);
        float rad = state == DictationService.RECORDING ? base * (0.86f + 0.14f * level) : base * 0.86f;
        c.drawCircle(cx, cy, rad, fill);

        if (state == DictationService.RECORDING) {
            ring.setColor(0x66E53935);
            c.drawCircle(cx, cy, base * (0.92f + 0.08f * level) + 2 * d, ring);
        } else if (state == DictationService.PROCESSING) {
            ring.setColor(0xFFFFFFFF);
            r.set(cx - base * 0.95f, cy - base * 0.95f, cx + base * 0.95f, cy + base * 0.95f);
            c.drawArc(r, spin, 90, false, ring);
        }

        float s = base * 0.42f;
        if (note) {
            // Note glyph: a page with three lines of text
            glyph.setStyle(Paint.Style.STROKE);
            r.set(cx - s * 0.7f, cy - s * 0.95f, cx + s * 0.7f, cy + s * 0.95f);
            c.drawRoundRect(r, s * 0.18f, s * 0.18f, glyph);
            for (int i = -1; i <= 1; i++) {
                float y = cy + i * s * 0.42f;
                c.drawLine(cx - s * 0.32f, y, cx + s * 0.32f, y, glyph);
            }
            return;
        }
        // Mic glyph
        glyph.setStyle(Paint.Style.FILL);
        r.set(cx - s * 0.38f, cy - s * 0.95f, cx + s * 0.38f, cy + s * 0.25f);
        c.drawRoundRect(r, s * 0.38f, s * 0.38f, glyph);
        glyph.setStyle(Paint.Style.STROKE);
        r.set(cx - s * 0.68f, cy - s * 0.55f, cx + s * 0.68f, cy + s * 0.55f);
        c.drawArc(r, 20, 140, false, glyph);
        c.drawLine(cx, cy + s * 0.55f, cx, cy + s * 0.9f, glyph);
    }
}
