package com.minhaj.vox;

import android.content.Intent;
import android.os.Build;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import android.widget.Toast;

/**
 * The quick settings tile "Voice note": a tap starts a note, and a tap while a note is being recorded stops it.
 * Starting goes through TrampolineActivity like every other entry (see NoteEntry); the tile itself never touches the
 * microphone. The tile looks active while a note is being recorded, as far as it can tell when the panel is opened.
 */
public class NoteTileService extends TileService {

    @Override
    public void onStartListening() {
        super.onStartListening();
        Tile t = getQsTile();
        if (t == null) return;
        DictationService svc = DictationService.instance;
        t.setState(svc != null && svc.isNoteRecording() ? Tile.STATE_ACTIVE : Tile.STATE_INACTIVE);
        t.updateTile();
    }

    @Override
    public void onClick() {
        super.onClick();
        DictationService svc = DictationService.instance;
        if (svc != null && svc.getState() != DictationService.IDLE) {
            if (svc.isNoteRecording()) stopNote(svc);
            else Toast.makeText(this, "Vox is busy. Try again in a moment.", Toast.LENGTH_SHORT).show();
            return;
        }
        unlockAndRun(this::startNote);   // runs at once when the phone is not locked
    }

    /** Finishes the recording through the service's own stop action (the same one the notification button uses). */
    private void stopNote(DictationService svc) {
        try {
            startService(new Intent(this, DictationService.class).setAction(DictationService.ACTION_STOP_RECORDING));
        } catch (RuntimeException e) {   // the system refused a background service start: the service is in this process, call it
            svc.stopRecording();
        }
    }

    private void startNote() {
        if (Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(NoteEntry.startPendingIntent(this));
        } else {
            startActivityAndCollapse(NoteEntry.startIntent(this));
        }
    }
}
