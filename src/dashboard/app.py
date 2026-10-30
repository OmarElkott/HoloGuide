"""Launch from repository root: python -m streamlit run src/dashboard/app.py."""
import json
import sys
from pathlib import Path

# Streamlit executes this file directly; support a fresh clone without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from src.control.audit_log import AuditLogError
from src.control.workflow import InvalidTransition
from src.dashboard.session import DashboardSession


def main():
    st.set_page_config(page_title='HoloGuide', layout='wide')
    st.title('HoloGuide assembly dashboard')
    st.warning('Demo Mode — deterministic mock scene and inspector; not AI performance.')
    st.caption('This inspection path uses local JSON snapshots and makes no model calls. '
               'Network availability is not monitored. Streamlit telemetry is disabled in project config.')
    if 'assembly' not in st.session_state:
        st.session_state.assembly = DashboardSession()
    session = st.session_state.assembly
    workflow = session.workflow
    if st.button('New demo session'):
        st.session_state.assembly = DashboardSession()
        st.rerun()
    completed = (len(session.config.steps) if workflow.state.value == 'complete'
                 else workflow.current_step_id - 1)
    st.progress(completed / len(session.config.steps),
                text=f'{completed}/{len(session.config.steps)} steps verified')
    st.write(f'State: {workflow.state.value}')
    st.subheader(workflow.current_step.name)
    st.write(workflow.current_step.instruction)
    st.write('Mock visible parts:', sorted(session.environment.visible_parts))
    part = st.selectbox('Part', [p.name for p in session.config.parts])
    operator = st.text_input('Operator identifier')
    reason = st.text_input('Typed reason (required for override or removal)')
    try:
        if st.button('Place part', disabled=workflow.state.value in {'complete', 'hold'}):
            check = session.place(part)
            if check.allowed:
                st.rerun()
            st.error(check.reason)
        if st.button('Remove part', disabled=not (operator.strip() and reason.strip())):
            session.remove(part, reason, operator)
            st.rerun()
        if st.button('Inspect', disabled=workflow.state.value in {'complete', 'hold'}):
            session.inspect()
            st.rerun()
        if workflow.state.value in {'blocked', 'needs_review'}:
            approve = st.checkbox('Approve current step')
            if st.button('Submit override', disabled=not (operator.strip() and reason.strip())):
                session.override(approve, reason, operator)
                st.rerun()
        if workflow.state.value == 'hold':
            if st.button('Acknowledge resolved hold', disabled=not (operator.strip() and reason.strip())):
                workflow.clear_hold(operator, reason)
                st.rerun()
    except (ValueError, RuntimeError, OSError, InvalidTransition) as exc:
        st.error(str(exc))
    decision = workflow.last_decision
    if decision:
        st.subheader(f'Decision: {decision.status.value} (step {decision.step_id})')
        st.write(decision.message)
        st.write(f'Decided by: {decision.decided_by.value}')
        if decision.observation:
            st.metric('Observation confidence', f'{decision.observation.confidence:.0%}')
            st.write(decision.observation.rationale)
            st.json(decision.observation.model_dump(mode='json'))
    st.subheader('Session audit')
    try:
        events = session.audit.read_all()
        kinds = sorted({event['event_type'] for event in events})
        selected = st.multiselect('Event types (empty shows all)', kinds)
        st.json([e for e in events if not selected or e['event_type'] in selected])
        st.download_button('Download session audit',
                           '\n'.join(json.dumps(e) for e in events),
                           file_name='audit.jsonl', mime='application/x-ndjson')
    except AuditLogError as exc:
        st.error(str(exc))


if __name__ == '__main__':
    main()
