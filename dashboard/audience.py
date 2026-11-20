"""A quick audience overview of the existing recorded experiment."""

import os

import streamlit as st

from dashboard import presentation as ui


def show_workspace():
    st.session_state.view_mode = "Run & inspect"


def render(api, run_label, local_time):
    runs = api("/runs")
    st.html(ui.hero())
    if not runs:
        st.info("Start your first experiment to see confirmed outcomes here.")
        st.button("Open live workspace", type="primary", on_click=show_workspace)
        return
    by_id = {row["run_id"]: row for row in runs}
    if st.session_state.get("active_run") not in by_id:
        preferred = next(
            (
                row
                for row in runs
                if row["mode"] == "protected"
                and row["status"] == "completed"
                and row.get("capability_profile")
            ),
            runs[0],
        )
        st.session_state.active_run = preferred["run_id"]
    with st.expander("Choose the run shown below"):
        chosen = st.selectbox(
            "Recorded run",
            list(by_id),
            format_func=lambda value: run_label(by_id[value]),
            key="active_run",
        )
    run = by_id[chosen]
    cache = {}

    def get(run_id, suffix):
        key = (run_id, suffix)
        if key not in cache:
            cache[key] = api(f"/runs/{run_id}{suffix}")
        return cache[key]

    writes = get(chosen, "/tool-writes")
    timeline = get(chosen, "/timeline")
    capabilities = get(chosen, "/capabilities")
    usage = get(chosen, "/usage")
    coverage = get(chosen, "/coverage") if run.get("capability_profile") else None
    approved = {
        value.strip()
        for value in os.getenv("APPROVED_CRM_DESTINATIONS", "approved-crm").split(",")
        if value.strip()
    }
    running = run["status"] == "running"
    tone = "warn" if running else "danger" if run["status"] == "failed" else "neutral"
    st.html(
        f'<div class="am-source">{ui.badge("Running now" if running else "Recorded result", tone)}<span>{ui.e(local_time(run["started_at"]))} · {ui.e(run["driver"].capitalize())} · {ui.e(run["scenario"].capitalize())}</span></div>'
    )
    if run["pending_executions"] or run["unknown_executions"]:
        st.warning(
            "An execution outcome is unresolved. Inspect actual tool state before retrying."
        )
    if run["status"] == "failed":
        st.error(
            "This run failed. The confirmed effects and capability statuses below remain available for inspection."
        )
    roles = {
        card
        for role, cards in (coverage or {}).get("open_model_roles", {}).items()
        if role in {"agent", "reviewer"}
        for card in cards
    }
    for col, label, count in zip(
        st.columns(4),
        [
            "Unsafe attempts blocked",
            "Confirmed downstream writes",
            "Otari features demonstrated",
            "Open-weight model roles",
        ],
        [
            run["blocked"],
            len(writes),
            sum((coverage or {}).get("features", {}).values()),
            len(roles),
        ],
    ):
        col.metric(label, count)
    st.html(
        ui.section(
            "THE DIFFERENCE YOU CAN SEE",
            "Same task. Different boundaries.",
            "Compare the actual destinations and exported fields. Every prospect is fictitious.",
        )
    )
    matching = [
        row
        for row in runs
        if row["status"] == "completed"
        and row["scenario"] == run["scenario"]
        and row["driver"] == run["driver"]
        and bool(row.get("capability_profile")) == bool(run.get("capability_profile"))
    ]
    for col, mode in zip(st.columns(2), ("unprotected_test", "protected")):
        with col:
            item = (
                run
                if run["mode"] == mode
                else next((row for row in matching if row["mode"] == mode), None)
            )
            if item:
                records = get(item["run_id"], "/tool-writes")
                st.html(ui.outcome_card(item, records, approved))
                st.caption(
                    f"{'Selected run' if item['run_id'] == chosen else 'Latest matching run'} · {item['run_id'][:8]} · {local_time(item['started_at'])}"
                )
            else:
                st.info(
                    "Complete the matching protected or UNPROTECTED TEST run to see both outcomes."
                )
    st.html(
        ui.section(
            "ACTION BY ACTION",
            "Where the boundary intervened",
            "Authorization and execution are separate. A confirmed write is matched to actual tool state.",
        )
    )
    if timeline:
        st.html(ui.timeline(timeline, writes))
    else:
        st.info(
            "No business actions have been recorded yet. Research can finish before the first action is proposed."
        )
    if run.get("capability_profile"):
        st.html(
            ui.section(
                "POWERED BY OTARI",
                "Six capabilities. One connected workflow.",
                "Verified means demonstrated by this run's returned results, rather than enabled in a dashboard.",
            )
        )
        st.html(ui.features(capabilities))
        st.html(ui.section("TWO DISTINCT MODEL ROLES", "One acts. Another reviews."))
        for col, role, label, responsibility in zip(
            st.columns(2),
            ("agent", "reviewer"),
            ("THE SALES AGENT", "THE INDEPENDENT REVIEWER"),
            (
                "Selects business tools and responds to policy decisions.",
                "Checks redacted results. Its advice cannot authorize an action.",
            ),
        ):
            with col:
                row = next(
                    (
                        row
                        for row in reversed(usage)
                        if row.get("role", "agent") == role
                    ),
                    None,
                )
                model = (
                    (row.get("resolved_model") or row.get("model"))
                    if row
                    else "Awaiting model evidence"
                )
                model = {
                    "mistral-small-2603": "Mistral Small 4",
                    "gpt-oss-120b": "GPT-OSS-120B",
                }.get(model, model)
                verified = bool((coverage or {}).get("open_model_roles", {}).get(role))
                st.html(
                    f'<article class="am-feature"><div><span class="am-eyebrow">{label}</span>{ui.badge("Open-weight verified", "good") if verified else ui.badge("Awaiting evidence")}</div><h3>{ui.e(model)}</h3><p>{responsibility}</p></article>'
                )
        review = next(
            (row.get("review") for row in reversed(capabilities) if row.get("review")),
            None,
        )
        if review:
            st.html(
                f'<div class="am-note"><b>Second-model assessment · advisory</b><br>{ui.e(review["rationale_summary"])}</div>'
            )
        if coverage:
            with st.expander("Judge's rubric and verified model sources"):
                st.caption(coverage["note"])
                st.write(
                    f"Technical evidence self-audit: **{coverage['technical_evidence_points']} / {coverage['technical_maximum']}**"
                )
                for role, cards in coverage["open_model_roles"].items():
                    for card in cards:
                        st.markdown(
                            f"**{role.capitalize()}**: [{card.rsplit('/', 1)[-1]}]({card})"
                        )
                st.dataframe(
                    [
                        {"Otari feature": name, "Demonstrated": value}
                        for name, value in coverage["features"].items()
                    ],
                    hide_index=True,
                    width="stretch",
                )
        st.caption(
            "Native Otari guardrails and per-user budgets are not demonstrated. The gateway enforces separate organization rules."
        )
    else:
        st.info(
            "This run uses local enforcement only. Choose Otari agent in the live workspace to demonstrate native capabilities and model roles."
        )
    st.button("Open live workspace", type="primary", on_click=show_workspace)
    st.html(
        '<footer class="am-footer"><span>Agent Monitor · Built with Otari</span><span>Synthetic records. Local business effects. Returned evidence.</span></footer>'
    )
