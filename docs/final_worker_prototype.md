# Final Transcription Worker Prototype

Use this Streamlit page to validate the faster-whisper background worker lifecycle before integrating it into `app.py`.

## How to Run

```bash
source venv/bin/activate
STREAMLIT_BROWSER_GATHER_USAGE_STATS=false streamlit run prototypes/final_worker_demo.py
```

## Manual Test Checklist

1. **Startup logging**
   - Note the PID displayed in the metrics banner.
   - Confirm the first log entry shows “Worker started (PID=…)”.
2. **Submit jobs**
   - Enter sample text and click “Submit job”.
   - Observe the “Queued jobs” metric increment.
3. **Drain results**
   - Click “Poll results”; the job should move to “Completed jobs” with an entry in *Completed Results*.
   - The `transcript` field will be the uppercase payload (stand-in for faster-whisper output).
4. **Rerun resilience**
   - Modify a widget (e.g., submit another job) and ensure the worker PID remains unchanged—no duplicate processes should spawn.
   - Queued/completed counts must reflect all jobs across reruns.
5. **Shutdown**
   - Click “Shutdown worker” and verify:
     - A log entry reads “Worker shutdown (PID=…)”.
     - The metrics reset after Streamlit reruns (no lingering PID).
    - Confirm the PID is gone:
      ```bash
      ps -p <PID> || python - <<'PY'
      import psutil
      print(psutil.pid_exists(<PID>))
      PY
      ```
      Both commands should show the process no longer exists.

### Additional Tips

- Inspect `st.session_state["final_worker_jobs"]` if the JSON widget is empty—state persists there even after UI re-renders.
- Completed jobs populate `st.session_state["final_worker_last_result"]` with `job_id`, `transcript`, `confidence`, `latency_ms`, and an ISO8601 `completed_at` timestamp.
- In the primary app, this state drives the **High-Accuracy Transcript** panel and sidebar telemetry, so verifying it here mirrors the operator experience.
- CRM payloads record both the streaming partial (`transcription_stream_partial`) and the final transcript plus metadata (`transcription_final_*` fields). Ops logs capture queue depth/last success/error for the worker.
- To reset the prototype completely, click “Shutdown worker” and then rerun the app (`Command+R` in the Streamlit UI or restart the `streamlit run` command).

If all checks pass, the prototype satisfies the success criteria outlined in `docs/faster_whisper_checklist.md`.
