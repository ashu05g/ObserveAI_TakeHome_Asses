"""Post-call email alerts via Resend, fired for low QA score or negative
sentiment. Sender defaults to Resend's sandbox so no domain verification
is needed; switch to a verified domain in production."""

import os

import httpx

RESEND_ENDPOINT = "https://api.resend.com/emails"
HIGH_SEVERITY_QA_THRESHOLD = 0.5


async def send_alert(
    *,
    caller_name: str | None,
    sentiment: str,
    qa_score: float | None,
    summary: str,
    trace_url: str | None = None,
) -> None:
    severity = (
        "HIGH"
        if qa_score is not None and qa_score < HIGH_SEVERITY_QA_THRESHOLD
        else "MEDIUM"
    )
    qa_display = f"{qa_score:.0%}" if qa_score is not None else "N/A"
    trace_link = (
        f'<p><a href="{trace_url}">View LLM trace in Langfuse</a></p>'
        if trace_url
        else ""
    )

    payload = {
        "from": os.environ["ALERT_EMAIL_FROM"],
        "to": [os.environ["ALERT_EMAIL_TO"]],
        "subject": f"[{severity}] Post-call alert — {caller_name or 'Unauthenticated caller'}",
        "html": (
            "<h2>Post-call alert</h2>"
            "<table style=\"font-family: -apple-system, sans-serif; border-collapse: collapse;\">"
            f"<tr><td><b>Caller</b></td><td>&nbsp;{caller_name or 'Unauthenticated'}</td></tr>"
            f"<tr><td><b>Sentiment</b></td><td>&nbsp;{sentiment}</td></tr>"
            f"<tr><td><b>QA Score</b></td><td>&nbsp;{qa_display}</td></tr>"
            f"<tr><td><b>Summary</b></td><td>&nbsp;{summary}</td></tr>"
            "</table>"
            f"{trace_link}"
        ),
    }
    headers = {
        "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(RESEND_ENDPOINT, json=payload, headers=headers)
        response.raise_for_status()
