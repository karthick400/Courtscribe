# Court transcription

## Speaker configuration

Copy [`speakers.example.json`](./speakers.example.json), add names while the
transcription page is open, and pass the file to the STT script:

```powershell
.\test_prisma_stt.ps1 `
  -RecordSeconds 10 `
  -SpeakerConfigFile .\speakers.json `
  -OutputJson .\transcript.json
```

Each speaker needs a unique `id`, one of these roles, and an optional `name`:

- `Judge`
- `Complainant's Counsel`
- `Defendant's Counsel`
- `Witness`
- `Accused`

Add another object with the same role and a different ID for additional
judges, witnesses, or other participants.

The JSON output contains the configured `speakers` and a `segments` array.
Each segment starts with `speakerId`, `role`, and `name` set to `null` because
the REST STT response is one undifferentiated transcript. The live page can
assign those fields as each realtime segment is classified, then render the
line as `Judge: transcript`.

## FastAPI service

From the repository root, install the realtime client if needed and start the
API:

```powershell
.\court\Scripts\pip.exe install websockets
.\court\Scripts\uvicorn.exe main:app --reload
```

Set `gnaniAPI=<your-api-key>` in `.env`. The browser can connect to
`ws://localhost:8000/ws/transcribe` and send 16 kHz PCM binary frames. Gnani
transcript events are forwarded as JSON messages with `type: "transcript"` and
`text` fields. Send the text message `stop` to end the upstream stream.

To export a transcript, `POST` JSON to `/export-pdf` with either a `transcript`
string or a `segments` array. Optional `court_name`, `case_number`,
`hearing_date`, and `appearances` fields populate the court header and
appearances section. The response is a downloadable PDF. The frontend's
**Export PDF** button calls this endpoint for the selected session.

To save a recording directly on the server user's Desktop (or the user's home
directory when `Desktop` does not exist), `POST` JSON to
`/export-to-desktop`:

```json
{"recording_id": "session-id", "format": "pdf"}
```

`format` can be `pdf` or `txt`. The response contains the saved path:
`{"saved_to": "/path/to/Desktop/CourtScribe_CaseNo_Date.pdf"}`.

The standalone browser client is [`../frontend/index.html`](../frontend/index.html).
Serve the repository root over HTTP while the API is running, then open
`frontend/index.html`. It sends microphone audio as 16 kHz PCM frames over the
WebSocket and assigns each streamed line to the currently selected speaker.

New sessions are created with `POST /sessions` and are stored in the
`recordings/` directory (or the path in `RECORDINGS_DIR`). Each dated, named
folder contains `recording.wav`, `transcript.pdf`, and `metadata.json`. The UI
can select an existing session from `GET /recordings` or create a new named
folder. The Saved recordings panel lists those folders in a left-hand column;
selecting one shows its recording and transcript PDF, and **Continue transcript**
makes that folder the active session. After selecting a speaker label and
optional name, hold Space (or the record button) to send microphone audio;
release to pause and choose another speaker. Ending the session appends the
new audio and transcript segments to that folder and regenerates its PDF. Audio
and PDF files are available from the URLs returned by the recordings endpoint.

To correct a finalized transcript line, send `PATCH
/recordings/{recording_id}/segment/{segment_index}` with a JSON body containing
the replacement text:

```json
{"text": "Corrected transcript text"}
```

The segment is updated in `metadata.json` and `transcript.pdf` is regenerated.

### Pause and resume

Use **Pause** on the live transcription screen when a hearing is adjourned. The
browser stops the microphone and sends the normal `stop` control message to
`/ws/transcribe`, so the current audio and transcript segments are saved before
the WebSocket closes. The session ID, elapsed session duration, and pause time
are kept in browser local storage, while the session remains in the Saved
Recordings list.

When the stenographer selects that same saved session again, the frontend loads
all segments returned by `GET /recordings` from `metadata.json`, restores the
display and timer, and continues using the existing session ID. New audio is
appended to `recording.wav` and new segments are appended to `metadata.json`;
no new session folder is created and existing data is not overwritten.
