# CourtScribe

A realtime court transcription tool for stenographers. It streams microphone audio to a speech-to-text service, labels each line with the speaker's role, and saves every hearing as a session you can pause, resume, correct and export as a PDF.
## problem this tool solves

The Indian court has not changed for many years still suing the same typist and shorthand experts for the records of the trails and hearing, most of the magistrate court  not have the stenographer i saw this as the 
good gap to fill because the Gnani.ai support multiple Indian languages this tool have the following feature to help the stenographer

## Features

- Live transcription over WebSocket, using Gnani speech-to-text
- Speaker roles: Judge, Complainant's Counsel, Defendant's Counsel, Witness, Accused
- Hold Space to record, release to pause, and press 1-5 to switch speaker
- Saved sessions with audio, transcript PDF and metadata
- Pause and resume a hearing without losing the session or timer
- Click any line to correct it; the PDF is regenerated
- PDF and TXT export, including saving straight to the server's Desktop
- Light and dark theme, responsive layout

## Project structure

```
.
├── main.py                 FastAPI app
├── court/                  Python virtual environment
├── frontend/
│   └── index.html          Standalone browser client
├── recordings/             Saved sessions (created automatically)
├── test_prisma_stt.ps1     REST STT test script
├── speakers.example.json   Example speaker configuration
└── .env                    API key (not committed)
```

## Getting started

### 1. Install dependencies

From the repository root:

```powershell
.\court\Scripts\pip.exe install websockets
```

Install any other requirements for the project in the same environment.

### 2. Add your API key

Create a `.env` file in the repository root:

```
gnaniAPI=<your-api-key>
```

Optionally set `RECORDINGS_DIR` to store sessions somewhere other than `recordings/`.

### 3. Start the API

```powershell
.\court\Scripts\uvicorn.exe main:app --reload
```

The API runs on `http://localhost:8000`.

### 4. Open the client

Serve the repository root over HTTP while the API is running, then open `frontend/index.html`. For example:

```powershell
python -m http.server 5500
```

Then visit `http://localhost:5500/frontend/index.html`. The browser will ask for microphone permission the first time you record.

## Using the app

1. Create a named session, or pick an existing one from the list on the left.
2. Choose a speaker (keys 1-5) and optionally type a name.
3. Hold Space, or hold the record button, while that person speaks. Release to pause.
4. Switch speaker and repeat. Click any transcript line to correct it.
5. Press Adjourn when the hearing stops. Select the same session later to continue; new audio and lines are appended and nothing is overwritten.
6. Press Export PDF to download the transcript.

## Sessions and storage

New sessions are created with `POST /sessions`. Each one is a dated, named folder inside `recordings/` containing:

| File | Contents |
| --- | --- |
| `recording.wav` | All recorded audio, appended on each resume |
| `transcript.pdf` | Regenerated whenever the transcript changes |
| `metadata.json` | Session details and transcript segments |

Pausing keeps the session ID, elapsed time and pause time in the browser's local storage. Resuming loads the segments from `metadata.json` and continues in the same folder.

## API reference

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `WS` | `/ws/transcribe` | Send 16 kHz PCM binary frames; receive transcript events. Send the text message `stop` to end the stream. |
| `POST` | `/sessions` | Create a session |
| `GET` | `/recordings` | List saved sessions with audio and PDF URLs |
| `PATCH` | `/recordings/{recording_id}/segment/{segment_index}` | Correct a finalized line |
| `POST` | `/export-pdf` | Return a transcript PDF |
| `POST` | `/export-to-desktop` | Save a recording to the server's Desktop |

### WebSocket events

Transcript events arrive as JSON:

```json
{"type": "transcript", "text": "..."}
```

### Correct a line

```json
{"text": "Corrected transcript text"}
```

The segment is updated in `metadata.json` and `transcript.pdf` is regenerated.

### Export a PDF

`POST /export-pdf` with either a `transcript` string or a `segments` array. These optional fields fill in the court header and appearances section:

- `court_name`
- `case_number`
- `hearing_date`
- `appearances`

### Save to Desktop

```json
{"recording_id": "session-id", "format": "pdf"}
```

`format` can be `pdf` or `txt`. The response contains the saved path:

```json
{"saved_to": "/path/to/Desktop/CourtScribe_CaseNo_Date.pdf"}
```

If the server user has no `Desktop` folder, the file is saved to their home directory.

## Speaker configuration for the STT test script

Copy `speakers.example.json` to `speakers.json`, add names, and run:

```powershell
.\test_prisma_stt.ps1 `
  -RecordSeconds 10 `
  -SpeakerConfigFile .\speakers.json `
  -OutputJson .\transcript.json
```

Each speaker needs a unique `id`, one of the roles above, and an optional `name`. Add another object with the same role and a different `id` for additional judges, witnesses or other participants.

The JSON output contains the configured `speakers` and a `segments` array. Each segment starts with `speakerId`, `role` and `name` set to `null`, because the REST STT response is one undifferentiated transcript.

## Troubleshooting

- **No recordings appear:** check that the API is running on port 8000 and that the browser console shows no CORS errors.
- **Microphone does not start:** allow microphone access, and open the page over `http://localhost` or HTTPS.
- **Transcription is empty:** confirm `gnaniAPI` is set in `.env` and the key is valid.
- **Poor accuracy:** use a close microphone, reduce background noise, and correct lines by clicking them.

## Notes

Transcripts and recordings can contain sensitive case information. Keep `recordings/` and `.env` out of version control and share exports only with authorized people.
