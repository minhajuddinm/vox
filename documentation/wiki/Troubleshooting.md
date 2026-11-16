# Troubleshooting

| Problem | Try |
|---|---|
| Nothing is typed on Windows | Check the tray icon is there and the key is set (Settings). Look in `%APPDATA%\Vox\vox.log` for `recording started` and the lines after it. |
| "Silence" or an empty result | The microphone level was too low: pick the right microphone in Settings and check Windows privacy settings allow desktop apps to use it. |
| The pill (recording indicator) is missing | Search `vox.log` for `overlay repair`: that line names the cause. |
| No bubble on Android | The accessibility service is off (Android turns it off after some updates or a crash): switch it on again, set battery use to Unrestricted, and see Settings, System, Install help. |
| A name or term comes out wrong | Add it to the Dictionary. Corrections you make right after a dictation are learned automatically (Learn from my corrections). |
| The text was changed too much | Use Light cleanup, or **Use raw** in History for that one. |
| An error from the server | The message in History or the log is the server's own; a 401 means the key, a 429 a rate limit. A failed recording is kept, so you can retry it. |

Every log line explained: [logs and diagnostics](https://github.com/minhajuddinm/vox/blob/main/documentation/11-logs-and-diagnostics.md). Known problems: [known issues](https://github.com/minhajuddinm/vox/blob/main/documentation/12-known-issues-and-roadmap.md). Report a bug: [issues](https://github.com/minhajuddinm/vox/issues/new/choose).
